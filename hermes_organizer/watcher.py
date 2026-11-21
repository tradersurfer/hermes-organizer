"""drive_watcher.py skeleton — poll/filter/inspect/resolve/execute loop.

v0.1 implements the loop over a local folder or a one-shot Drive listing.
The Drive `changes.watch` webhook hook is a stub: it needs a registered
public endpoint, which is a later step. Execution stays gated (see
execution.py) — the watcher only ever *proposes* in v0.1.

Loop (per the handoff):
  poll -> filter unorganized -> inspect (OCR + classify) -> resolve location
  -> execute (GATED) + audit log
"""
from __future__ import annotations

import hashlib
import os
import time

from . import audit
from .classifier import classify, clean_text, is_unorganized
from .execution import resolve_folder
from .indexer import DriveIndexer, list_local_images
from .naming import deduplicate_filenames, propose_filename, review_proposal
from .ocr import SUPPORTED_IMAGE_EXTS, LocalOcr, VisionBridge, extract_text
from .schemas import FileMetadata, OrganizerResult

MAX_DRIVE_REQ_PER_MIN = 100  # handoff cap; v0.1 indexer defaults to 1/sec


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duplicate_of(seen_hashes: dict[str, str], content_hash: str, name: str) -> str | None:
    """Flag only — duplicates are still classified normally, never skipped
    or deleted (read-only). Returns the FIRST file's name on a repeat hash."""
    first = seen_hashes.get(content_hash)
    if first is None:
        seen_hashes[content_hash] = name
        return None
    return first


def _deduplicate_proposals(
    results: list[OrganizerResult],
    existing: list[tuple[str | None, str]] | None = None,
) -> list[OrganizerResult]:
    """Append _2/_3/... to repeat (filename, folder) pairs in one scan,
    and to proposals colliding with files already in the target folder
    (``existing`` — read at scan time). Review results (no proposed
    filename) are untouched."""
    pairs = [(r.proposed_filename, r.target_folder_path) for r in results]
    fixed = deduplicate_filenames(pairs, existing=existing)
    return [
        r.model_copy(update={"proposed_filename": fn})
        if fn != r.proposed_filename else r
        for r, (fn, _folder) in zip(results, fixed)
    ]


def _existing_target_names(
    results: list[OrganizerResult],
    list_names,
    audit_log: str | None = None,
) -> list[tuple[str | None, str]]:
    """(filename, folder) pairs already present in the target folders.

    ``list_names(folder_path)`` returns the filenames in that folder,
    or None when the folder does not exist (nothing to collide with).
    One listing per distinct target folder; a listing failure falls
    back to no existing names for that folder — the scan continues
    and the executor's target-name-free check stays the backstop —
    and is audit-logged. Review results have no proposal and are
    skipped (their folder is never listed).
    """
    existing: list[tuple[str | None, str]] = []
    folders = {r.target_folder_path for r in results
               if r.proposed_filename is not None}
    for folder in sorted(folders):
        try:
            names = list_names(folder)
        except Exception as exc:  # noqa: BLE001 - listing must not kill the scan
            if audit_log:
                audit.log_skip(audit_log, "", folder,
                               f"target-list-error: {_short_reason(exc)}")
            continue
        if names is None:
            continue
        existing.extend((name, folder) for name in names)
    return existing


def _drive_target_names(indexer: DriveIndexer, root_folder_id: str = "root"):
    """list_names callable for Drive scans: resolve the taxonomy path
    with the executor's resolver (read-only — create=False never
    creates a folder) and list the folder's children. Returns None
    when the target folder does not exist yet."""
    def list_names(folder_path: str) -> list[str] | None:
        folder_id, _created, _would = resolve_folder(
            indexer, folder_path, root_folder_id, create=False
        )
        if folder_id is None:
            return None
        return [child["name"] for child in indexer.list_children(folder_id)]

    return list_names


def _local_target_names(base_dir: str):
    """list_names callable for local scans: the taxonomy path resolves
    under the scanned folder (e.g. <scan>/Work/Product Photos/2026/).
    A missing directory means nothing to collide with."""
    def list_names(folder_path: str) -> list[str] | None:
        target = os.path.join(base_dir, *[
            seg for seg in folder_path.strip("/").split("/") if seg
        ])
        if not os.path.isdir(target):
            return None
        return os.listdir(target)

    return list_names


