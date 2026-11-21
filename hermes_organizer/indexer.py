"""Read-only indexer for Hermes Organizer v0.1.

Two sources:
- DriveIndexer: lists files via the connected Google Drive account using the
  hatch_gws_cli (Drive API v3). READ-ONLY: only `files list` / `files get`.
  Token-bucket rate limiter, default 1 req/sec (handoff cap: 100 req/min).
- LocalIndexer: walks a local folder (used for the sample + mock runs).

No write calls exist in this module.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field

DRIVE_CLI = "/opt/hatch/bin/hatch_gws_cli"

# Drive API v3 files.list accepts pageSize up to 1000.
DRIVE_LIST_MAX_PAGE_SIZE = 1000
# Upper bound on pages per listing. 100 pages x 1000 items is far above
# any folder this tool is used on; the cap exists so a runaway listing
# fails loudly instead of fetching forever.
DRIVE_LIST_MAX_PAGES = 100


def _fetch_all_pages(run, params, *, page_size, limit=None,
                     page_limit=DRIVE_LIST_MAX_PAGES):
    """Fetch every page of a files.list query.

    ``run`` is a ``_run``-style callable (args -> response dict).
    Follows ``nextPageToken`` while the channel returns one; stops
    early once ``limit`` items are collected (the caller's scan
    limit). Per-request pageSize is tightened to the remaining limit
    so a small scan never asks for a huge page.

    Raises RuntimeError if ``page_limit`` pages are exhausted with
    more pages outstanding — a partial listing must never silently
    pass as complete: collision checks and scans depend on seeing
    every child.

    Channel note (verified live 2026-11-21): the token is only
    returned when requested in ``fields`` — Drive partial responses
    omit it otherwise, which is why every call site below asks for
    ``nextPageToken`` explicitly. A two-page round-trip against the
    real CLI (pageSize 5, then the returned token) yielded 5 new
    files with zero overlap, so the loop follows tokens live.
    page_size is set to the API maximum (1000) at every call site.
    """
    if limit is not None and limit <= 0:
        return []
    out: list[dict] = []
    page_token = None
    for _ in range(page_limit):
        remaining = None if limit is None else limit - len(out)
        p = dict(params)
        p["pageSize"] = (
            page_size if remaining is None
            else min(page_size, remaining)
        )
        if page_token:
            p["pageToken"] = page_token
        data = run(["files", "list", "--params", json.dumps(p)])
        out.extend(data.get("files", []))
        if limit is not None and len(out) >= limit:
            return out[:limit]
        page_token = data.get("nextPageToken")
        if not page_token:
            return out
    raise RuntimeError(
        f"Drive listing exceeded {page_limit} pages "
        f"({page_limit * page_size} items) with more pages outstanding "
        "— refusing to treat a partial listing as complete."
    )


def _escape_query_value(value: str) -> str:
    """Escape a value for a Drive search query string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    parents: list[str] = field(default_factory=list)
    modified_time: str | None = None
    size_bytes: int | None = None
    content_hash: str | None = None  # SHA-256 of the file bytes (set by the watcher)


class RateLimiter:
    """Token bucket. Default 1 request/sec — far under the 100/min cap."""

    def __init__(self, rate_per_sec: float = 1.0) -> None:
        self.rate = rate_per_sec
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = 1.0 / self.rate
        wait_for = self._last + gap - now
        if wait_for > 0:
            time.sleep(wait_for)
        self._last = time.monotonic()


