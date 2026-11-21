"""Tests for the v0.7.0 execution + approval flow.

No live Drive calls here: DriveWriter._run is replaced by an
in-memory fake Drive (files dict + call log). Every test asserts the
write boundary explicitly — dry-run and refused runs make zero
files update/create calls.
"""
import copy
import json
import os
import re
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer import execution as execution_mod  # noqa: E402
from hermes_organizer.execution import (  # noqa: E402
    DriveCliError,
    DriveWriter,
    GateRefused,
)
from hermes_organizer.proposals import (  # noqa: E402
    build_proposals,
    load_approvals,
    load_proposals,
    proposal_hash,
    write_proposals,
)
from hermes_organizer.schemas import (  # noqa: E402
    FileMetadata,
    OrganizerResult,
    VisualClassification,
)

FOLDER_MIME = "application/vnd.google-apps.folder"
SRC_FOLDER = "src-folder"
FILE_ID = "file-1"
ORIGINAL_NAME = "attachment (9).jpg"
PROPOSED_NAME = "2026-11-13_ProductPhoto_image_product.jpg"
TARGET_PATH = "/Work/Product Photos/2026/"


def _entry(**overrides):
    entry = {
        "file_id": FILE_ID,
        "original_name": ORIGINAL_NAME,
        "proposed_filename": PROPOSED_NAME,
        "target_folder_path": TARGET_PATH,
        "subcategory": "Product Photo",
        "confidence": 0.84,
        "proposal_hash": None,
        "approved": False,
    }
    entry.update(overrides)
    entry["proposal_hash"] = proposal_hash(
        entry["file_id"], entry["proposed_filename"],
        entry["target_folder_path"])
    return entry


def _proposals_doc(entries):
    return {"version": 1, "generated_at": "2026-11-13T00:00:00+00:00",
            "source": "test", "proposals": entries}


def _approve(doc):
    approved = copy.deepcopy(doc)
    for entry in approved["proposals"]:
        entry["approved"] = True
    return approved


class FakeDrive:
    """In-memory Drive + call log. Write calls are files update/create."""

    def __init__(self, files):
        self.state = {f["id"]: dict(f) for f in files}
        self.writes: list[tuple] = []
        self.reads = 0
        self._next = 100
        self.tamper_after_gets: int | None = None
        self._gets = 0
        # "applied": update applies to state, then raises a timeout
        # (server applied, response lost). "dropped": raises without
        # applying. None: normal.
        self.timeout_on_update: str | None = None

    def fake_run(self, args):
        method = args[1]
        params = (json.loads(args[args.index("--params") + 1])
                  if "--params" in args else {})
        body = (json.loads(args[args.index("--json") + 1])
                if "--json" in args else {})
        if method in ("create", "update"):
            self.writes.append((method, params, body))
        else:
            self.reads += 1
        if method == "get":
            self._gets += 1
            f = self.state.get(params["fileId"])
            if f is None:
                raise DriveCliError("not found", stderr="404 Not Found")
            out = dict(f)
            if (self.tamper_after_gets is not None
                    and self._gets > self.tamper_after_gets):
                out["name"] = "tampered-externally.jpg"
            return out
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
                            ("id", "name", "mimeType", "parents")
                            if k in f})
            return {"files": out}
        if method == "create":
            new_id = f"new-folder-{self._next}"
            self._next += 1
            self.state[new_id] = {
                "id": new_id, "name": body["name"],
                "mimeType": FOLDER_MIME, "parents": body["parents"],
            }
            return {"id": new_id}
        if method == "update":
            f = self.state[params["fileId"]]
            if self.timeout_on_update == "applied":
                self._apply_update(f, params, body)
                raise execution_mod.DriveTimeoutError(
                    "Drive CLI timed out after 120s (simulated)")
            if self.timeout_on_update == "dropped":
                raise execution_mod.DriveTimeoutError(
                    "Drive CLI timed out after 120s (simulated)")
            self._apply_update(f, params, body)
            return dict(f)
        raise AssertionError(f"unexpected method {method}")

    @staticmethod
    def _apply_update(f, params, body):
        if "name" in body:
            f["name"] = body["name"]
        if "addParents" in params:
            for p in params["addParents"].split(","):
                if p not in f["parents"]:
                    f["parents"].append(p)
        if "removeParents" in params:
            f["parents"] = [p for p in f["parents"]
                            if p not in params["removeParents"].split(",")]


def _base_files():
    return [{
        "id": FILE_ID, "name": ORIGINAL_NAME, "mimeType": "image/jpeg",
        "parents": [SRC_FOLDER],
    }]


def _drive(monkeypatch, files=None):
    fake = FakeDrive(files if files is not None else _base_files())
    monkeypatch.setattr(DriveWriter, "_run", fake.fake_run)
    return fake


