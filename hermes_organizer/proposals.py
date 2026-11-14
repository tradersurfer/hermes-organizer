"""Proposals + approvals files for Hermes Organizer v0.7.0.

The scan produces OrganizerResult entries (metadata.file_id,
original_name, proposed_filename, target_folder_path, confidence,
subcategory). This module turns them into a proposals file the user
can approve, and reads back the approvals file they edited.

Flow:
  1. run_scan.py --proposals proposals.json writes one entry per
     scanned file, each with ``proposal_hash`` (SHA-256 of file_id +
     proposed_filename + target_folder_path) and ``approved: false``.
  2. The user copies proposals.json to approvals.json and flips
     ``approved`` to true on the entries they accept. They must not
     edit the proposed filename or folder — the hash catches that.
  3. run_execute.py loads both files. An entry executes only when
     the approvals entry matches the proposals entry by file_id, has
     ``approved: true``, and its ``proposal_hash`` equals the hash
     recomputed from the proposals entry. A re-scan changes the
     proposal (new filename/folder/date), the hash changes, and the
     old approval is refused as stale — never silently executed.

Files the scan could not classify (confidence < 0.60) have
``proposed_filename: null`` and ``proposal_hash: null``; they can
never be approved and the executor reports them as skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

PROPOSALS_VERSION = 1


def proposal_hash(file_id: str, proposed_filename: str | None,
                  target_folder_path: str | None) -> str | None:
    """Hash binding one proposal to one file.

    None when there is no proposal to bind (human-review entries).
    The separator (NUL) cannot appear in a Drive file ID, filename,
    or folder path, so the hash is unambiguous.
    """
    if not proposed_filename:
        return None
    material = "\0".join([file_id, proposed_filename, target_folder_path or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_proposals(results, source: str = "") -> dict:
    """One entry per scan result, approvals defaulting to false."""
    entries = []
    for r in results:
        proposed = r.proposed_filename
        entries.append({
            "file_id": r.metadata.file_id,
            "original_name": r.metadata.original_name,
            "proposed_filename": proposed,
            "target_folder_path": r.target_folder_path,
            "subcategory": r.visual_classification.subcategory,
            "confidence": r.visual_classification.confidence,
            "proposal_hash": proposal_hash(
                r.metadata.file_id, proposed, r.target_folder_path
            ),
            "approved": False,
        })
    return {
        "version": PROPOSALS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "proposals": entries,
    }


def write_proposals(results, path: str, source: str = "") -> dict:
    """Write the proposals file for a scan. Returns the document."""
    doc = build_proposals(results, source=source)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    return doc


def load_proposals(path: str) -> dict:
    """Load and structurally validate a proposals file."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or doc.get("version") != PROPOSALS_VERSION:
        raise ValueError(
            f"{path}: not a v{PROPOSALS_VERSION} proposals file "
            "(missing or wrong 'version')"
        )
    entries = doc.get("proposals")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: 'proposals' must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("file_id"):
            raise ValueError(f"{path}: every proposal needs a file_id")
        if entry["file_id"] in seen:
            raise ValueError(
                f"{path}: duplicate file_id {entry['file_id']!r}"
            )
        seen.add(entry["file_id"])
    return doc


def load_approvals(path: str) -> dict:
    """Load and structurally validate an approvals file.

    Same schema as a proposals file: the user copies the proposals
    file and flips ``approved`` to true. An entry without a boolean
    ``approved`` field is invalid — silence is not approval.
    """
    doc = load_proposals(path)
    for entry in doc["proposals"]:
        if not isinstance(entry.get("approved"), bool):
            raise ValueError(
                f"{path}: entry {entry.get('file_id')!r} has no boolean "
                "'approved' field — silence is not approval"
            )
    return doc


def match_approvals(proposals_doc: dict, approvals_doc: dict
                    ) -> tuple[list[dict], list[str]]:
    """Match approvals against proposals.

    Returns (approved_entries, problems). ``approved_entries`` are the
    proposals entries the user approved with a matching hash, in
    proposals order. ``problems`` are human-readable reasons the run
    must be refused (unknown file, hash mismatch, not approved).
    Review entries (no proposal) are never in approved_entries and
    never in problems — the caller reports them as skipped.
    """
    proposals = {e["file_id"]: e for e in proposals_doc["proposals"]}
    approvals = {e["file_id"]: e for e in approvals_doc["proposals"]}
    problems: list[str] = []
    approved: list[dict] = []
    for file_id, entry in approvals.items():
        if file_id not in proposals:
            problems.append(
                f"{entry.get('original_name', file_id)!r}: approval for "
                "a file that is not in the proposals file — stale approval"
            )
    for file_id, entry in proposals.items():
        if entry.get("proposed_filename") is None:
            continue  # human review — nothing to approve, reported as skipped
        decision = approvals.get(file_id)
        if decision is None:
            problems.append(
                f"{entry['original_name']!r}: no approval entry — "
                "every proposal must be explicitly approved"
            )
            continue
        if not decision.get("approved"):
            problems.append(
                f"{entry['original_name']!r}: not approved"
            )
            continue
        expected = proposal_hash(
            file_id, entry["proposed_filename"], entry["target_folder_path"]
        )
        if decision.get("proposal_hash") != expected:
            problems.append(
                f"{entry['original_name']!r}: proposal_hash mismatch — "
                "the proposal changed since approval (re-scan?); "
                "stale approval refused"
            )
            continue
        approved.append(entry)
    return approved, problems