class DriveIndexer:
    """Read-only Drive listing. Raises on auth errors; never writes."""

    def __init__(self, rate_per_sec: float = 1.0) -> None:
        self.limiter = RateLimiter(rate_per_sec)

    def _run(self, args: list[str]) -> dict:
        self.limiter.wait()
        proc = subprocess.run(
            [DRIVE_CLI, "drive"] + args,
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Drive CLI failed: {proc.stderr.strip()[:300]}")
        return json.loads(proc.stdout)

    def list_images(self, page_size: int = DRIVE_LIST_MAX_PAGE_SIZE,
                    limit: int | None = None) -> list[DriveFile]:
        """All images in Drive (paginated; ``limit`` caps the total)."""
        files = _fetch_all_pages(self._run, {
            "q": "mimeType contains 'image/' and trashed=false",
            "fields": "files(id,name,mimeType,parents,modifiedTime,size),nextPageToken",
        }, page_size=page_size, limit=limit)
        return [self._to_file(f) for f in files]

    def find_child(self, parent_id: str, name: str,
                   mime_type: str | None = None) -> dict | None:
        """First non-trashed child of ``parent_id`` with this name.

        Read-only (files list). Lets the watcher resolve a target
        folder path without the write channel — see
        execution.resolve_folder(..., create=False).

        A name-equality query puts every match on page 1, so
        limit=1 is complete: if a match exists, the first page
        holds it.
        """
        q = (
            f"'{parent_id}' in parents and "
            f"name = '{_escape_query_value(name)}' and trashed=false"
        )
        if mime_type:
            q += f" and mimeType = '{mime_type}'"
        files = _fetch_all_pages(self._run, {
            "q": q,
            "fields": "files(id,name,mimeType,parents),nextPageToken",
        }, page_size=10, limit=1)
        return files[0] if files else None

    def list_children(self, folder_id: str) -> list[dict]:
        """Non-trashed direct children of a folder (id, name, parents).

        Read-only (files list). All children, not just images — a
        name collision counts whatever the file type is. Paginated
        to the end: a collision on a later page is still seen.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
            raise ValueError(f"invalid Drive folder ID: {folder_id!r}")
        return _fetch_all_pages(self._run, {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "files(id,name,mimeType,parents),nextPageToken",
        }, page_size=DRIVE_LIST_MAX_PAGE_SIZE)

    def list_folder_images(self, folder_id: str,
                           page_size: int = DRIVE_LIST_MAX_PAGE_SIZE,
                           limit: int | None = None) -> list[DriveFile]:
        """Read-only listing of images directly inside one Drive folder.

        ``folder_id`` is a Drive folder ID (letters, digits, ``_`` and ``-``
        only); anything else is rejected so a bad ID cannot break the
        query string. Paginated to the end (or ``limit`` files): a
        folder with more images than one page no longer silently
        drops the rest from the scan.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
            raise ValueError(f"invalid Drive folder ID: {folder_id!r}")
        files = _fetch_all_pages(self._run, {
            "q": f"'{folder_id}' in parents and mimeType contains 'image/' "
                 "and trashed=false",
            "fields": "files(id,name,mimeType,parents,modifiedTime,size),nextPageToken",
        }, page_size=page_size, limit=limit)
        return [self._to_file(f) for f in files]

    def list_unorganized(self, page_size: int = DRIVE_LIST_MAX_PAGE_SIZE,
                         limit: int | None = None) -> list[DriveFile]:
        """Handoff filter: IMG_ / Screenshot / scan_ names, root parents.

        Paginated to the end (or ``limit`` files).
        """
        files = _fetch_all_pages(self._run, {
            "q": "(name contains 'IMG_' or name contains 'Screenshot' "
                 "or name contains 'scan_') and trashed=false",
            "fields": "files(id,name,mimeType,parents,modifiedTime,size),nextPageToken",
        }, page_size=page_size, limit=limit)
        return [self._to_file(f) for f in files]

    def download(self, file_id: str, dest_path: str) -> str:
        """Download a working copy for OCR. The Drive original is untouched."""
        self.limiter.wait()
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        proc = subprocess.run(
            [DRIVE_CLI, "drive", "files", "get", "--params",
             json.dumps({"fileId": file_id, "alt": "media"}),
             "--output", dest_path],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Drive download failed: {proc.stderr.strip()[:300]}")
        return dest_path

    @staticmethod
    def _to_file(raw: dict) -> DriveFile:
        size = raw.get("size")
        return DriveFile(
            file_id=raw["id"],
            name=raw["name"],
            mime_type=raw.get("mimeType", ""),
            parents=raw.get("parents", []),
            modified_time=raw.get("modifiedTime"),
            size_bytes=int(size) if size else None,
        )


def list_local_images(folder: str) -> list[DriveFile]:
    """Local-folder indexer for sample/mock runs."""
    exts = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
    out: list[DriveFile] = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in exts:
            continue
        st = os.stat(path)
        out.append(DriveFile(
            file_id=path,
            name=name,
            mime_type="image/png" if name.lower().endswith(".png") else "image/jpeg",
            modified_time=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            size_bytes=st.st_size,
        ))
    return out