def _token():
    """Mint a fresh single-use write token for one execute()/rollback() call."""
    return execution_mod.mint_write_token()


# --- gates ---

def test_execute_refuses_without_execute_flag(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    doc = _proposals_doc([_entry()])
    with pytest.raises(GateRefused, match="--execute"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=False)
    assert fake.writes == []


def test_execute_refuses_without_write_token(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    doc = _proposals_doc([_entry()])
    with pytest.raises(GateRefused, match="write token"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True)
    assert fake.writes == []  # refused before any Drive call


def test_execute_refuses_unapproved_entry(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    doc = _proposals_doc([_entry()])  # approved stays False
    with pytest.raises(GateRefused, match="not approved"):
        execution_mod.execute(doc, doc, DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True, write_token=_token())
    assert fake.writes == []


def test_execute_refuses_on_hash_mismatch(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    proposals = _proposals_doc([_entry()])
    approvals = _approve(proposals)
    approvals["proposals"][0]["proposal_hash"] = "0" * 64  # tampered
    with pytest.raises(GateRefused, match="hash mismatch"):
        execution_mod.execute(proposals, approvals, DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True, write_token=_token())
    assert fake.writes == []


def test_execute_refuses_stale_approval_after_rescan(monkeypatch, tmp_path):
    """Re-scan renames the proposal -> old approval hash no longer matches."""
    fake = _drive(monkeypatch)
    old_approvals = _approve(_proposals_doc([_entry()]))
    rescanned = _proposals_doc([_entry(
        proposed_filename="2026-11-14_ProductPhoto_other_mockup.jpg")])
    rescanned["proposals"][0]["proposal_hash"] = proposal_hash(
        FILE_ID, "2026-11-14_ProductPhoto_other_mockup.jpg", TARGET_PATH)
    with pytest.raises(GateRefused, match="hash mismatch"):
        execution_mod.execute(rescanned, old_approvals, DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True, write_token=_token())
    assert fake.writes == []


def test_execute_refuses_when_target_name_exists(monkeypatch, tmp_path):
    files = _base_files() + [{
        "id": "other-file", "name": PROPOSED_NAME, "mimeType": "image/jpeg",
        "parents": ["target-folder-id"],
    }]
    fake = _drive(monkeypatch, files)
    # Target folder already exists in the fake, holding the same name.
    fake.state["work"] = {"id": "work", "name": "Work",
                          "mimeType": FOLDER_MIME, "parents": ["root"]}
    fake.state["photos"] = {"id": "photos", "name": "Product Photos",
                            "mimeType": FOLDER_MIME, "parents": ["work"]}
    fake.state["target-folder-id"] = {
        "id": "target-folder-id", "name": "2026",
        "mimeType": FOLDER_MIME, "parents": ["photos"]}
    doc = _proposals_doc([_entry()])
    with pytest.raises(GateRefused, match="already exists"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True, write_token=_token())
    assert fake.writes == []


def test_execute_refuses_when_file_state_changed(monkeypatch, tmp_path):
    """File renamed between plan and write: the re-check refuses."""
    fake = _drive(monkeypatch)
    fake.tamper_after_gets = 1  # plan's get is clean; execute's re-get is not
    doc = _proposals_doc([_entry()])
    with pytest.raises(GateRefused, match="changed since the plan"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True, write_token=_token())
    assert fake.writes == []


# --- dry-run ---

def test_dry_run_plan_makes_zero_write_calls(monkeypatch):
    fake = _drive(monkeypatch)
    doc = _proposals_doc([_entry()])
    planned = execution_mod.plan(doc, _approve(doc), DriveWriter())
    assert planned["refused"] == []
    assert len(planned["steps"]) == 1
    step = planned["steps"][0]
    assert step["proposed_filename"] == PROPOSED_NAME
    assert step["target_folder_id"] is None  # folders don't exist yet
    assert planned["folders_to_create"] == ["/Work/"]
    assert fake.writes == []
    assert fake.reads > 0  # reads are fine in a dry-run


def test_plan_reports_review_entries_as_skipped(monkeypatch):
    fake = _drive(monkeypatch)
    review = _entry(file_id="file-2", original_name="mystery.png",
                    proposed_filename=None, proposal_hash=None,
                    subcategory="Requires_Human_Review", confidence=0.4)
    doc = _proposals_doc([_entry(), review])
    planned = execution_mod.plan(doc, _approve(doc), DriveWriter())
    assert planned["refused"] == []
    assert len(planned["steps"]) == 1
    assert len(planned["skipped"]) == 1
    assert planned["skipped"][0]["file_id"] == "file-2"
    assert fake.writes == []


# --- happy path + log + rollback + verify ---

def test_happy_path_renames_moves_and_logs(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _proposals_doc([_entry()])
    report = execution_mod.execute(doc, _approve(doc), DriveWriter(),
                                   log_path, execute=True, write_token=_token())
    assert len(report["executed"]) == 1
    assert len(report["folders_created"]) == 3  # Work, Product Photos, 2026
    assert report["verification"][0]["ok"] is True
    # Fake Drive state: renamed + moved, original parent gone.
    final = fake.state[FILE_ID]
    assert final["name"] == PROPOSED_NAME
    assert final["parents"] == [report["executed"][0]["new_parent_ids"][0]]
    assert SRC_FOLDER not in final["parents"]
    # Log: write-through record with the original state, plus one
    # folder_created audit record per folder created.
    records = execution_mod.load_log(log_path)
    file_records = [r for r in records if "file_id" in r]
    folder_records = [r for r in records if r.get("action") == "folder_created"]
    assert len(file_records) == 1
    assert len(folder_records) == 3
    assert [r["name"] for r in folder_records] == [
        "Work", "Product Photos", "2026"]
    record = file_records[0]
    assert record["file_id"] == FILE_ID
    assert record["original_name"] == ORIGINAL_NAME
    assert record["original_parent_ids"] == [SRC_FOLDER]
    assert record["new_name"] == PROPOSED_NAME
    assert record["proposal_hash"] == proposal_hash(
        FILE_ID, PROPOSED_NAME, TARGET_PATH)
    # 3 folder creates + 1 update.
    assert len(fake.writes) == 4
    assert [w[0] for w in fake.writes] == ["create", "create", "create", "update"]
    update_params, update_body = fake.writes[3][1], fake.writes[3][2]
    assert update_body == {"name": PROPOSED_NAME}
    assert update_params["removeParents"] == SRC_FOLDER


def test_rollback_restores_original_name_and_parents(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _proposals_doc([_entry()])
    execution_mod.execute(doc, _approve(doc), DriveWriter(),
                          log_path, execute=True, write_token=_token())
    writes_after_execute = len(fake.writes)
    report = execution_mod.rollback(log_path, DriveWriter(), execute=True, write_token=_token())
    assert len(report["restored"]) == 1
    assert report["restored"][0]["restored_name"] == ORIGINAL_NAME
    assert report["restored"][0]["restored_parent_ids"] == [SRC_FOLDER]
    final = fake.state[FILE_ID]
    assert final["name"] == ORIGINAL_NAME
    assert final["parents"] == [SRC_FOLDER]
    assert len(fake.writes) == writes_after_execute + 1  # one update to undo


def test_rollback_dry_run_makes_no_writes(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _proposals_doc([_entry()])
    execution_mod.execute(doc, _approve(doc), DriveWriter(),
                          log_path, execute=True, write_token=_token())
    writes_after_execute = len(fake.writes)
    report = execution_mod.rollback(log_path, DriveWriter(), execute=False)
    assert report["refused"]  # dry-run refusal message
    assert len(report["would_restore"]) == 1
    assert report["would_restore"][0]["restore_name"] == ORIGINAL_NAME
    assert len(fake.writes) == writes_after_execute
    assert fake.state[FILE_ID]["name"] == PROPOSED_NAME  # untouched


def test_verification_detects_mismatch(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _proposals_doc([_entry()])
    execution_mod.execute(doc, _approve(doc), DriveWriter(),
                          log_path, execute=True, write_token=_token())
    # Someone renames the file externally after execution.
    fake.state[FILE_ID]["name"] = "someone-else-renamed-me.jpg"
    verification = execution_mod.verify_log(log_path, DriveWriter())
    assert len(verification) == 1
    assert verification[0]["ok"] is False
    assert verification[0]["expected_name"] == PROPOSED_NAME
    assert verification[0]["actual_name"] == "someone-else-renamed-me.jpg"


def test_rollback_reports_missing_file(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _proposals_doc([_entry()])
    execution_mod.execute(doc, _approve(doc), DriveWriter(),
                          log_path, execute=True, write_token=_token())
    del fake.state[FILE_ID]  # file gone from Drive
    report = execution_mod.rollback(log_path, DriveWriter(), execute=True, write_token=_token())
    assert report["restored"] == []
    assert len(report["missing"]) == 1


# --- timeout: the update may have applied server-side ---

def _two_file_docs():
    second = _entry(file_id="file-2", original_name="attachment (8).jpg",
                    proposed_filename="2026-11-12_ProductPhoto_other.jpg")
    return _proposals_doc([_entry(), second])


def _two_files():
    return _base_files() + [{
        "id": "file-2", "name": "attachment (8).jpg",
        "mimeType": "image/jpeg", "parents": [SRC_FOLDER],
    }]


def test_update_timeout_applied_is_logged_and_continues(monkeypatch, tmp_path):
    """Update times out but applied server-side: verify, log, continue."""
    fake = _drive(monkeypatch, _two_files())
    fake.timeout_on_update = "applied"
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _two_file_docs()
    report = execution_mod.execute(doc, _approve(doc), DriveWriter(),
                                   log_path, execute=True, write_token=_token())
    assert len(report["executed"]) == 2
    assert all(v["ok"] for v in report["verification"])
    records = [r for r in execution_mod.load_log(log_path)
               if "file_id" in r]
    assert len(records) == 2
    assert fake.state[FILE_ID]["name"] == PROPOSED_NAME
    assert fake.state["file-2"]["name"] == "2026-11-12_ProductPhoto_other.jpg"


def test_update_timeout_dropped_aborts_run(monkeypatch, tmp_path):
    """Update times out and did NOT apply: abort, log nothing for it."""
    fake = _drive(monkeypatch, _two_files())
    fake.timeout_on_update = "dropped"
    log_path = str(tmp_path / "execution-log.jsonl")
    doc = _two_file_docs()
    with pytest.raises(execution_mod.ExecutionError, match="timed out"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              log_path, execute=True, write_token=_token())
    records = [r for r in execution_mod.load_log(log_path)
               if "file_id" in r] if os.path.exists(log_path) else []
    assert records == []
    assert fake.state[FILE_ID]["name"] == ORIGINAL_NAME  # untouched


def test_run_translates_subprocess_timeout(monkeypatch):
    """The real _run converts TimeoutExpired to DriveTimeoutError."""
    import subprocess

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=120)

    writer = DriveWriter()
    with mock.patch("subprocess.run", slow):
        with pytest.raises(execution_mod.DriveTimeoutError,
                           match="timed out"):
            writer._run(["files", "get", "--params", "{}"])


# --- proposals file ---

def _scan_result():
    return OrganizerResult(
        metadata=FileMetadata(file_id=FILE_ID, original_name=ORIGINAL_NAME,
                              mime_type="image/jpeg"),
        extracted_text="leggings product photo",
        visual_classification=VisualClassification(
            category="Technical & Work Assets", subcategory="Product Photo",
            confidence=0.84, signals=["text:leggings"]),
        proposed_filename=PROPOSED_NAME,
        target_folder_path=TARGET_PATH,
    )


def test_proposals_writer_round_trip(tmp_path):
    path = str(tmp_path / "proposals.json")
    doc = write_proposals([_scan_result()], path, source="test scan")
    assert doc["version"] == 1
    assert len(doc["proposals"]) == 1
    entry = doc["proposals"][0]
    assert entry["approved"] is False
    assert entry["proposal_hash"] == proposal_hash(
        FILE_ID, PROPOSED_NAME, TARGET_PATH)
    loaded = load_proposals(path)
    assert loaded["proposals"][0]["file_id"] == FILE_ID
    # The loaded file approves cleanly against itself.
    from hermes_organizer.proposals import match_approvals
    approvals = copy.deepcopy(loaded)
    approvals["proposals"][0]["approved"] = True
    approved_entries, problems = match_approvals(loaded, approvals)
    assert problems == []
    assert len(approved_entries) == 1


def test_load_approvals_rejects_missing_approved_flag(tmp_path):
    path = str(tmp_path / "approvals.json")
    doc = _proposals_doc([_entry()])
    del doc["proposals"][0]["approved"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    with pytest.raises(ValueError, match="approved"):
        load_approvals(path)


def test_load_proposals_rejects_duplicate_file_id(tmp_path):
    path = str(tmp_path / "proposals.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_proposals_doc([_entry(), _entry()]), fh)
    with pytest.raises(ValueError, match="duplicate"):
        load_proposals(path)


# --- no delete anywhere ---

def test_no_delete_call_anywhere_in_codebase():
    """The executor must never delete: grep the write path for it."""
    roots = [os.path.join(REPO_ROOT, "hermes_organizer"),
             os.path.join(REPO_ROOT, "hermes-plugin"),
             os.path.join(REPO_ROOT, "run_execute.py"),
             os.path.join(REPO_ROOT, "run_scan.py")]
    paths: list[str] = []
    for root in roots:
        if os.path.isdir(root):
            # Recursive: subdirectories count too (dashboard/plugin_api.py
            # lives one level down — a top-level-only listing missed it).
            for dirpath, _, filenames in os.walk(root):
                if "__pycache__" in dirpath:
                    continue
                paths.extend(os.path.join(dirpath, f) for f in filenames
                             if f.endswith(".py"))
        else:
            paths.append(root)
    for path in paths:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            assert "files delete" not in src, f"{path} calls files delete"
            assert '"delete"' not in src, f"{path} has a delete method call"
            assert "emptyTrash" not in src, f"{path} empties trash"
