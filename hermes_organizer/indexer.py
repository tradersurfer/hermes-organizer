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

    def list_images(self, page_size: int = 20) -> list[DriveFile]:
        params = {
            "q": "mimeType contains 'image/' and trashed=false",
            "pageSize": page_size,
            "fields": "files(id,name,mimeType,parents,modifiedTime,size)",
        }
        data = self._run(["files", "list", "--params", json.dumps(params)])
        return [self._to_file(f) for f in data.get("files", [])]

    def list_folder_images(self, folder_id: str, page_size: int = 50) -> list[DriveFile]:
        """Read-only listing of images directly inside one Drive folder.

        ``folder_id`` is a Drive folder ID (letters, digits, ``_`` and ``-``
        only); anything else is rejected so a bad ID cannot break the
        query string.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
            raise ValueError(f"invalid Drive folder ID: {folder_id!r}")
        params = {
            "q": f"'{folder_id}' in parents and mimeType contains 'image/' "
                 "and trashed=false",
            "pageSize": page_size,
            "fields": "files(id,name,mimeType,parents,modifiedTime,size)",
        }
        data = self._run(["files", "list", "--params", json.dumps(params)])
        return [self._to_file(f) for f in data.get("files", [])]

    def list_unorganized(self, page_size: int = 50) -> list[DriveFile]:
        """Handoff filter: IMG_ / Screenshot / scan_ names, root parents."""
        params = {
            "q": "(name contains 'IMG_' or name contains 'Screenshot' "
                 "or name contains 'scan_') and trashed=false",
            "pageSize": page_size,
            "fields": "files(id,name,mimeType,parents,modifiedTime,size)",
        }
        data = self._run(["files", "list", "--params", json.dumps(params)])
        return [self._to_file(f) for f in data.get("files", [])]

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
