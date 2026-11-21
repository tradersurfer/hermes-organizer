"""Tests for the folder-scoped Drive scan (v0.4 follow-up).

The indexer lists one folder by parent ID; the watcher uses it when
folder_id is given and keeps the unorganized sweep otherwise. No live
Drive calls here: the CLI runner is mocked and downloads copy a mock image.
"""
import json
import os
import shutil
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.indexer import DriveFile, DriveIndexer  # noqa: E402
from hermes_organizer.watcher import scan_drive_sample  # noqa: E402

FOLDER_ID = "1lTSH_AZkkd1kP2kHMgRnfverxH9By4_Q"
RECEIPT_IMG = os.path.join(REPO_ROOT, "mock_samples", "IMG_0042_receipt.png")


def test_list_folder_images_queries_parent_and_images():
    captured = {}

    def fake_run(self, args):
        captured["args"] = args
        return {"files": [
            {"id": "abc123", "name": "attachment (1).jpg",
             "mimeType": "image/jpeg", "parents": [FOLDER_ID],
             "modifiedTime": "2026-11-12T20:45:45.149Z", "size": "64057"},
        ]}

    indexer = DriveIndexer()
    with mock.patch.object(DriveIndexer, "_run", fake_run):
        files = indexer.list_folder_images(FOLDER_ID, page_size=50)
    params = json.loads(captured["args"][captured["args"].index("--params") + 1])
    assert f"'{FOLDER_ID}' in parents" in params["q"]
    assert "mimeType contains 'image/'" in params["q"]
    assert "trashed=false" in params["q"]
    assert len(files) == 1
    assert files[0].name == "attachment (1).jpg"
    assert files[0].file_id == "abc123"


def test_list_folder_images_rejects_bad_id():
    indexer = DriveIndexer()
    with pytest.raises(ValueError):
        indexer.list_folder_images("abc' or '1'='1", page_size=10)
    with pytest.raises(ValueError):
        indexer.list_folder_images("has space", page_size=10)


class StubDriveIndexer:
    """Fake indexer: records which listing ran, downloads a mock image."""

    def __init__(self, files):
        self.files = files
        self.listed_folder = None
        self.listed_unorganized = False
        self.downloaded = []

    def list_folder_images(self, folder_id, page_size=1000, limit=None):
        self.listed_folder = folder_id
        return self.files[:limit] if limit is not None else self.files

    def list_unorganized(self, page_size=1000, limit=None):
        self.listed_unorganized = True
        return self.files[:limit] if limit is not None else self.files

    def download(self, file_id, dest_path):
        self.downloaded.append(file_id)
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        shutil.copy(RECEIPT_IMG, dest_path)
        return dest_path


def _receipt_file():
    return DriveFile(file_id="abc123", name="attachment (1).jpg",
                     mime_type="image/jpeg", parents=[FOLDER_ID])


def test_scan_drive_sample_with_folder_id_uses_folder_listing(tmp_path):
    indexer = StubDriveIndexer([_receipt_file()])
    results = scan_drive_sample(indexer, str(tmp_path), limit=10,
                                audit_log=None, folder_id=FOLDER_ID)
    assert indexer.listed_folder == FOLDER_ID
    assert indexer.listed_unorganized is False
    assert indexer.downloaded == ["abc123"]
    assert len(results) == 1
    assert results[0].visual_classification.subcategory == "Receipt"


def test_scan_drive_sample_without_folder_id_uses_unorganized_sweep(tmp_path):
    indexer = StubDriveIndexer([_receipt_file()])
    results = scan_drive_sample(indexer, str(tmp_path), limit=10,
                                audit_log=None)
    assert indexer.listed_folder is None
    assert indexer.listed_unorganized is True
    assert len(results) == 1
    assert results[0].visual_classification.subcategory == "Receipt"
