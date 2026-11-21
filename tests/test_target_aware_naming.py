"""Tests for target-aware proposals.

Proposals used to collide with files already in the destination
folder: the scan only deduped within itself, so the executor's
target-name-free check refused the file at execute time. This blocked
attachment (5).jpg in the Beyond Vision wave — it proposed
2026-11-12_ProductPhoto_sleeved_abstract.jpg, but
/Work/Product Photos/2026/ already held sleeved_abstract.jpg and
sleeved_abstract_2.jpg. Now the scan reads each target folder's
existing filenames and the colliding proposal gets its _2/_3/...
suffix at proposal time.

Covered here:
  (a) deduplicate_filenames() with ``existing`` (unit, incl. the
      exact Beyond Vision case);
  (b) the local wiring: scan_local_folder plants the suffix when the
      target subfolder already holds the name;
  (c) the Drive wiring: scan_drive_sample with a stub indexer —
      existing names bump the suffix, a missing target folder leaves
      the proposal alone, and a listing failure falls back to
      scan-only dedup instead of killing the scan.
No live Drive calls anywhere in this file.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.indexer import DriveFile  # noqa: E402
from hermes_organizer.naming import deduplicate_filenames  # noqa: E402

FOLDER = "/Work/Product Photos/2026/"
BASE = "2026-11-12_ProductPhoto_sleeved_abstract.jpg"


def test_dedup_with_existing_beyond_vision_case():
    """The exact Beyond Vision collision: base + _2 already in the
    target folder, so the proposal becomes _3."""
    pairs = [(BASE, FOLDER)]
    existing = [
        (BASE, FOLDER),
        ("2026-11-12_ProductPhoto_sleeved_abstract_2.jpg", FOLDER),
    ]
    assert deduplicate_filenames(pairs, existing=existing) == [
        ("2026-11-12_ProductPhoto_sleeved_abstract_3.jpg", FOLDER)]


def test_dedup_with_existing_base_free():
    """Only _2 exists in the target — the base name is free and the
    proposal is unchanged."""
    pairs = [(BASE, FOLDER)]
    existing = [("2026-11-12_ProductPhoto_sleeved_abstract_2.jpg", FOLDER)]
    assert deduplicate_filenames(pairs, existing=existing) == pairs


def test_dedup_with_existing_non_colliding_unchanged():
    """An existing file with an unrelated name changes nothing."""
    pairs = [(BASE, FOLDER)]
    existing = [("2026-11-12_ProductPhoto_black_shirt.jpg", FOLDER)]
    assert deduplicate_filenames(pairs, existing=existing) == pairs


def test_dedup_existing_in_other_folder_ignored():
    """The same filename in a *different* folder is not a collision."""
    pairs = [(BASE, FOLDER)]
    existing = [(BASE, "/Work/Product Photos/2025/")]
    assert deduplicate_filenames(pairs, existing=existing) == pairs


def test_dedup_existing_combines_with_within_scan_repeats():
    """Existing base + two identical proposals in one scan ->
    _2 and _3 (the within-scan dedup still runs on top)."""
    pairs = [(BASE, FOLDER), (BASE, FOLDER)]
    existing = [(BASE, FOLDER)]
    assert deduplicate_filenames(pairs, existing=existing) == [
        ("2026-11-12_ProductPhoto_sleeved_abstract_2.jpg", FOLDER),
        ("2026-11-12_ProductPhoto_sleeved_abstract_3.jpg", FOLDER)]


def test_dedup_existing_passes_through_review_results():
    """Review results (filename None) pass through untouched even
    when ``existing`` is given."""
    pairs = [
        (None, "/Requires_Human_Review/"),
        (BASE, FOLDER),
    ]
    existing = [(BASE, FOLDER)]
    assert deduplicate_filenames(pairs, existing=existing) == [
        (None, "/Requires_Human_Review/"),
        ("2026-11-12_ProductPhoto_sleeved_abstract_2.jpg", FOLDER)]


def test_dedup_without_existing_is_unchanged():
    """No ``existing`` argument: the old scan-only behaviour."""
    pairs = [(BASE, FOLDER), (BASE, FOLDER)]
    assert deduplicate_filenames(pairs) == [
        (BASE, FOLDER),
        ("2026-11-12_ProductPhoto_sleeved_abstract_2.jpg", FOLDER)]


def _suffixed(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{stem}_2.{ext}" if dot else f"{name}_2"


def test_local_scan_target_aware(tmp_path):
    """Wiring: one mock image, scanned twice. The first scan proposes
    name N. Planting a file named N in the target subfolder (under
    the scanned folder) makes the second scan propose N_2."""
    from hermes_organizer.watcher import scan_local_folder

    src = os.path.join(REPO_ROOT, "mock_samples", "IMG_0103_product.png")
    shutil.copy(src, tmp_path / "a.png")
    first = scan_local_folder(str(tmp_path))
    assert len(first) == 1
    name = first[0].proposed_filename
    folder = first[0].target_folder_path
    assert name is not None

    # Control: a second folder with the same single image and no
    # planted file proposes the same name.
    control = tmp_path / "control"
    control.mkdir()
    shutil.copy(src, control / "a.png")
    control_results = scan_local_folder(str(control))
    assert control_results[0].proposed_filename == name

    # Plant the collision: a file named N in the target subfolder.
    target = os.path.join(str(tmp_path), *folder.strip("/").split("/"))
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, name), "w", encoding="utf-8") as fh:
        fh.write("already here\n")
    second = scan_local_folder(str(tmp_path))
    assert len(second) == 1
    assert second[0].proposed_filename == _suffixed(name)
    assert second[0].target_folder_path == folder


RECEIPT_IMG = os.path.join(REPO_ROOT, "mock_samples", "IMG_0042_receipt.png")
FOLDER_ID = "1lTSH_AZkkd1kP2kHMgRnfverxH9By4_Q"


class StubDriveIndexer:
    """Fake indexer: one Drive file, downloads a mock image, and a
    fake folder tree where every path resolves. ``planted`` are the
    filenames list_children returns for any folder; ``missing`` makes
    find_child return None (target folder does not exist);
    ``fail_listing`` makes list_children raise."""

    def __init__(self, files, planted=None, missing=False, fail_listing=False):
        self.files = files
        self.planted = planted or []
        self.missing = missing
        self.fail_listing = fail_listing
        self.list_children_calls = 0

    def list_folder_images(self, folder_id, page_size=1000, limit=None):
        return self.files[:limit] if limit is not None else self.files

    def list_unorganized(self, page_size=1000, limit=None):
        return self.files[:limit] if limit is not None else self.files

    def download(self, file_id, dest_path):
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        # copy2 keeps the source mtime, so the proposed date is stable
        # across repeated scans in one test.
        shutil.copy2(RECEIPT_IMG, dest_path)
        return dest_path

    def find_child(self, parent_id, name, mime_type=None):
        if self.missing:
            return None
        return {"id": f"{parent_id}/{name}", "name": name,
                "mimeType": mime_type or "application/vnd.google-apps.folder",
                "parents": [parent_id]}

    def list_children(self, folder_id):
        self.list_children_calls += 1
        if self.fail_listing:
            raise RuntimeError("Drive CLI failed: boom")
        return [{"id": f"child-{i}", "name": name,
                 "mimeType": "image/jpeg", "parents": [folder_id]}
                for i, name in enumerate(self.planted)]


def _drive_file():
    return [DriveFile(
        file_id="abc123", name="attachment (1).jpg", mime_type="image/jpeg",
        parents=[FOLDER_ID], modified_time="2026-11-12T20:45:45.149Z",
        size_bytes=64057,
    )]


def _drive_scan(stub, download_dir):
    from hermes_organizer.watcher import scan_drive_sample

    return scan_drive_sample(stub, str(download_dir), limit=10,
                             audit_log=None, vision=None,
                             folder_id=FOLDER_ID)


def test_drive_scan_target_aware(tmp_path):
    """Wiring: the stub target folder holds the proposed name (and
    its _2), so the scan proposes _3 — with exactly one listing for
    the one distinct target folder."""
    stub = StubDriveIndexer(_drive_file())
    first = _drive_scan(stub, tmp_path / "dl1")
    assert len(first) == 1
    name = first[0].proposed_filename
    assert name is not None

    planted = StubDriveIndexer(_drive_file(), planted=[name, _suffixed(name)])
    second = _drive_scan(planted, tmp_path / "dl2")
    assert len(second) == 1
    stem, dot, ext = name.rpartition(".")
    assert second[0].proposed_filename == f"{stem}_3.{ext}"
    assert second[0].target_folder_path == first[0].target_folder_path
    assert planted.list_children_calls == 1


def test_drive_scan_missing_target_folder_unchanged(tmp_path):
    """The target folder does not exist yet (find_child finds nothing)
    — nothing to collide with, proposal unchanged."""
    stub = StubDriveIndexer(_drive_file())
    first = _drive_scan(stub, tmp_path / "dl1")
    name = first[0].proposed_filename

    missing = StubDriveIndexer(_drive_file(), missing=True)
    second = _drive_scan(missing, tmp_path / "dl2")
    assert second[0].proposed_filename == name
    assert missing.list_children_calls == 0


def test_drive_scan_listing_failure_falls_back(tmp_path):
    """A listing failure does not kill the scan: it falls back to
    scan-only dedup and the proposal is unchanged."""
    stub = StubDriveIndexer(_drive_file())
    first = _drive_scan(stub, tmp_path / "dl1")
    name = first[0].proposed_filename

    failing = StubDriveIndexer(_drive_file(), fail_listing=True)
    second = _drive_scan(failing, tmp_path / "dl2")
    assert len(second) == 1
    assert second[0].proposed_filename == name
