"""Tests for Drive listing pagination (Grok review fix).

No live Drive calls here: _run is replaced by a stub that serves a
fixed file list in pages, honoring the requested pageSize and
pageToken. Every test proves the listing consumes pages until
nextPageToken is exhausted (or the caller's limit is reached) —
never silently returning one page as if it were the whole folder.
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_organizer import execution as execution_mod  # noqa: E402
from hermes_organizer.execution import DriveWriter  # noqa: E402
from hermes_organizer.indexer import (  # noqa: E402
    DRIVE_LIST_MAX_PAGE_SIZE,
    DriveIndexer,
    _fetch_all_pages,
)

FOLDER_ID = "target-folder"


def _files(n, parent=FOLDER_ID, prefix="file"):
    return [{
        "id": f"id-{i}", "name": f"{prefix}-{i:03d}.jpg",
        "mimeType": "image/jpeg", "parents": [parent],
    } for i in range(n)]


class _PagedStub:
    """Stub _run serving a fixed file list in pages.

    Honors the requested pageSize and pageToken (the token is the
    offset, as a string). ``max_per_page`` forces small pages even
    when the caller asks for more — that is how the tests prove the
    loop follows tokens instead of trusting one page.
    """

    def __init__(self, files, max_per_page=None, gate_token_on_fields=False):
        self.files = list(files)
        self.calls = []
        self.max_per_page = max_per_page
        # When True, nextPageToken is only returned if the caller
        # asked for it in `fields` — how the real Drive channel
        # behaves (partial responses). Default False keeps the
        # original unconditional-token behavior for the loop tests.
        self.gate_token_on_fields = gate_token_on_fields

    def __call__(self, args):
        params = json.loads(args[args.index("--params") + 1])
        self.calls.append(dict(params))
        # Honor the query's name / mimeType filters, like the real API:
        # find_child relies on the server returning matches only.
        files = list(self.files)
        q = params.get("q", "")
        name_m = re.search(r"name = '([^']*)'", q)
        if name_m:
            files = [f for f in files if f["name"] == name_m.group(1)]
        mime_m = re.search(r"mimeType = '([^']*)'", q)
        if mime_m:
            files = [f for f in files
                     if f.get("mimeType") == mime_m.group(1)]
        page_size = params.get("pageSize", 100)
        if self.max_per_page is not None:
            page_size = min(page_size, self.max_per_page)
        offset = int(params.get("pageToken") or 0)
        page = files[offset:offset + page_size]
        out = {"files": [dict(f) for f in page]}
        if offset + page_size < len(files):
            if (not self.gate_token_on_fields
                    or "nextPageToken" in params.get("fields", "")):
                out["nextPageToken"] = str(offset + page_size)
        return out


def test_fetch_all_pages_consumes_every_page():
    stub = _PagedStub(_files(120))
    out = _fetch_all_pages(stub, {"q": "x", "fields": "files(id)"},
                           page_size=50)
    assert len(out) == 120
    assert len(stub.calls) == 3  # 50 + 50 + 20
    assert stub.calls[1]["pageToken"] == "50"
    assert stub.calls[2]["pageToken"] == "100"


def test_fetch_all_pages_respects_limit():
    stub = _PagedStub(_files(120))
    out = _fetch_all_pages(stub, {"q": "x", "fields": "files(id)"},
                           page_size=50, limit=70)
    assert len(out) == 70
    assert len(stub.calls) == 2
    # The second request asks only for the remaining 20.
    assert stub.calls[1]["pageSize"] == 20


def test_fetch_all_pages_raises_when_page_cap_hit():
    stub = _PagedStub(_files(10))
    with pytest.raises(RuntimeError, match="exceeded .* pages"):
        _fetch_all_pages(stub, {"q": "x", "fields": "files(id)"},
                         page_size=1, page_limit=3)
    assert len(stub.calls) == 3  # stopped at the cap, did not loop forever


def test_list_children_requests_max_page_size_and_pages():
    stub = _PagedStub(_files(120), max_per_page=50)
    indexer = DriveIndexer()
    indexer._run = stub
    out = indexer.list_children(FOLDER_ID)
    assert len(out) == 120
    assert stub.calls[0]["pageSize"] == DRIVE_LIST_MAX_PAGE_SIZE
    assert len(stub.calls) == 3


def test_list_folder_images_scans_beyond_one_page():
    stub = _PagedStub(_files(120), max_per_page=50)
    indexer = DriveIndexer()
    indexer._run = stub
    out = indexer.list_folder_images(FOLDER_ID)
    assert len(out) == 120  # the old default (50) would have returned 50
    assert all(f.name.endswith(".jpg") for f in out)
    assert stub.calls[0]["pageSize"] == DRIVE_LIST_MAX_PAGE_SIZE


def test_list_folder_images_limit_caps_the_total():
    stub = _PagedStub(_files(120), max_per_page=50)
    indexer = DriveIndexer()
    indexer._run = stub
    out = indexer.list_folder_images(FOLDER_ID, limit=70)
    assert len(out) == 70


def test_list_unorganized_paginates():
    stub = _PagedStub(_files(60), max_per_page=25)
    indexer = DriveIndexer()
    indexer._run = stub
    out = indexer.list_unorganized()
    assert len(out) == 60
    assert len(stub.calls) == 3


def test_find_child_returns_first_match_and_stops():
    files = _files(3)
    files[1]["name"] = "wanted.jpg"
    files[2]["name"] = "wanted.jpg"
    stub = _PagedStub(files, max_per_page=1)
    indexer = DriveIndexer()
    indexer._run = stub
    # The name filter runs server-side: page 1 already holds a match,
    # so limit=1 is complete after one call.
    found = indexer.find_child(FOLDER_ID, "wanted.jpg")
    assert found is not None
    assert found["id"] == "id-1"
    assert len(stub.calls) == 1  # limit=1: one page is enough


def test_find_child_returns_none_when_absent():
    stub = _PagedStub(_files(5), max_per_page=2)
    indexer = DriveIndexer()
    indexer._run = stub
    assert indexer.find_child(FOLDER_ID, "nope.jpg") is None


def test_collision_on_later_page_is_found():
    files = _files(120)
    files[105]["name"] = "2026-11-13_ProductPhoto_image_product.jpg"
    files[105]["id"] = "collider-id"
    stub = _PagedStub(files, max_per_page=50)
    writer = DriveWriter()
    writer._run = stub
    found = execution_mod._check_collision(
        writer, FOLDER_ID,
        "2026-11-13_ProductPhoto_image_product.jpg", "file-1")
    assert found == "collider-id"
    assert len(stub.calls) == 3  # the collider was on page 3


def test_collision_check_returns_none_when_name_free():
    stub = _PagedStub(_files(120), max_per_page=50)
    writer = DriveWriter()
    writer._run = stub
    assert execution_mod._check_collision(
        writer, FOLDER_ID, "nothing-like-this.jpg", "file-1") is None


def test_list_folder_images_requests_next_page_token():
    # Regression test for the 2026-11-21 miss: the loop existed but
    # no call site requested the token in `fields`, so the real
    # channel returned page 1 only while the unconditional-token
    # stubs above still passed. With gate_token_on_fields=True the
    # stub behaves like the real channel: no token in fields means
    # no token back, and the scan stops after one page.
    stub = _PagedStub(_files(120), max_per_page=50,
                      gate_token_on_fields=True)
    indexer = DriveIndexer()
    indexer._run = stub
    out = indexer.list_folder_images(FOLDER_ID)
    assert len(out) == 120  # without the token in fields, this is 50
    assert all("nextPageToken" in c["fields"] for c in stub.calls)
    assert len(stub.calls) == 3


def test_list_children_requests_next_page_token():
    # Same gate, on the collision-check path: a collider past page 1
    # is invisible unless the token is requested.
    stub = _PagedStub(_files(120), max_per_page=50,
                      gate_token_on_fields=True)
    indexer = DriveIndexer()
    indexer._run = stub
    out = indexer.list_children(FOLDER_ID)
    assert len(out) == 120
    assert all("nextPageToken" in c["fields"] for c in stub.calls)