def scan_local_folder(
    folder: str,
    audit_log: str | None = None,
    vision: VisionBridge | None = None,
    vision_mode: str = "auto",
) -> list[OrganizerResult]:
    """One-shot scan of a local folder. Returns validated results.

    ``vision`` (a VisionBridge) is a second opinion only: in ``auto`` mode
    it runs only on files OCR leaves in review or with very thin OCR text;
    in ``always`` mode it runs on every image. It never changes the
    read-only boundary — results are still proposals.
    """
    ocr = LocalOcr()
    results: list[OrganizerResult] = []
    seen_hashes: dict[str, str] = {}
    for f in list_local_images(folder):
        path = f.file_id
        try:
            ocr_result = extract_text(path, ocr)
        except Exception as exc:  # noqa: BLE001 - OCR must not kill the batch
            if audit_log:
                audit.log_skip(audit_log, f.file_id, f.name, f"ocr-error: {exc}")
            continue
        f.content_hash = _sha256_file(path)
        duplicate_of = _duplicate_of(seen_hashes, f.content_hash, f.name)
        result = _build_result(f, ocr_result.text, ocr_result.engine, duplicate_of)
        result = _try_vision(f, path, result, ocr_result.text, vision, vision_mode, audit_log)
        results.append(result)
    results = _deduplicate_proposals(
        results,
        _existing_target_names(results, _local_target_names(folder), audit_log),
    )
    if audit_log:
        for result in results:
            audit.log_proposal(audit_log, result.model_dump())
    return results


def scan_drive_sample(indexer: DriveIndexer, download_dir: str,
                      limit: int = 10, audit_log: str | None = None,
                      vision: VisionBridge | None = None,
                      vision_mode: str = "auto",
                      folder_id: str | None = None,
                      root_folder_id: str = "root") -> list[OrganizerResult]:
    """One-shot read-only Drive scan: list images, download working copies,
    classify. Originals are never touched. With ``folder_id`` set, only
    images directly inside that folder are listed; otherwise the
    unorganized sweep (IMG_ / Screenshot / scan_ names) runs as before.
    ``vision`` / ``vision_mode`` behave as in scan_local_folder.
    Target folders are resolved from ``root_folder_id`` (My Drive by
    default) and listed read-only, so a proposal colliding with a file
    already in its destination gets its _2/_3/... suffix at proposal
    time; a target folder that does not exist yet has nothing to
    collide with."""
    ocr = LocalOcr()
    results: list[OrganizerResult] = []
    seen_hashes: dict[str, str] = {}
    if folder_id is not None:
        files = indexer.list_folder_images(folder_id, limit=limit)
    else:
        files = indexer.list_unorganized(limit=limit)
    for f in files:
        dest = os.path.join(download_dir, f.name)
        try:
            indexer.download(f.file_id, dest)
            f.content_hash = _sha256_file(dest)
            ocr_result = extract_text(dest, ocr)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
            if audit_log:
                audit.log_skip(audit_log, f.file_id, f.name, f"scan-error: {exc}")
            continue
        duplicate_of = _duplicate_of(seen_hashes, f.content_hash, f.name)
        result = _build_result(f, ocr_result.text, ocr_result.engine, duplicate_of)
        result = _try_vision(f, dest, result, ocr_result.text, vision, vision_mode, audit_log)
        results.append(result)
    results = _deduplicate_proposals(
        results,
        _existing_target_names(
            results, _drive_target_names(indexer, root_folder_id), audit_log
        ),
    )
    if audit_log:
        for result in results:
            audit.log_proposal(audit_log, result.model_dump())
    return results


def _short_reason(exc: Exception) -> str:
    """One short, single-line reason for the audit log (never key material)."""
    return str(exc).splitlines()[0][:120] if str(exc) else exc.__class__.__name__


