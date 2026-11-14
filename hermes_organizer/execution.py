"""Execution layer for Hermes Organizer v0.7.0.

Turns approved proposals into real Drive renames + moves — and only
then. The scan path (run_scan.py) never imports this module for
writing; run_execute.py is the only entry point that does.

Flow:
  scan -> proposals file -> user approves (copy + flip approved) ->
  execute (dry-run by default) -> verify -> rollback if needed.

Gates — execution refuses unless ALL hold:
  1. The caller passes ``execute=True`` AND this module's
     ``WRITE_ENABLED`` is True. run_execute.py sets WRITE_ENABLED
     only when the user passes ``--execute`` on the command line.
     Nothing in this codebase ever flips it on its own.
  2. A valid approvals file exists; every actionable proposal is
     approved with a ``proposal_hash`` matching the proposals file
     (see proposals.py — a re-scan makes old approvals stale).
  3. The target name does not already exist in the target folder
     (checked by listing the folder before each rename; the namer's
     dedup suffix only handles collisions *within* one scan).
  4. Never delete. There is no delete call in this module, anywhere
     in the codebase, or in any test helper. Rollback restores names
     and parents; it never removes a file.

Drive writes go through the same hatch_gws_cli channel as the
indexer (``drive files update`` for rename+move in one call,
``drive files create`` for missing target folders). The connector
doc documents these as normal approval-gated operations, and the
CLI exposes them — but the connected token's OAuth scope cannot be
confirmed read-only (no scope field in status/about, and token
introspection is out of bounds). A 403 from Drive is therefore
treated as a clean refusal: WriteScopeError telling the user to
reconnect Drive with write scope. The first live execute is the
scope test; nothing here assumes it will succeed.

Every executed change is appended to a JSONL execution log
(write-through, one record per change, before moving to the next
file) so a crash mid-batch still leaves a rollback trail. After
execution, each file is re-fetched by ID and its name + parents
compared against the plan — success is verified, never assumed.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .indexer import DRIVE_CLI, RateLimiter
from .proposals import match_approvals, proposal_hash

WRITE_ENABLED = False  # runtime gate: only run_execute.py --execute sets this True

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


class ExecutionError(RuntimeError):
    """Base class for execution failures."""


class GateRefused(ExecutionError):
    """A safety gate refused the run. Nothing was written."""


# Backwards-compatible name for the v0.1 stub's exception.
ExecutionDisabled = GateRefused


class WriteScopeError(ExecutionError):
    """Drive refused the write (HTTP 403): the connected Drive token
    has no write scope. Reconnect Google Drive with write access."""


class DriveTimeoutError(ExecutionError):
    """A Drive write call timed out (120s). The request may still
    have applied server-side — the caller must re-fetch the file and
    compare live state before deciding anything. Never assume a
    timeout means 'not applied'."""


class DriveCliError(RuntimeError):
    """hatch_gws_cli failed; carries the raw stderr for inspection."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def _escape_query_value(value: str) -> str:
    """Escape a value for a Drive search query string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class DriveWriter:
    """Drive write channel via hatch_gws_cli.

    Same subprocess shape as DriveIndexer._run (rate-limited, JSON
    out). ``write_calls`` counts files update/create calls so tests
    and dry-run audits can prove zero writes happened.
    """

    rate_per_sec: float = 1.0
    write_calls: int = 0
    _limiter: RateLimiter = field(default_factory=RateLimiter, init=False)

    def __post_init__(self) -> None:
        self._limiter = RateLimiter(self.rate_per_sec)

    def _run(self, args: list[str]) -> dict:
        self._limiter.wait()
        try:
            proc = subprocess.run(
                [DRIVE_CLI, "drive"] + args,
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            # Seen live 2026-11-13: files update applied server-side
            # while the CLI call itself hung past 120s. A timeout is
            # UNKNOWN state, not failure — callers must verify.
            raise DriveTimeoutError(
                f"Drive CLI timed out after 120s: {' '.join(args[:4])} "
                "(the change may still have applied — verify live state)"
            ) from exc
        if proc.returncode != 0:
            raise DriveCliError(
                f"Drive CLI failed: {proc.stderr.strip()[:300]}",
                stderr=proc.stderr,
            )
        return json.loads(proc.stdout)

    def get_file(self, file_id: str) -> dict:
        """Read-only metadata fetch (id, name, mimeType, parents)."""
        data = self._run(["files", "get", "--params", json.dumps({
            "fileId": file_id,
            "fields": "id,name,mimeType,parents",
        })])
        return data

    def find_child(self, parent_id: str, name: str,
                   mime_type: str | None = None) -> dict | None:
        """First non-trashed child of ``parent_id`` with this name."""
        q = (
            f"'{parent_id}' in parents and "
            f"name = '{_escape_query_value(name)}' and trashed=false"
        )
        if mime_type:
            q += f" and mimeType = '{mime_type}'"
        data = self._run(["files", "list", "--params", json.dumps({
            "q": q,
            "pageSize": 10,
            "fields": "files(id,name,mimeType,parents)",
        })])
        files = data.get("files", [])
        return files[0] if files else None

    def list_children(self, folder_id: str) -> list[dict]:
        """Non-trashed direct children (id, name, parents)."""
        data = self._run(["files", "list", "--params", json.dumps({
            "q": f"'{folder_id}' in parents and trashed=false",
            "pageSize": 100,
            "fields": "files(id,name,mimeType,parents)",
        })])
        return data.get("files", [])

    def create_folder(self, name: str, parent_id: str) -> str:
        """Create a folder; returns its Drive ID. Counts as a write."""
        try:
            data = self._run(["files", "create", "--params", json.dumps({
                "ignoreDefaultVisibility": True,
            }), "--json", json.dumps({
                "name": name,
                "mimeType": DRIVE_FOLDER_MIME,
                "parents": [parent_id],
            })])
        except DriveCliError as exc:
            raise _translate_write_error(exc) from exc
        self.write_calls += 1
        return data["id"]

    def update_file(self, file_id: str, name: str | None = None,
                    add_parents: list[str] | None = None,
                    remove_parents: list[str] | None = None) -> dict:
        """Rename and/or move in one files.update call. Counts as a write."""
        params: dict = {"fileId": file_id}
        if add_parents:
            params["addParents"] = ",".join(add_parents)
        if remove_parents:
            params["removeParents"] = ",".join(remove_parents)
        args: list[str] = ["files", "update", "--params", json.dumps(params)]
        if name is not None:
            args += ["--json", json.dumps({"name": name})]
        try:
            data = self._run(args)
        except DriveCliError as exc:
            raise _translate_write_error(exc) from exc
        self.write_calls += 1
        return data


def _translate_write_error(exc: DriveCliError) -> ExecutionError:
    """A 403 on a write means the token has no write scope — say so
    plainly instead of surfacing a raw API error."""
    if "403" in exc.stderr or "insufficientPermissions" in exc.stderr:
        return WriteScopeError(
            "Drive refused the write (HTTP 403): the connected Google "
            "Drive token has no write scope. Reconnect Google Drive "
            "with write access, then retry. Nothing was changed by "
            "this call."
        )
    return ExecutionError(f"Drive write failed: {exc}")


def _split_folder_path(path: str) -> list[str]:
    return [seg for seg in path.strip("/").split("/") if seg]


def resolve_folder(drive: DriveWriter, path: str, root_id: str = "root",
                   create: bool = True) -> tuple[str | None, list[str], list[str]]:
    """Resolve ``/Work/Product Photos/2026/`` to a Drive folder ID.

    Walks each segment from ``root_id``; a missing segment is created
    when ``create`` is True. Returns (folder_id, created,
    would_create): folder_id is None when create is False and a
    segment is missing, and would_create lists the full paths that
    would have been created (dry-run reporting). ``created`` is a
    list of {"folder_id", "name", "parent_id"} dicts, so callers can
    log folder creation (rollback needs the IDs; folders are never
    deleted, but the trail must exist).
    """
    parent_id = root_id
    created: list[dict] = []
    would_create: list[str] = []
    prefix = "/"
    for segment in _split_folder_path(path):
        child = drive.find_child(parent_id, segment, DRIVE_FOLDER_MIME)
        if child is not None:
            parent_id = child["id"]
            prefix = f"{prefix}{segment}/"
            continue
        if not create:
            would_create.append(f"{prefix}{segment}/")
            return None, created, would_create
        folder_id = drive.create_folder(segment, parent_id)
        created.append({"folder_id": folder_id, "name": segment,
                        "parent_id": parent_id})
        parent_id = folder_id
        prefix = f"{prefix}{segment}/"
    return parent_id, created, []


def append_log_record(log_path: str, record: dict) -> None:
    """Write-through: one JSON line per executed change."""
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def load_log(log_path: str) -> list[dict]:
    """Read an execution log (JSONL) back, newest last."""
    records: list[dict] = []
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _check_collision(drive: DriveWriter, folder_id: str, name: str,
                     own_id: str) -> str | None:
    """Name already used in the target folder by a *different* file?

    Returns the colliding file's ID, or None when the name is free
    (or already belongs to this file itself).
    """
    for child in drive.list_children(folder_id):
        if child["name"] == name and child["id"] != own_id:
            return child["id"]
    return None


def plan(proposals_doc: dict, approvals_doc: dict, drive: DriveWriter,
         root_folder_id: str = "root") -> dict:
    """Dry-run plan: everything execute() would do, zero write calls.

    Reads only (files get/list). Refusals are returned in the plan
    instead of raised, so the CLI can print exactly what blocks the
    run. Steps carry the fresh Drive state they were planned against;
    execute() re-checks that state before each write.
    """
    approved, problems = match_approvals(proposals_doc, approvals_doc)
    skipped = [
        {
            "file_id": e["file_id"],
            "original_name": e["original_name"],
            "reason": "human review — no proposal to execute",
        }
        for e in proposals_doc["proposals"]
        if e.get("proposed_filename") is None
    ]
    result: dict = {
        "refused": list(problems),
        "steps": [],
        "skipped": skipped,
        "folders_to_create": [],
    }
    if problems:
        return result
    folders_seen: set[str] = set()
    for entry in approved:
        file_id = entry["file_id"]
        try:
            current = drive.get_file(file_id)
        except (DriveCliError, ExecutionError):
            result["refused"].append(
                f"{entry['original_name']!r}: file {file_id} not found "
                "on Drive — re-run the scan"
            )
            continue
        current_parents = list(current.get("parents", []))
        target_id, created, would_create = resolve_folder(
            drive, entry["target_folder_path"], root_folder_id, create=False
        )
        for label in would_create:
            if label not in folders_seen:
                folders_seen.add(label)
                result["folders_to_create"].append(label)
        already_placed = (
            target_id is not None
            and set(current_parents) == {target_id}
            and current.get("name") == entry["proposed_filename"]
        )
        if already_placed:
            result["skipped"].append({
                "file_id": file_id,
                "original_name": entry["original_name"],
                "reason": "already renamed and in the target folder",
            })
            continue
        if target_id is not None:
            collider = _check_collision(
                drive, target_id, entry["proposed_filename"], file_id
            )
            if collider is not None:
                result["refused"].append(
                    f"{entry['original_name']!r}: target name "
                    f"{entry['proposed_filename']!r} already exists in "
                    f"{entry['target_folder_path']} (file {collider}) — "
                    "rename it in the proposals file first"
                )
                continue
        result["steps"].append({
            "file_id": file_id,
            "original_name": current.get("name", entry["original_name"]),
            "original_parent_ids": current_parents,
            "proposed_filename": entry["proposed_filename"],
            "target_folder_path": entry["target_folder_path"],
            "target_folder_id": target_id,
            "proposal_hash": proposal_hash(
                file_id, entry["proposed_filename"],
                entry["target_folder_path"],
            ),
        })
    if result["refused"]:
        # A collision anywhere blocks the whole run: no partial writes.
        result["steps"] = []
    return result


def execute(proposals_doc: dict, approvals_doc: dict, drive: DriveWriter,
            log_path: str, *, execute: bool = False,
            root_folder_id: str = "root") -> dict:
    """Perform the approved plan. Refuses unless the gates all hold.

    Requires ``execute=True`` AND module ``WRITE_ENABLED`` (set only
    by run_execute.py --execute). Returns a report with executed,
    skipped, verification, and the log path. Raises GateRefused /
    WriteScopeError before or during the run; executed files stay
    logged either way.
    """
    if not execute or not WRITE_ENABLED:
        raise GateRefused(
            "Execution refused: pass execute=True with --execute on the "
            "command line. Dry-run is the default; nothing was written."
        )
    planned = plan(proposals_doc, approvals_doc, drive, root_folder_id)
    if planned["refused"]:
        raise GateRefused(
            "Execution refused:\n- " + "\n- ".join(planned["refused"])
        )
    report: dict = {
        "executed": [],
        "skipped": planned["skipped"],
        "folders_created": [],
        "verification": [],
        "log_path": log_path,
    }
    created_ids: set[str] = set()
    for step in planned["steps"]:
        file_id = step["file_id"]
        # Re-check fresh state right before the write: the file may
        # have moved or been renamed since the plan was built.
        current = drive.get_file(file_id)
        if (current.get("name") != step["original_name"]
                or set(current.get("parents", []))
                != set(step["original_parent_ids"])):
            raise GateRefused(
                f"{step['original_name']!r}: Drive state changed since "
                "the plan was built — re-run the scan and re-approve. "
                f"({len(report['executed'])} file(s) already executed; "
                "see the execution log.)"
            )
        target_id = step["target_folder_id"]
        if target_id is None:
            target_id, created, _ = resolve_folder(
                drive, step["target_folder_path"], root_folder_id, create=True
            )
            for folder in created:
                if folder["folder_id"] not in created_ids:
                    created_ids.add(folder["folder_id"])
                    report["folders_created"].append(folder["folder_id"])
                    # Folders are never deleted, but their creation is
                    # part of the audit trail — log it like a change.
                    append_log_record(log_path, {
                        "action": "folder_created",
                        "folder_id": folder["folder_id"],
                        "name": folder["name"],
                        "parent_id": folder["parent_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
        collider = _check_collision(
            drive, target_id, step["proposed_filename"], file_id
        )
        if collider is not None:
            raise GateRefused(
                f"{step['original_name']!r}: target name "
                f"{step['proposed_filename']!r} appeared in "
                f"{step['target_folder_path']} after planning "
                f"(file {collider}) — run aborted before this write."
            )
        original_parents = step["original_parent_ids"]
        moving = set(original_parents) != {target_id}
        new_parents = (
            [target_id] if moving and target_id not in original_parents
            else []
        )
        remove_parents = (
            [p for p in original_parents if p != target_id]
            if moving else []
        )
        new_name = (
            step["proposed_filename"]
            if step["original_name"] != step["proposed_filename"] else None
        )
        try:
            drive.update_file(
                file_id, name=new_name,
                add_parents=new_parents or None,
                remove_parents=remove_parents or None,
            )
        except DriveTimeoutError:
            # The update may have applied server-side while the CLI
            # call hung (seen live 2026-11-13). Verify live state
            # before deciding: applied -> log it and continue;
            # anything else -> stop the run, do not guess.
            current = drive.get_file(file_id)
            applied = (
                current.get("name") == step["proposed_filename"]
                and set(current.get("parents", [])) == {target_id}
            )
            if not applied:
                raise ExecutionError(
                    f"{step['original_name']!r}: files.update timed out "
                    "after 120s and live Drive state does not match the "
                    "plan — state unknown. Run stopped; no further "
                    "files were touched. Verify this file by hand "
                    "before re-running."
                )
        record = {
            "file_id": file_id,
            "original_name": step["original_name"],
            "original_parent_ids": step["original_parent_ids"],
            "new_name": step["proposed_filename"],
            "new_parent_ids": [target_id],
            "proposal_hash": step["proposal_hash"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        append_log_record(log_path, record)
        report["executed"].append(record)
    report["verification"] = verify_records(report["executed"], drive)
    return report


def verify_records(records: list[dict], drive: DriveWriter) -> list[dict]:
    """Re-fetch each executed file by ID; confirm name + parents.

    Returns one entry per record: ok flag plus expected vs actual.
    Never assumes success.
    """
    out: list[dict] = []
    for record in records:
        if "file_id" not in record:
            continue  # folder_created audit records are not files
        current = drive.get_file(record["file_id"])
        actual_name = current.get("name")
        actual_parents = sorted(current.get("parents", []))
        expected_parents = sorted(record["new_parent_ids"])
        ok = (
            actual_name == record["new_name"]
            and actual_parents == expected_parents
        )
        out.append({
            "file_id": record["file_id"],
            "ok": ok,
            "expected_name": record["new_name"],
            "actual_name": actual_name,
            "expected_parents": expected_parents,
            "actual_parents": actual_parents,
        })
    return out


def verify_log(log_path: str, drive: DriveWriter) -> list[dict]:
    """Verify every record in an execution log against live Drive."""
    return verify_records(load_log(log_path), drive)


def rollback(log_path: str, drive: DriveWriter, *,
             execute: bool = False) -> dict:
    """Restore original names + parents from an execution log.

    Runs in reverse log order (last change undone first). Dry-run by
    default: returns the restore plan with zero write calls. With
    ``execute=True`` (and WRITE_ENABLED) it performs the restores.
    Never deletes anything; a file missing from Drive is reported
    and skipped, not an error that aborts the rest.
    """
    records = load_log(log_path)
    report: dict = {
        "restored": [],
        "missing": [],
        "refused": [],
        "log_path": log_path,
    }
    if not execute or not WRITE_ENABLED:
        report["refused"].append(
            "Rollback is a dry-run unless execute=True with --execute. "
            "Nothing was restored."
        )
        report["would_restore"] = [
            {
                "file_id": r["file_id"],
                "restore_name": r["original_name"],
                "restore_parent_ids": r["original_parent_ids"],
            }
            for r in reversed(records) if "file_id" in r
        ]
        return report
    for record in reversed(records):
        if "file_id" not in record:
            continue  # folder_created audit records are not rolled back
        file_id = record["file_id"]
        try:
            current = drive.get_file(file_id)
        except (DriveCliError, ExecutionError):
            report["missing"].append({
                "file_id": file_id,
                "original_name": record["original_name"],
                "reason": "file not found on Drive — cannot restore",
            })
            continue
        current_parents = list(current.get("parents", []))
        add = [p for p in record["original_parent_ids"]
               if p not in current_parents]
        remove = [p for p in current_parents
                  if p not in record["original_parent_ids"]]
        new_name = (
            record["original_name"]
            if current.get("name") != record["original_name"] else None
        )
        drive.update_file(
            file_id, name=new_name,
            add_parents=add or None,
            remove_parents=remove or None,
        )
        report["restored"].append({
            "file_id": file_id,
            "restored_name": record["original_name"],
            "restored_parent_ids": record["original_parent_ids"],
        })
    return report
