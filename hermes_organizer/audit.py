"""Audit log for Hermes Organizer v0.1.

Append-only JSONL. In v0.1 every entry is a PROPOSAL — no execution happens,
so there is nothing to roll back. When writes are enabled later, executed
entries get status=executed with the Drive file id and a reversible record.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_proposal(log_path: str, result: dict) -> None:
    entry = {
        "ts": _now_iso(),
        "action": "propose",
        "file_id": result["metadata"]["file_id"],
        "original_name": result["metadata"]["original_name"],
        "category": result["visual_classification"]["category"],
        "subcategory": result["visual_classification"]["subcategory"],
        "confidence": result["visual_classification"]["confidence"],
        "content_hash": result["metadata"].get("content_hash"),
        "duplicate_of": result.get("duplicate_of"),
        "vision_used": result.get("vision_used", False),
        "proposed_filename": result.get("proposed_filename"),
        "target_folder_path": result["target_folder_path"],
    }
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def log_vision(log_path: str, file_id: str, name: str, kept: bool,
               old_confidence: float, new_confidence: float,
               engine: str) -> None:
    """One entry per vision second-opinion attempt, kept or not, so the
    scan leaves a trace of every AI look at a file (not just the ones
    that changed the result)."""
    entry = {"ts": _now_iso(), "action": "vision", "file_id": file_id,
             "original_name": name, "kept": kept,
             "old_confidence": old_confidence,
             "new_confidence": new_confidence, "engine": engine}
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def log_skip(log_path: str, file_id: str, name: str, reason: str) -> None:
    entry = {"ts": _now_iso(), "action": "skip", "file_id": file_id,
             "original_name": name, "reason": reason}
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
