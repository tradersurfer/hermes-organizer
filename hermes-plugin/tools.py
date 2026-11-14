"""Tool handlers for the Hermes Organizer plugin (v0.1.0).

Thin wrappers around the core package (``hermes_organizer/``) in the
same repo — no classification, naming, or approval logic is
re-implemented here. Handlers follow the CEO Pack shape: they take
``args`` (dict or JSON string), always return a JSON string, and never
raise.

Rules the handlers enforce (the core enforces them too — this is the
second lock, not the only one):
  - Dry-run is the default. ``organizer_execute`` and
    ``organizer_rollback`` only write when the caller passes
    ``execute: true`` — and the agent must only do that after the
    user approved the exact items, in this conversation.
  - ``organizer_execute`` refuses without an approvals file whose
    per-item hashes match the proposals file (see
    ``hermes_organizer/proposals.py``). A re-scan changes the
    proposals, the hashes change, and the old approval is refused.
  - Nothing is ever deleted. The core has no delete call anywhere.
  - No credentials ever pass through these handlers. Vision keys and
    tokens are resolved by the core from the host environment or the
    host's registered connections; a credential-like value in ``args``
    is refused outright and never echoed.

The core package lives beside this plugin directory (same repo
checkout). When the plugin is installed from the public repo, the
import shim below looks for ``hermes_organizer/`` next to this
directory first, then in this directory itself, so the layer works
whether the public repo keeps the ``hermes-plugin/`` layout or
flattens the plugin to the repo root.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

# ---------------------------------------------------------------- core import

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_core_root() -> str | None:
    """First directory holding a hermes_organizer/ package."""
    candidates = [
        os.path.dirname(_PLUGIN_DIR),  # repo root, hermes-plugin/ layout
        _PLUGIN_DIR,  # flattened layout: core inside the plugin dir
    ]
    for root in candidates:
        if os.path.isdir(os.path.join(root, "hermes_organizer")):
            return root
    return None


_CORE_ERROR: str | None = None
_CORE = None
_CORE_ROOT = _find_core_root()
if _CORE_ROOT is not None and _CORE_ROOT not in sys.path:
    sys.path.insert(0, _CORE_ROOT)
try:
    from hermes_organizer import execution as _execution  # noqa: E402
    from hermes_organizer.indexer import DriveIndexer  # noqa: E402
    from hermes_organizer.ocr import VisionBridge  # noqa: E402
    from hermes_organizer.proposals import (  # noqa: E402
        load_approvals,
        load_proposals,
        write_proposals,
    )
    from hermes_organizer.report import (  # noqa: E402
        build_image_lookup,
        render_html,
    )
    from hermes_organizer.schemas import OrganizerResult  # noqa: E402
    from hermes_organizer.watcher import (  # noqa: E402
        scan_drive_sample,
        scan_local_folder,
    )

    _CORE = True
except ImportError as exc:  # pragma: no cover - import failure path
    _CORE_ERROR = (
        "hermes_organizer core package not found beside the plugin "
        f"({exc}). The plugin ships with the core in the same repo; "
        "install the whole repository, not this directory alone."
    )

# ---------------------------------------------------------------- config

CONFIG = {
    # Manifest defaults. __init__.py overwrites these from the host's
    # install-time config when it is available; call args override
    # per call. confidence_threshold is deliberately NOT here — it is
    # locked at 0.60 in hermes_organizer/taxonomy.py and never read
    # from config.
    "vision_provider": "cloudflare",
    "approval_required": True,
}

# ---------------------------------------------------------------- helpers


def _ok(payload: dict) -> str:
    payload = dict(payload)
    payload.setdefault("status", "success")
    return json.dumps(payload)


def _err(message: str) -> str:
    return json.dumps({"status": "error", "error": message})


_CREDENTIAL_KEY = re.compile(r"api[_-]?key|token|secret|password|passwd|auth", re.I)


def _prep(args, scan: bool = True):
    """Coerce args to a dict and refuse credential-like content.

    Returns (args_dict, error_json). ``scan=False`` is unused here —
    every Organizer handler refuses credential-like input.
    """
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None, _err("Arguments string is not valid JSON.")
    if not isinstance(args, dict):
        return None, _err("Arguments must be a JSON object.")
    if scan:
        for key, value in args.items():
            if _CREDENTIAL_KEY.search(str(key)):
                return None, _err(
                    f"Refused: argument {key!r} looks like a credential. "
                    "Credentials come from the host, never from tool "
                    "arguments — pass paths and IDs only."
                )
            if isinstance(value, str) and _CREDENTIAL_KEY.search(value):
                return None, _err(
                    "Refused: an argument value looks like a credential. "
                    "Pass paths and IDs only."
                )
    return args, None


def _need_core():
    """Return an error JSON string when the core is unavailable."""
    if _CORE is None:
        return _err(_CORE_ERROR or "Core package unavailable.")
    return None


def _vision_bridge(provider: str):
    """Build a VisionBridge, or return (None, note) when unusable.

    A bridge with no credentials is not an error — the scan runs
    OCR-only and says so. A bad provider name is an error.
    """
    try:
        bridge = VisionBridge(provider=provider)
    except ValueError as exc:
        return None, f"invalid vision provider: {exc}"
    if not bridge.available():
        return None, (
            f"no {bridge.provider} credentials found — OCR only "
            f"(set {('CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID' if bridge.provider == 'cloudflare' else 'GEMINI_API_KEY')} "
            "or connect the provider to enable vision)"
        )
    return bridge, None


def _results_summary(results) -> dict:
    classified = sum(
        1 for r in results
        if r.visual_classification.subcategory != "Requires_Human_Review"
    )
    return {
        "files_scanned": len(results),
        "classified": classified,
        "flagged_for_review": len(results) - classified,
        "vision_used_count": sum(1 for r in results if r.vision_used),
    }


def _scan_payload(results, source: str, vision_mode: str) -> dict:
    return {
        "source": source,
        "action": "propose-only (plugin v0.1.0 read-only)",
        "vision_mode": vision_mode,
        "vision_used_count": sum(1 for r in results if r.vision_used),
        "files_scanned": len(results),
        "results": [r.model_dump() for r in results],
    }


# ---------------------------------------------------------------- tools


def organizer_scan(args, **kwargs) -> str:
    """Scan a local folder (or a Drive folder) and propose renames.

    Read-only: downloads Drive working copies locally at most, and
    never renames, moves, or deletes anything. Vision is a second
    opinion only; without credentials the scan runs OCR-only.
    """
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    folder = parsed.get("folder")
    drive_folder_id = parsed.get("drive_folder_id")
    if not folder and not drive_folder_id:
        return _err("Pass 'folder' (local path) or 'drive_folder_id'.")
    vision_mode = parsed.get("vision", "auto")
    if vision_mode not in ("off", "auto", "always"):
        return _err("vision must be 'off', 'auto', or 'always'.")
    provider = parsed.get("vision_provider", CONFIG["vision_provider"])
    try:
        vision = None
        vision_note = "Vision: off (OCR only — nothing is sent anywhere)"
        if vision_mode != "off":
            vision, vision_note = _vision_bridge(provider)
            if vision is None and vision_note.startswith("invalid"):
                return _err(vision_note)
            vision_note = f"Vision: {vision_mode} ({provider})" if vision else (
                f"Vision: {vision_mode} ({vision_note})"
            )
        audit_log = parsed.get("audit_log")
        if drive_folder_id:
            download_dir = parsed.get("download_dir") or tempfile.mkdtemp(
                prefix="hermes-organizer-drive-"
            )
            limit = int(parsed.get("limit", 10))
            results = scan_drive_sample(
                DriveIndexer(), download_dir, limit, audit_log,
                vision=vision, vision_mode=vision_mode,
                folder_id=drive_folder_id,
            )
            source = (f"Google Drive folder {drive_folder_id} "
                      f"(read-only, limit {limit})")
        else:
            results = scan_local_folder(
                folder, audit_log, vision=vision, vision_mode=vision_mode
            )
            source = f"local folder {folder}"
        payload = _scan_payload(results, source, vision_mode)
        report_path = parsed.get("report_path")
        if report_path:
            os.makedirs(os.path.dirname(os.path.abspath(report_path)) or ".",
                        exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        proposals_path = parsed.get("proposals_path")
        if proposals_path:
            write_proposals(results, proposals_path, source=source)
        out = {"source": source, "vision_note": vision_note,
               "report_path": report_path, "proposals_path": proposals_path,
               "results": payload["results"]}
        out.update(_results_summary(results))
        return _ok(out)
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_scan failed: {exc}")


def organizer_propose(args, **kwargs) -> str:
    """Turn a scan report into a proposals file (approved: false).

    Reads the scan JSON ``organizer_scan`` wrote and writes one
    proposal entry per file, each with a proposal_hash binding the
    file ID to its proposed filename and folder. Nothing is approved
    here — that is organizer_approve's job, after the user's yes.
    """
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    scan_report = parsed.get("scan_report")
    proposals_path = parsed.get("proposals_path")
    if not scan_report or not proposals_path:
        return _err("Pass 'scan_report' and 'proposals_path'.")
    try:
        with open(scan_report, encoding="utf-8") as fh:
            payload = json.load(fh)
        results = [OrganizerResult.model_validate(r)
                   for r in payload.get("results", [])]
        doc = write_proposals(results, proposals_path,
                              source=payload.get("source", ""))
        return _ok({
            "proposals_path": proposals_path,
            "entries": len(doc["proposals"]),
            "source": doc.get("source", ""),
        })
    except (OSError, ValueError) as exc:
        return _err(f"organizer_propose failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_propose failed: {exc}")


def organizer_approve(args, **kwargs) -> str:
    """Record the user's approval for exact proposal entries.

    The agent must ONLY call this after the user approved the exact
    items, in this conversation, with their exact proposed names and
    destination folder. This tool records that approval — it never
    invents one. Entries not listed stay approved: false. Human-review
    entries (no proposal) and unknown file IDs are refused, and no
    approvals file is written when anything is refused.
    """
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    proposals_path = parsed.get("proposals_path")
    approvals_path = parsed.get("approvals_path")
    if not proposals_path or not approvals_path:
        return _err("Pass 'proposals_path' and 'approvals_path'.")
    approve_all = bool(parsed.get("approve_all", False))
    approved_ids = parsed.get("approved_file_ids") or []
    if not isinstance(approved_ids, list):
        return _err("'approved_file_ids' must be a list of file IDs.")
    if not approve_all and not approved_ids:
        return _err(
            "Nothing to approve: pass 'approved_file_ids' or "
            "'approve_all': true."
        )
    try:
        proposals_doc = load_proposals(proposals_path)
        by_id = {e["file_id"]: e for e in proposals_doc["proposals"]}
        wanted = set(by_id) if approve_all else set(approved_ids)
        for file_id in wanted:
            entry = by_id.get(file_id)
            if entry is None:
                return _err(
                    f"Refused: approval for unknown file ID {file_id!r} — "
                    "it is not in the proposals file. No approvals file "
                    "was written."
                )
            if entry.get("proposed_filename") is None:
                return _err(
                    f"Refused: {entry.get('original_name', file_id)!r} has "
                    "no proposal (human review) and can never be "
                    "approved. No approvals file was written."
                )
        approvals_doc = copy.deepcopy(proposals_doc)
        for entry in approvals_doc["proposals"]:
            entry["approved"] = entry["file_id"] in wanted
        approvals_doc["approval_source"] = {
            "recorded_by": "organizer_approve (user approval in chat)",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "note": parsed.get("note", ""),
            "items": [
                f"{by_id[f]['original_name']} -> "
                f"{by_id[f]['proposed_filename']}"
                for f in sorted(wanted)
            ],
        }
        os.makedirs(os.path.dirname(os.path.abspath(approvals_path)) or ".",
                    exist_ok=True)
        with open(approvals_path, "w", encoding="utf-8") as fh:
            json.dump(approvals_doc, fh, indent=2)
        return _ok({
            "approvals_path": approvals_path,
            "approved": sorted(wanted),
            "approved_count": len(wanted),
            "total_entries": len(by_id),
        })
    except (OSError, ValueError) as exc:
        return _err(f"organizer_approve failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_approve failed: {exc}")


def organizer_execute(args, **kwargs) -> str:
    """Dry-run the approved plan by default; execute only on execute: true.

    Reads the proposals + approvals files and refuses unless every
    actionable proposal is approved with a matching proposal_hash and
    its target name is free in the target folder. The agent must only
    pass execute: true after the user approved the exact plan. The
    core's WRITE_ENABLED gate is set for this call only and restored
    afterwards. Nothing is ever deleted; every change is written to
    the execution log before moving to the next file.
    """
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    proposals_path = parsed.get("proposals_path")
    approvals_path = parsed.get("approvals_path")
    if not proposals_path or not approvals_path:
        return _err("Pass 'proposals_path' and 'approvals_path'.")
    log_path = parsed.get("log_path", "execution-log.jsonl")
    root_folder_id = parsed.get("root_folder_id", "root")
    do_execute = bool(parsed.get("execute", False))
    try:
        proposals_doc = load_proposals(proposals_path)
        approvals_doc = load_approvals(approvals_path)
        drive = _execution.DriveWriter()
        if not do_execute:
            planned = _execution.plan(
                proposals_doc, approvals_doc, drive,
                root_folder_id=root_folder_id,
            )
            return _ok({
                "mode": "dry-run",
                "refused": planned["refused"],
                "steps": planned["steps"],
                "skipped": planned["skipped"],
                "folders_to_create": planned["folders_to_create"],
                "note": "Dry-run only — nothing was renamed, moved, or "
                        "created. Pass execute: true to perform this plan.",
            })
        _execution.WRITE_ENABLED = True
        try:
            report = _execution.execute(
                proposals_doc, approvals_doc, drive, log_path,
                execute=True, root_folder_id=root_folder_id,
            )
        finally:
            _execution.WRITE_ENABLED = False
        out = {
            "mode": "executed",
            "executed": report["executed"],
            "skipped": report["skipped"],
            "folders_created": report["folders_created"],
            "verification": report["verification"],
            "log_path": report["log_path"],
        }
        bad = [v for v in report["verification"] if not v["ok"]]
        if bad:
            out["status"] = "error"
            out["error"] = (
                f"{len(bad)} file(s) did not verify after execution — "
                "see verification. Use organizer_rollback with the log "
                "to undo."
            )
        return _ok(out)
    except _execution.ExecutionError as exc:
        return _err(f"organizer_execute refused: {exc}")
    except (OSError, ValueError) as exc:
        return _err(f"organizer_execute failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_execute failed: {exc}")


def organizer_verify(args, **kwargs) -> str:
    """Re-fetch every file in an execution log and confirm name + parents.

    Read-only. Success is verified against live Drive, never assumed.
    """
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    log_path = parsed.get("log_path")
    if not log_path:
        return _err("Pass 'log_path'.")
    try:
        drive = _execution.DriveWriter()
        verification = _execution.verify_log(log_path, drive)
        ok_count = sum(1 for v in verification if v["ok"])
        return _ok({
            "log_path": log_path,
            "verified_ok": ok_count,
            "total": len(verification),
            "verification": verification,
        })
    except (OSError, ValueError) as exc:
        return _err(f"organizer_verify failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_verify failed: {exc}")


def organizer_rollback(args, **kwargs) -> str:
    """Restore original names + parents from an execution log.

    Dry-run by default (returns what would be restored, zero writes).
    Pass execute: true to perform the restores. Never deletes
    anything; a file missing from Drive is reported and skipped.
    """
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    log_path = parsed.get("log_path")
    if not log_path:
        return _err("Pass 'log_path'.")
    do_execute = bool(parsed.get("execute", False))
    try:
        drive = _execution.DriveWriter()
        _execution.WRITE_ENABLED = do_execute
        try:
            report = _execution.rollback(
                log_path, drive, execute=do_execute
            )
        finally:
            _execution.WRITE_ENABLED = False
        out = {
            "mode": "executed" if do_execute else "dry-run",
            "restored": report["restored"],
            "missing": report["missing"],
            "refused": report["refused"],
            "log_path": report["log_path"],
        }
        if "would_restore" in report:
            out["would_restore"] = report["would_restore"]
        return _ok(out)
    except _execution.ExecutionError as exc:
        return _err(f"organizer_rollback refused: {exc}")
    except (OSError, ValueError) as exc:
        return _err(f"organizer_rollback failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_rollback failed: {exc}")


def organizer_report(args, **kwargs) -> str:
    """Render scan JSON(s) as one self-contained HTML report page."""
    parsed, err = _prep(args)
    if err:
        return err
    core_err = _need_core()
    if core_err:
        return core_err
    scan_reports = parsed.get("scan_report") or parsed.get("scan_reports")
    out_path = parsed.get("out_path")
    if not scan_reports or not out_path:
        return _err("Pass 'scan_report' (path or list) and 'out_path'.")
    if isinstance(scan_reports, str):
        scan_reports = [scan_reports]
    image_dirs = parsed.get("image_dir") or parsed.get("image_dirs") or []
    if isinstance(image_dirs, str):
        image_dirs = [image_dirs]
    try:
        reports = []
        for path in scan_reports:
            with open(path, encoding="utf-8") as fh:
                reports.append(json.load(fh))
        lookup = build_image_lookup(image_dirs)
        page = render_html(reports, lookup)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".",
                    exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(page)
        total = sum(len(r.get("results") or []) for r in reports)
        return _ok({"out_path": out_path, "files": total})
    except (OSError, ValueError) as exc:
        return _err(f"organizer_report failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - handlers never raise
        return _err(f"organizer_report failed: {exc}")
