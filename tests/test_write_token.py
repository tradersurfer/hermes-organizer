"""Tests for the per-call write token (Grok review fix, Batch B).

The old module-global WRITE_ENABLED flag is gone: execute() and
rollback() require a fresh single-use token minted by
mint_write_token() alongside execute=True. These tests prove the
three properties the redesign exists for:

- no token (or a spent/forged token) -> refused, zero writes,
  even while a valid token exists somewhere else;
- one token authorizes exactly one call (single-use);
- two concurrent calls hold independent tokens and neither can
  observe the other's authorization (no cross-talk).

No live Drive calls here: DriveWriter._run is replaced by the
in-memory FakeDrive from test_execution.py.
"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_execution import (  # noqa: E402
    FILE_ID,
    ORIGINAL_NAME,
    PROPOSED_NAME,
    TARGET_PATH,
    FakeDrive,
    _approve,
    _base_files,
    _drive,
    _entry,
    _proposals_doc,
)
from hermes_organizer import execution as execution_mod  # noqa: E402
from hermes_organizer.execution import (  # noqa: E402
    DriveWriter,
    GateRefused,
    WriteToken,
    mint_write_token,
)


def _token():
    return mint_write_token()


def test_execute_without_token_refused_even_when_one_exists(monkeypatch,
                                                           tmp_path):
    """A valid token sitting unpassed authorizes nothing."""
    fake = _drive(monkeypatch)
    existing = _token()  # minted but never passed to any call
    assert not existing.spent
    doc = _proposals_doc([_entry()])
    with pytest.raises(GateRefused, match="write token"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              str(tmp_path / "log.jsonl"), execute=True)
    assert fake.writes == []
    assert not existing.spent  # the refusal did not consume it
    # Passing it now works — the token itself was always valid.
    report = execution_mod.execute(doc, _approve(doc), DriveWriter(),
                                   str(tmp_path / "log.jsonl"), execute=True,
                                   write_token=existing)
    assert len(report["executed"]) == 1


def test_token_is_single_use(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    token = _token()
    log_path = str(tmp_path / "log.jsonl")
    doc = _proposals_doc([_entry()])
    report = execution_mod.execute(doc, _approve(doc), DriveWriter(),
                                   log_path, execute=True,
                                   write_token=token)
    assert len(report["executed"]) == 1
    assert token.spent
    with pytest.raises(GateRefused, match="write token"):
        execution_mod.execute(doc, _approve(doc), DriveWriter(),
                              log_path, execute=True, write_token=token)
    assert len(fake.writes) == 4  # no second call wrote anything


def test_rollback_without_token_refused(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "log.jsonl")
    doc = _proposals_doc([_entry()])
    execution_mod.execute(doc, _approve(doc), DriveWriter(),
                          log_path, execute=True, write_token=_token())
    assert fake.state[FILE_ID]["name"] == PROPOSED_NAME
    with pytest.raises(GateRefused, match="write token"):
        execution_mod.rollback(log_path, DriveWriter(), execute=True)
    assert fake.state[FILE_ID]["name"] == PROPOSED_NAME  # untouched
    report = execution_mod.rollback(log_path, DriveWriter(), execute=True,
                                    write_token=_token())
    assert len(report["restored"]) == 1
    assert fake.state[FILE_ID]["name"] == ORIGINAL_NAME


def test_rollback_dry_run_still_needs_no_token(monkeypatch, tmp_path):
    fake = _drive(monkeypatch)
    log_path = str(tmp_path / "log.jsonl")
    doc = _proposals_doc([_entry()])
    execution_mod.execute(doc, _approve(doc), DriveWriter(),
                          log_path, execute=True, write_token=_token())
    writes = len(fake.writes)
    report = execution_mod.rollback(log_path, DriveWriter(), execute=False)
    assert report["refused"]
    assert len(report["would_restore"]) == 1
    assert len(fake.writes) == writes


def test_token_cannot_be_forged():
    with pytest.raises(TypeError):
        WriteToken(object())
    with pytest.raises(TypeError):
        WriteToken()
    token = mint_write_token()
    assert isinstance(token, WriteToken)
    assert not token.spent


def test_concurrent_calls_do_not_share_authorization(monkeypatch, tmp_path):
    """Two threads, two tokens, two files: both succeed, and a
    tokenless call racing them is refused with zero writes."""
    files = _base_files() + [{
        "id": "file-2", "name": "attachment (10).jpg",
        "mimeType": "image/jpeg", "parents": ["src-folder"],
    }]
    fake = _drive(monkeypatch, files)
    # Serialize the fake's _run calls: the in-memory dict is not
    # thread-safe (a list iterating state while another thread
    # creates a folder would raise). The threads still interleave
    # between calls — which is the interleaving under test: the
    # authorization lives in per-call tokens, not shared state.
    lock = threading.Lock()
    real_run = DriveWriter._run

    def locked_run(self, args):
        with lock:
            return real_run(args)

    monkeypatch.setattr(DriveWriter, "_run", locked_run)

    def _doc_for(file_id, original, proposed):
        entry = _entry(file_id=file_id, original_name=original,
                       proposed_filename=proposed)
        return _proposals_doc([entry])

    docs = [
        _doc_for(FILE_ID, ORIGINAL_NAME, PROPOSED_NAME),
        _doc_for("file-2", "attachment (10).jpg",
                 "2026-11-13_ProductPhoto_image_product_2.jpg"),
    ]
    barrier = threading.Barrier(3)
    results = []
    errors = []

    def _run_one(doc):
        barrier.wait(timeout=30)
        try:
            report = execution_mod.execute(
                doc, _approve(doc), DriveWriter(),
                str(tmp_path / "log.jsonl"), execute=True,
                write_token=_token())
            results.append(report)
        except Exception as exc:  # noqa: BLE001 - collected, asserted below
            errors.append(exc)

    def _run_refused(doc):
        barrier.wait(timeout=30)
        try:
            execution_mod.execute(
                doc, _approve(doc), DriveWriter(),
                str(tmp_path / "log.jsonl"), execute=True)
            errors.append(AssertionError("tokenless call was not refused"))
        except GateRefused:
            results.append("refused")

    threads = [
        threading.Thread(target=_run_one, args=(docs[0],)),
        threading.Thread(target=_run_one, args=(docs[1],)),
        threading.Thread(target=_run_refused, args=(docs[0],)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert len(results) == 3
    assert results.count("refused") == 1
    executed = [r for r in results if r != "refused"]
    assert len(executed) == 2
    assert all(len(r["executed"]) == 1 for r in executed)
    assert fake.state[FILE_ID]["name"] == PROPOSED_NAME
    assert fake.state["file-2"]["name"] == \
        "2026-11-13_ProductPhoto_image_product_2.jpg"
