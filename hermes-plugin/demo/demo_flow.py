"""Hermes Organizer plugin — demo flow (v0.1.0).

Runs the whole loop against MOCK samples only:
  scan -> propose -> approve -> dry-run execute -> execute -> verify
  -> rollback (dry-run) -> HTML report.

No network, no credentials, no real Drive. Vision is off (local OCR
only). The Drive is an in-memory fake; the only writes are to a temp
dir this script creates. Run from the plugin root:
  python demo/demo_flow.py

The approval in step 3 is a DEMO fixture — it stands in for the
user's yes so the loop can run end to end. In real use the agent
must only approve after the user's explicit yes on the exact items.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(PLUGIN_DIR)
MOCK_DIR = os.path.join(REPO_ROOT, "mock_samples")
sys.path.insert(0, REPO_ROOT)


def _load_tools():
    """Load the plugin package (the dir name has a hyphen, so plain
    import does not work) and return its tools module."""
    spec = importlib.util.spec_from_file_location(
        "hermes_plugin",
        os.path.join(PLUGIN_DIR, "__init__.py"),
        submodule_search_locations=[PLUGIN_DIR],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugin"] = pkg
    spec.loader.exec_module(pkg)
    import hermes_plugin.tools as tools

    return tools


FOLDER_MIME = "application/vnd.google-apps.folder"


class DemoDrive:
    """In-memory Drive. Write calls are files update/create — counted,
    so the demo can prove the dry-run made zero of them."""

    def __init__(self, files):
        self.state = {f["id"]: dict(f) for f in files}
        self.writes: list[tuple] = []
        self._next = 100

    def fake_run(self, args):
        method = args[1]
        params = (json.loads(args[args.index("--params") + 1])
                  if "--params" in args else {})
        body = (json.loads(args[args.index("--json") + 1])
                if "--json" in args else {})
        if method in ("create", "update"):
            self.writes.append((method, params, body))
        if method == "get":
            f = self.state.get(params["fileId"])
            if f is None:
                raise RuntimeError("404 Not Found")
            return dict(f)
        if method == "list":
            q = params["q"]
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            name_m = re.search(r"name = '([^']*)'", q)
            mime_m = re.search(r"mimeType = '([^']*)'", q)
            out = []
            for f in self.state.values():
                if parent not in f.get("parents", []):
                    continue
                if name_m and f["name"] != name_m.group(1):
                    continue
                if mime_m and f.get("mimeType") != mime_m.group(1):
                    continue
                out.append({k: f[k] for k in
                            ("id", "name", "mimeType", "parents") if k in f})
            return {"files": out}
        if method == "create":
            new_id = f"demo-folder-{self._next}"
            self._next += 1
            self.state[new_id] = {
                "id": new_id, "name": body["name"],
                "mimeType": FOLDER_MIME, "parents": body["parents"],
            }
            return {"id": new_id}
        if method == "update":
            f = self.state[params["fileId"]]
            if "name" in body:
                f["name"] = body["name"]
            if "addParents" in params:
                for p in params["addParents"].split(","):
                    if p not in f["parents"]:
                        f["parents"].append(p)
            if "removeParents" in params:
                f["parents"] = [p for p in f["parents"]
                                if p not in params["removeParents"].split(",")]
            return dict(f)
        raise AssertionError(f"unexpected method {method}")


def _call(tools, fn_name, args):
    """Call a tool handler and parse its JSON. Raises on error status."""
    raw = getattr(tools, fn_name)(args)
    payload = json.loads(raw)
    if payload.get("status") != "success":
        raise RuntimeError(f"{fn_name} failed: {payload.get('error')}")
    return payload


def main() -> int:
    tools = _load_tools()
    from hermes_organizer.execution import DriveWriter  # noqa: E402

    tmp = tempfile.mkdtemp(prefix="hermes-organizer-demo-")
    print("HERMES ORGANIZER PLUGIN — demo flow (mock samples only)")
    print(f"work dir: {tmp}")

    mocks = sorted(f for f in os.listdir(MOCK_DIR) if f.endswith(".png"))
    if not mocks:
        subprocess.run(
            [sys.executable, os.path.join(MOCK_DIR, "generate_mock_samples.py")],
            check=True,
        )
        mocks = sorted(f for f in os.listdir(MOCK_DIR) if f.endswith(".png"))
    print(f"[0] mock samples: {len(mocks)}")

    # [1] scan (local folder, OCR only — vision off, no credentials)
    scan = _call(tools, "organizer_scan", {
        "folder": MOCK_DIR,
        "vision": "off",
        "report_path": os.path.join(tmp, "scan.json"),
    })
    print(f"[1] SCAN: {scan['files_scanned']} files, "
          f"{scan['classified']} classified, "
          f"{scan['flagged_for_review']} flagged — {scan['vision_note']}")
    assert scan["files_scanned"] == len(mocks)

    # [2] propose
    proposed = _call(tools, "organizer_propose", {
        "scan_report": os.path.join(tmp, "scan.json"),
        "proposals_path": os.path.join(tmp, "proposals.json"),
    })
    print(f"[2] PROPOSE: {proposed['entries']} entries -> "
          f"{proposed['proposals_path']}")

    # [3] approve (DEMO fixture approval — stands in for the user's yes)
    with open(os.path.join(tmp, "proposals.json"), encoding="utf-8") as fh:
        proposals_doc = json.load(fh)
    actionable = [e["file_id"] for e in proposals_doc["proposals"]
                  if e["proposed_filename"]]
    approved = _call(tools, "organizer_approve", {
        "proposals_path": os.path.join(tmp, "proposals.json"),
        "approvals_path": os.path.join(tmp, "approvals.json"),
        "approved_file_ids": actionable,
        "note": "DEMO fixture approval — not a real user approval.",
    })
    print(f"[3] APPROVE (demo fixture): {approved['approved_count']} of "
          f"{approved['total_entries']} entries")

    # Fake Drive seeded from the scan: every mock file lives in demo-src.
    files = [{
        "id": e["file_id"], "name": e["original_name"],
        "mimeType": "image/png", "parents": ["demo-src"],
    } for e in proposals_doc["proposals"]]
    fake = DemoDrive(files)
    real_run = DriveWriter._run
    DriveWriter._run = fake.fake_run
    try:
        # [4] dry-run execute — zero writes, plan matches the approval
        planned = _call(tools, "organizer_execute", {
            "proposals_path": os.path.join(tmp, "proposals.json"),
            "approvals_path": os.path.join(tmp, "approvals.json"),
            "log_path": os.path.join(tmp, "execution-log.jsonl"),
        })
        assert planned["mode"] == "dry-run", planned
        assert not planned["refused"], planned["refused"]
        assert len(planned["steps"]) == len(actionable), planned
        assert fake.writes == [], f"dry-run wrote: {fake.writes}"
        print(f"[4] DRY-RUN: {len(planned['steps'])} steps, "
              f"{len(planned['skipped'])} skipped, "
              f"{len(planned['folders_to_create'])} folders to create, "
              "0 writes")

        # [5] execute for real (against the fake only)
        report = _call(tools, "organizer_execute", {
            "proposals_path": os.path.join(tmp, "proposals.json"),
            "approvals_path": os.path.join(tmp, "approvals.json"),
            "log_path": os.path.join(tmp, "execution-log.jsonl"),
            "execute": True,
        })
        assert len(report["executed"]) == len(actionable), report
        assert all(v["ok"] for v in report["verification"]), report
        print(f"[5] EXECUTE: {len(report['executed'])} executed, "
              f"verification {len(report['verification'])}/"
              f"{len(report['verification'])} ok, "
              f"writes: {len(fake.writes)}")

        # [6] verify the log (read-only)
        verified = _call(tools, "organizer_verify", {
            "log_path": os.path.join(tmp, "execution-log.jsonl"),
        })
        assert verified["verified_ok"] == verified["total"] > 0, verified
        print(f"[6] VERIFY: {verified['verified_ok']}/"
              f"{verified['total']} ok")

        # [7] rollback, dry-run only — restores are listed, nothing written
        writes_before = len(fake.writes)
        rolled = _call(tools, "organizer_rollback", {
            "log_path": os.path.join(tmp, "execution-log.jsonl"),
        })
        assert rolled["mode"] == "dry-run", rolled
        assert len(rolled["would_restore"]) == len(actionable), rolled
        assert len(fake.writes) == writes_before
        print(f"[7] ROLLBACK (dry-run): {len(rolled['would_restore'])} "
              "would restore, 0 new writes")

        # [8] HTML report
        rendered = _call(tools, "organizer_report", {
            "scan_report": os.path.join(tmp, "scan.json"),
            "image_dir": MOCK_DIR,
            "out_path": os.path.join(tmp, "report.html"),
        })
        print(f"[8] REPORT: {rendered['files']} files -> "
              f"{rendered['out_path']}")
    finally:
        # Restore the stubbed Drive channel. There is no write flag
        # to reset: write authorization is per-call (minted tokens),
        # so nothing lingers after the demo.
        DriveWriter._run = real_run

    print("DEMO OK — full loop on mocks, no network, no credentials, "
          "no real Drive writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
