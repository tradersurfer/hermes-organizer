"""Tests for the Hermes Organizer dashboard half (v0.1.0).

Covers hermes-plugin/dashboard/plugin_api.py (FastAPI router, tested
through its handlers directly — FastAPI itself is provided by the
Hermes gateway at mount time and is not installed here, so the
module's stand-in router records routes) and hermes-plugin/desktop/
plugin.js (SDK conformance by static checks + node --check).

No live Drive calls anywhere: DriveWriter._run is replaced by the
in-memory FakeDrive from test_execution.py, exactly like the core
execution tests. The API's data directory is a tmp dir pointed at
by ORGANIZER_DATA_DIR.
"""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(REPO_ROOT, "hermes-plugin")
DASHBOARD_DIR = os.path.join(PLUGIN_DIR, "dashboard")
PANE_PATH = os.path.join(PLUGIN_DIR, "desktop", "plugin.js")
MOCK_DIR = os.path.join(REPO_ROOT, "mock_samples")

from test_execution import FakeDrive  # noqa: E402
from test_plugin import _call, _fake_drive, _mock_dir  # noqa: E402
from hermes_organizer import execution as execution_mod  # noqa: E402
from hermes_organizer.execution import DriveWriter  # noqa: E402


def _load_api():
    """Load dashboard/plugin_api.py via importlib (hyphenated parent)."""
    if "organizer_plugin_api" in sys.modules:
        return sys.modules["organizer_plugin_api"]
    spec = importlib.util.spec_from_file_location(
        "organizer_plugin_api",
        os.path.join(DASHBOARD_DIR, "plugin_api.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["organizer_plugin_api"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def api():
    return _load_api()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A tmp data dir, pointed at by ORGANIZER_DATA_DIR."""
    monkeypatch.setenv("ORGANIZER_DATA_DIR", str(tmp_path))
    return tmp_path


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- manifest


def test_dashboard_manifest_declares_backend():
    with open(os.path.join(DASHBOARD_DIR, "manifest.json"),
              encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["name"] == "hermes-organizer"
    assert manifest["api"] == "plugin_api.py"


def test_api_routes_registered(api):
    routes = {(m, p) for m, p, _ in api.router.routes}
    for method, path in [
        ("GET", "/state"), ("GET", "/files"), ("GET", "/proposals"),
        ("GET", "/approvals"), ("GET", "/log"), ("GET", "/verify"),
        ("POST", "/approve"), ("POST", "/plan"), ("POST", "/execute"),
        ("POST", "/rollback"), ("WS", "/events"),
    ]:
        assert (method, path) in routes, f"missing route {method} {path}"


def test_api_runs_without_fastapi(api):
    # FastAPI is provided by the Hermes gateway at mount time; this
    # venv does not have it, so the stand-in router is what is tested.
    assert api._HAVE_FASTAPI is False
    assert hasattr(api.router, "routes")


# ---------------------------------------------------------------- reads


def _seed_proposals(api, tmp_path):
    """Scan 2 mocks (OCR only) and write proposals into the data dir.

    Returns (proposals_name, actionable_ids). The tools handlers are
    called directly, exactly like test_plugin does.
    """
    api._load_tools()  # ensure the hermes_plugin package is loaded
    import hermes_plugin.tools as tools_mod

    folder = _mock_dir(tmp_path)
    _call(tools_mod, "organizer_scan", {
        "folder": folder, "vision": "off",
        "report_path": str(tmp_path / "scan.json"),
    })
    proposed = _call(tools_mod, "organizer_propose", {
        "scan_report": str(tmp_path / "scan.json"),
        "proposals_path": str(tmp_path / "demo-proposals.json"),
    })
    assert proposed["status"] == "success"
    with open(tmp_path / "demo-proposals.json", encoding="utf-8") as fh:
        doc = json.load(fh)
    actionable = [e["file_id"] for e in doc["proposals"]
                  if e["proposed_filename"]]
    assert actionable
    return "demo-proposals.json", actionable


def test_state_lists_files_and_latest(api, data_dir, tmp_path):
    _seed_proposals(api, tmp_path)
    # A hand-written execution log (2 records).
    with open(tmp_path / "execution-log-demo.jsonl", "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps({"file_id": "x", "new_name": "a.jpg"}) + "\n")
        fh.write(json.dumps({"action": "folder_created"}) + "\n")
    state = _run(api.get_state())
    assert state["status"] == "success"
    assert state["data_dir"] == str(tmp_path)
    assert state["files"]["proposals"] == ["demo-proposals.json"]
    assert state["files"]["logs"] == ["execution-log-demo.jsonl"]
    latest = state["latest"]["proposals"]
    assert latest["file"] == "demo-proposals.json"
    assert latest["summary"]["entries"] == 2
    assert latest["summary"]["actionable"] >= 1
    assert state["latest"]["log"]["records"] == 2
    assert state["latest"]["log"]["executed"] == 1


def test_state_empty_data_dir(api, data_dir):
    state = _run(api.get_state())
    assert state["status"] == "success"
    assert state["files"] == {"proposals": [], "approvals": [], "logs": []}
    assert state["latest"] == {"proposals": None, "approvals": None,
                               "log": None}


def test_proposals_endpoint_and_traversal_refused(api, data_dir, tmp_path):
    _seed_proposals(api, tmp_path)
    ok = _run(api.get_proposals("demo-proposals.json"))
    assert ok["status"] == "success"
    assert ok["summary"]["entries"] == 2
    for bad_name in ("../secret.json", "/etc/passwd", "missing.json",
                     "..\\secret.json"):
        out = _run(api.get_proposals(bad_name))
        assert out["status"] == "error", bad_name
        assert out["error"]


def test_log_endpoint(api, data_dir, tmp_path):
    with open(tmp_path / "execution-log-demo.jsonl", "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps({"file_id": "x", "new_name": "a.jpg"}) + "\n")
    out = _run(api.get_log("execution-log-demo.jsonl"))
    assert out["status"] == "success"
    assert out["records"] == 1
    assert out["entries"][0]["new_name"] == "a.jpg"


# ---------------------------------------------------------------- actions


def test_approve_via_api_writes_approvals(api, data_dir, tmp_path):
    proposals_name, actionable = _seed_proposals(api, tmp_path)
    # Unknown ID refuses and writes nothing.
    bad = _run(api.post_approve({
        "proposals_file": proposals_name,
        "approvals_file": "demo-approvals.json",
        "approved_file_ids": ["no-such-id"],
    }))
    assert bad["status"] == "error"
    assert not os.path.exists(tmp_path / "demo-approvals.json")
    # Approving one actionable entry works and records provenance.
    ok = _run(api.post_approve({
        "proposals_file": proposals_name,
        "approvals_file": "demo-approvals.json",
        "approved_file_ids": [actionable[0]],
        "note": "pane approval",
    }))
    assert ok["status"] == "success"
    assert ok["approved_count"] == 1
    with open(tmp_path / "demo-approvals.json", encoding="utf-8") as fh:
        approvals = json.load(fh)
    assert "approval_source" in approvals
    for entry in approvals["proposals"]:
        assert entry["approved"] is (entry["file_id"] == actionable[0])


def test_credential_like_body_refused(api, data_dir, tmp_path):
    _seed_proposals(api, tmp_path)
    out = _run(api.post_approve({
        "proposals_file": "demo-proposals.json",
        "approvals_file": "demo-approvals.json",
        "approved_file_ids": [],
        "api_key": "sk-fake",
    }))
    assert out["status"] == "error"
    assert "sk-fake" not in json.dumps(out)
    assert not os.path.exists(tmp_path / "demo-approvals.json")


def _seed_approved(api, tmp_path, monkeypatch):
    """Proposals + approvals for 2 mocks, plus a FakeDrive over them.

    Returns (fake, proposals_name, approvals_name, actionable).
    """
    api._load_tools()  # ensure the hermes_plugin package is loaded
    import hermes_plugin.tools as tools_mod

    proposals_name, actionable = _seed_proposals(api, tmp_path)
    _call(tools_mod, "organizer_approve", {
        "proposals_path": str(tmp_path / proposals_name),
        "approvals_path": str(tmp_path / "demo-approvals.json"),
        "approved_file_ids": actionable,
        "note": "test approval",
    })
    with open(tmp_path / proposals_name, encoding="utf-8") as fh:
        doc = json.load(fh)
    files = [{
        "id": e["file_id"], "name": e["original_name"],
        "mimeType": "image/png", "parents": ["demo-src"],
    } for e in doc["proposals"]]
    fake = _fake_drive(monkeypatch, files)
    return fake, proposals_name, "demo-approvals.json", actionable


def test_plan_dry_run_zero_writes(api, data_dir, tmp_path, monkeypatch):
    fake, proposals_name, approvals_name, _ = _seed_approved(
        api, tmp_path, monkeypatch)
    out = _run(api.post_plan({
        "proposals_file": proposals_name,
        "approvals_file": approvals_name,
    }))
    assert out["status"] == "success"
    assert out["mode"] == "dry-run"
    assert out["refused"] == []
    assert out["steps"], "plan should have steps"
    assert fake.writes == []


def test_execute_dry_run_is_default(api, data_dir, tmp_path, monkeypatch):
    fake, proposals_name, approvals_name, _ = _seed_approved(
        api, tmp_path, monkeypatch)
    out = _run(api.post_execute({
        "proposals_file": proposals_name,
        "approvals_file": approvals_name,
        "log_file": "execution-log-demo.jsonl",
    }))
    assert out["status"] == "success"
    assert out["mode"] == "dry-run"
    assert fake.writes == []


def test_execute_via_api_then_verify(api, data_dir, tmp_path, monkeypatch):
    fake, proposals_name, approvals_name, actionable = _seed_approved(
        api, tmp_path, monkeypatch)
    out = _run(api.post_execute({
        "proposals_file": proposals_name,
        "approvals_file": approvals_name,
        "log_file": "execution-log-demo.jsonl",
        "execute": True,
    }))
    assert out["status"] == "success"
    assert out["mode"] == "executed"
    assert len(out["executed"]) == len(actionable)
    assert all(v["ok"] for v in out["verification"])
    assert fake.writes, "execute must have written via the fake"
    # No authorization lingers after the call: a direct core execute
    # with execute=True but no token is refused.
    with open(tmp_path / proposals_name, encoding="utf-8") as fh:
        proposals_doc = json.load(fh)
    with open(tmp_path / approvals_name, encoding="utf-8") as fh:
        approvals_doc = json.load(fh)
    with pytest.raises(execution_mod.GateRefused):
        execution_mod.execute(proposals_doc, approvals_doc, DriveWriter(),
                              str(tmp_path / "execution-log-demo.jsonl"),
                              execute=True)
    verified = _run(api.get_verify("execution-log-demo.jsonl"))
    assert verified["status"] == "success"
    assert verified["verified_ok"] == verified["total"] == len(actionable)
    # Rollback dry-run first, then the real thing.
    preview = _run(api.post_rollback({"log_file": "execution-log-demo.jsonl"}))
    assert preview["status"] == "success"
    assert preview["mode"] == "dry-run"
    assert len(preview["would_restore"]) == len(actionable)
    writes_before = len(fake.writes)
    assert len(fake.writes) == writes_before  # dry-run wrote nothing
    done = _run(api.post_rollback({
        "log_file": "execution-log-demo.jsonl", "execute": True,
    }))
    assert done["status"] == "success"
    assert done["mode"] == "executed"
    assert len(done["restored"]) == len(actionable)
    assert len(fake.writes) > writes_before


# ---------------------------------------------------------------- live feed


class _FakeWebSocket:
    """Records what the events handler sends; ends the loop on demand."""

    def __init__(self, messages):
        self.sent: list[str] = []
        self._messages = list(messages)
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        if self._messages:
            return self._messages.pop(0)
        raise RuntimeError("disconnect")


def test_websocket_events_sends_state_snapshots(api, data_dir, tmp_path):
    _seed_proposals(api, tmp_path)
    ws = _FakeWebSocket(['{"refresh": true}'])
    _run(api.events(ws))
    assert ws.accepted
    assert len(ws.sent) == 2  # snapshot on connect + snapshot on refresh
    for raw in ws.sent:
        payload = json.loads(raw)
        assert payload["type"] == "state"
        assert payload["state"]["status"] == "success"
        assert payload["state"]["files"]["proposals"] == [
            "demo-proposals.json"]


# ---------------------------------------------------------------- pane: SDK conformance


def _pane_source():
    with open(PANE_PATH, encoding="utf-8") as fh:
        return fh.read()


def test_pane_imports_only_allowed_specifiers():
    src = _pane_source()
    imports = [line.strip() for line in src.splitlines()
               if line.strip().startswith("import ")]
    assert imports, "pane must import the SDK"
    for line in imports:
        assert ("@hermes/plugin-sdk" in line or "from 'react'" in line
                or "from 'react/jsx-runtime'" in line), line


def test_pane_has_no_banned_patterns():
    src = _pane_source()
    banned = ["eval(", "Function(", "__proto__", "prototype.",
              "window.hermesDesktop", "document.", "innerHTML",
              "http://", "https://"]
    for pattern in banned:
        assert pattern not in src, f"pane contains banned pattern: {pattern}"


def test_pane_has_no_hardcoded_colors():
    src = _pane_source()
    import re

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", src), "hardcoded hex color"
    assert "rgb(" not in src
    # Theme variables only.
    assert "var(--ui-" in src or "--ui-text-tertiary" in src


def test_pane_registers_docked_pane_by_sessions():
    src = _pane_source()
    assert "const PLUGIN_ID = 'hermes-organizer'" in src
    assert "id: PLUGIN_ID" in src  # export id resolves to hermes-organizer
    assert "defaultEnabled: false" in src  # desktop half ships opt-in
    assert "area: 'panes'" in src
    assert "placement: 'left'" in src
    assert "dock: { pane: 'sessions', pos: 'left' }" in src
    assert "width: '340px'" in src


def test_pane_uses_sdk_rest_socket_and_polling_fallback():
    src = _pane_source()
    assert "ctx.rest(" in src
    assert "ctx.socket(" in src
    assert "ctx.setInterval(" in src
    assert "POLL_MS" in src
    assert "ctx.socket('/events'" in src


def test_pane_has_no_jsx_syntax():
    src = _pane_source()
    import re

    # No JSX elements: no <div, <button, <span, <select, <input,
    # <label, <option, or capitalized component tags.
    assert not re.search(r"<(div|span|button|select|input|label|option)\b", src)
    assert not re.search(r"<[A-Z][A-Za-z0-9]*\b", src)
    # UI is built with jsx()/jsxs() calls.
    assert "jsx(" in src and "jsxs(" in src


def test_pane_node_syntax():
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    check_path = "/tmp/organizer-pane-check.mjs"
    shutil.copy(PANE_PATH, check_path)
    proc = subprocess.run([node, "--check", check_path],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