def _try_vision(f, path: str, result: OrganizerResult, ocr_text: str,
                vision: VisionBridge | None, vision_mode: str,
                audit_log: str | None) -> OrganizerResult:
    """Second opinion from the vision bridge. Keeps the vision-assisted
    result only when it improves the OCR-only one (classified, or higher
    confidence); otherwise the OCR-only result stands. Vision errors are
    logged as skips and never kill the batch. PDFs stay OCR-only: the
    bridge takes image bytes, not rendered pages."""
    if vision is None or not vision.available():
        return result
    if os.path.splitext(path)[1].lower() not in SUPPORTED_IMAGE_EXTS:
        return result
    needs_vision = (
        vision_mode == "always"
        or result.visual_classification.subcategory == "Requires_Human_Review"
        or len(ocr_text) < 120
    )
    if not needs_vision:
        return result
    try:
        vision_result = vision.extract(path)
    except Exception as exc:  # noqa: BLE001 - vision must never kill a batch
        if audit_log:
            audit.log_skip(audit_log, f.file_id, f.name,
                           f"vision-error: {_short_reason(exc)}")
        return result
    combined = (ocr_text + "\n" + vision_result.text) if ocr_text.strip() else vision_result.text
    # engine_label names the provider/model cleanly (e.g.
    # cloudflare-llama-3.2-11b-vision); stubs without it fall back to
    # .model, which is what the Gemini bridge has always used.
    engine_label = getattr(vision, "engine_label", vision.model)
    engine = (
        f"rapidocr-local+{engine_label}" if ocr_text.strip() else engine_label
    )
    new_result = _build_result(
        f, combined, engine, result.duplicate_of, vision_used=True)
    old = result.visual_classification
    new = new_result.visual_classification
    # Better = newly classified out of review, or higher confidence.
    # (A newly-classified result always has confidence >= 0.60, above any
    # review result, so the confidence comparison alone implements both.)
    kept = new.confidence > old.confidence
    if audit_log:
        audit.log_vision(audit_log, f.file_id, f.name, kept,
                         old.confidence, new.confidence, engine)
    if kept:
        return new_result
    return result


def _build_result(f, text: str, engine: str,
                  duplicate_of: str | None = None,
                  vision_used: bool = False) -> OrganizerResult:
    """Classify + propose for one file. No audit logging here — the scan
    loops log the FINAL result once, after the vision second opinion."""
    cleaned = clean_text(text)
    # Pass the RAW text to classify(): it builds its own split variant and
    # matches against both, so the split only ever adds signal (e.g. the raw
    # "TransUnion" still matches "transunion" while the split "Your Credit
    # Scores" matches "credit score"). `cleaned` stays the stored text and
    # the naming input — it reads better that way.
    classification = classify(f.name, text)
    meta = FileMetadata(
        file_id=f.file_id, original_name=f.name, mime_type=f.mime_type,
        size_bytes=f.size_bytes, modified_time=f.modified_time,
        content_hash=getattr(f, "content_hash", None),
    )
    if classification.subcategory == "Requires_Human_Review":
        proposed, folder = review_proposal(f.name)
    else:
        from datetime import datetime

        mtime = None
        if f.modified_time:
            try:
                mtime = datetime.fromisoformat(f.modified_time.replace("Z", "+00:00"))
            except ValueError:
                mtime = None
        proposed, folder = propose_filename(f.name, classification.subcategory, cleaned, mtime)
    return OrganizerResult(
        metadata=meta, extracted_text=cleaned, ocr_engine=engine,
        visual_classification=classification,
        proposed_filename=proposed, target_folder_path=folder,
        duplicate_of=duplicate_of, vision_used=vision_used, action="propose",
    )


def _classify_file(f, text: str, engine: str, audit_log: str | None,
                   duplicate_of: str | None = None) -> OrganizerResult:
    """Backward-compatible wrapper: build the result and log the proposal."""
    result = _build_result(f, text, engine, duplicate_of)
    if audit_log:
        audit.log_proposal(audit_log, result.model_dump())
    return result


def watch_loop(poll_seconds: int = 3600) -> None:
    """STUB — scheduled poll loop. v0.1 runs one-shot scans instead."""
    raise NotImplementedError(
        "watch_loop is a skeleton in v0.1. Run one-shot scans via run_scan.py; "
        "the Drive changes.watch webhook needs a registered endpoint (later)."
    )


def filter_unorganized(files: list) -> list:
    """Handoff filter step, extracted for tests."""
    return [f for f in files if is_unorganized(f.name, f.parents)]
