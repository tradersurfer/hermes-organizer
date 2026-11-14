"""Tests for the Hermes Organizer plugin layer (v0.1.0).

The plugin lives in hermes-plugin/ (hyphenated dir name), so it is
loaded via importlib, not a plain import. No live Drive calls here:
DriveWriter._run is replaced by the in-memory FakeDrive from
test_execution.py, exactly like the core execution tests.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(REPO_ROOT, "hermes-plugin")
MOCK_DIR = os.path.join(REPO_ROOT, "mock_samples")

from test_execution import FakeDrive, _base_files  # noqa: E402
from hermes_organizer import execution as execution_mod  # noqa: E402
from hermes_organizer.execution import DriveWriter  # noqa: E402

TOOLS = [
    "organizer_scan",
    "organizer_propose",
    "organizer_approve",
    "organizer_execute",
    "organizer_verify",
    "organizer_rollback",
    "organizer_report",
]


def _load_plugin():
    """Load the hermes-plugin package (hyphenated dir) via importlib."""
    if "hermes_plugin" in sys.modules:
        return sys.modules["hermes_plugin"]
    spec = importlib.util.spec_from_file_location(
        "hermes_plugin",
        os.path.join(PLUGIN_DIR, "__init__.py"),
        submodule_search_locations=[PLUGIN_DIR],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugin"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin()


@pytest.fixture(scope="module")
def tools(plugin):
    import hermes_plugin.tools as tools_mod

    return tools_mod


def _call(tools_mod, name, args):
    raw = getattr(tools_mod, name)(args)
    return json.loads(raw)


# --- manifest ---

def test_manifest_parses_and_lists_seven_tools():
    with open(os.path.join(PLUGIN_DIR, "plugin.yaml"), encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    assert manifest["name"] == "hermes-organizer"
    assert manifest["version"] == "0.1.0"
    assert manifest["author"] == "tradersurfer93"
    assert manifest["license"] == "MIT"
    assert manifest["requires_hermes"] == ">=0.21.6"
    assert manifest["provides_tools"] == TOOLS


def test_manifest_deps_are_bounded_and_config_defaults():
    with open(os.path.join(PLUGIN_DIR, "plugin.yaml"), encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    # Catalog rule 10: no bare floors — every dep needs an upper bound.
    for dep in manifest["python_dependencies"]:
        assert ">=" in dep and ",<" in dep, f"unbounded dep: {dep}"
    assert any("pydantic" in d for d in manifest["python_dependencies"])
    assert any("rapidocr-onnxruntime" in d
               for d in manifest["python_dependencies"])
    config = manifest["config_schema"]
    assert config["vision_provider"]["default"] == "cloudflare"
    assert config["approval_required"]["default"] is True
    assert config["confidence_threshold"]["default"] == "0.60"
    assert "not user-lowerable" in config["confidence_threshold"]["description"]


def test_register_wires_seven_tools(plugin):
    registered = []

    class FakeCtx:
        def register_tool(self, name, toolset, schema, handler):
            registered.append((name, toolset, schema["name"], handler))

    plugin.register(FakeCtx())
    assert [r[0] for r in registered] == TOOLS
    assert all(r[1] == "hermes-organizer" for r in registered)
    assert all(r[0] == r[2] for r in registered)  # schema name matches tool
    assert all(callable(r[3]) for r in registered)


# --- handlers never raise, always return JSON ---

@pytest.mark.parametrize("bad_args", [None, "", "not json", 42, ["x"]])
def test_handlers_return_valid_json_on_garbage(tools, bad_args):
    for name in TOOLS:
        raw = getattr(tools, name)(bad_args)
        payload = json.loads(raw)  # must parse
        assert payload["status"] == "error"
        assert payload["error"]


def test_handlers_refuse_credential_like_args(tools):
    payload = json.loads(
        tools.organizer_scan({"folder": "/tmp", "api_key": "sk-fake"}))
    assert payload["status"] == "error"
    assert "sk-fake" not in json.dumps(payload)  # value never echoed


# --- scan / propose / approve on mocks (OCR only) ---

def _mock_dir(tmp_path):
    dest = tmp_path / "mocks"
    dest.mkdir()
    for name in ("IMG_0042_receipt.png", "IMG_0043_invoice.png"):
        shutil.copy(os.path.join(MOCK_DIR, name), dest / name)
    return str(dest)


def test_scan_mock_samples_ocr_only(tools, tmp_path):
    folder = _mock_dir(tmp_path)
    payload = _call(tools, "organizer_scan", {
        "folder": folder, "vision": "off",
        "report_path": str(tmp_path / "scan.json"),
    })
    assert payload["status"] == "success"
    assert payload["files_scanned"] == 2
    assert payload["classified"] >= 1
    assert payload["vision_note"].startswith("Vision: off")
    assert os.path.isfile(tmp_path / "scan.json")
    names = {r["metadata"]["original_name"] for r in payload["results"]}
    assert names == {"IMG_0042_receipt.png", "IMG_0043_invoice.png"}


def test_propose_approve_round_trip(tools, tmp_path):
    folder = _mock_dir(tmp_path)
    _call(tools, "organizer_scan", {
        "folder": folder, "vision": "off",
        "report_path": str(tmp_path / "scan.json"),
    })
    proposed = _call(tools, "organizer_propose", {
        "scan_report": str(tmp_path / "scan.json"),
        "proposals_path": str(tmp_path / "proposals.json"),
    })
    assert proposed["status"] == "success"
    assert proposed["entries"] == 2
    with open(tmp_path / "proposals.json", encoding="utf-8") as fh:
        doc = json.load(fh)
    actionable = [e["file_id"] for e in doc["proposals"]
                  if e["proposed_filename"]]
    assert actionable, "expected at least one actionable mock"
    # Approving an unknown ID refuses and writes nothing.
    bad = _call(tools, "organizer_approve", {
        "proposals_path": str(tmp_path / "proposals.json"),
        "approvals_path": str(tmp_path / "approvals.json"),
        "approved_file_ids": ["no-such-id"],
    })
    assert bad["status"] == "error"
    assert not os.path.exists(tmp_path / "approvals.json")
    # Approving the actionable entries works and records provenance.
    ok = _call(tools, "organizer_approve", {
        "proposals_path": str(tmp_path / "proposals.json"),
        "approvals_path": str(tmp_path / "approvals.json"),
        "approved_file_ids": actionable,
        "note": "test approval",
    })
    assert ok["status"] == "success"
    assert ok["approved_count"] == len(actionable)
    with open(tmp_path / "approvals.json", encoding="utf-8") as fh:
        approvals = json.load(fh)
    assert "approval_source" in approvals
    assert approvals["approval_source"]["note"] == "test approval"
    for entry in approvals["proposals"]:
        assert entry["approved"] is (entry["file_id"] in actionable)


# --- execute gates, on the in-memory fake Drive ---

def _fake_drive(monkeypatch, files):
    fake = FakeDrive(files)
    monkeypatch.setattr(DriveWriter, "_run", fake.fake_run)
    return fake


def _scan_and_approve(tools, tmp_path):
    """Scan 2 mocks, propose, approve the actionable ones. Returns
    (proposals_path, approvals_path, actionable_ids)."""
    folder = _mock_dir(tmp_path)
    _call(tools, "organizer_scan", {
        "folder": folder, "vision": "off",
        "report_path": str(tmp_path / "scan.json"),
    })
    _call(tools, "organizer_propose", {
        "scan_report": str(tmp_path / "scan.json"),
        "proposals_path": str(tmp_path / "proposals.json"),
    })
    with open(tmp_path / "proposals.json", encoding="utf-8") as fh:
        doc = json.load(fh)
    actionable = [e for e in doc["proposals"] if e["proposed_filename"]]
    assert actionable
    _call(tools, "organizer_approve", {
        "proposals_path": str(tmp_path / "proposals.json"),
        "approvals_path": str(tmp_path / "approvals.json"),
        "approved_file_ids": [e["file_id"] for e in actionable],
        "note": "test approval",
    })
    return (str(tmp_path / "proposals.json"),
            str(tmp_path / "approvals.json"), actionable)


def test_execute_refuses_without_approvals(tools, tmp_path, monkeypatch):
    proposals_path, _, actionable = _scan_and_approve(tools, tmp_path)
    # Unapproved copy: every entry approved: false.
    with open(proposals_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    unapproved_path = str(tmp_path / "unapproved.json")
    with open(unapproved_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    files = [{
        "id": e["file_id"], "name": e["original_name"],
        "mimeType": "image/png", "parents": ["demo-src"],
    } for e in doc["proposals"]]
    fake = _fake_drive(monkeypatch, files)
    dry = _call(tools, "organizer_execute", {
        "proposals_path": proposals_path,
        "approvals_path": unapproved_path,
        "log_path": str(tmp_path / "log.jsonl"),
    })
    assert dry["status"] == "success"  # dry-run reports, it does not raise
    assert dry["mode"] == "dry-run"
    assert dry["refused"], "unapproved entries must refuse the plan"
    assert fake.writes == []
    refused = _call(tools, "organizer_execute", {
        "proposals_path": proposals_path,
        "approvals_path": unapproved_path,
        "log_path": str(tmp_path / "log.jsonl"),
        "execute": True,
    })
    assert refused["status"] == "error"
    assert fake.writes == []


def test_execute_happy_path_verify_and_rollback(tools, tmp_path, monkeypatch):
    proposals_path, approvals_path, actionable = _scan_and_approve(tools, tmp_path)
    with open(proposals_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    files = [{
        "id": e["file_id"], "name": e["original_name"],
        "mimeType": "image/png", "parents": ["demo-src"],
    } for e in doc["proposals"]]
    fake = _fake_drive(monkeypatch, files)
    log_path = str(tmp_path / "log.jsonl")
    report = _call(tools, "organizer_execute", {
        "proposals_path": proposals_path,
        "approvals_path": approvals_path,
        "log_path": log_path,
        "execute": True,
    })
    assert report["status"] == "success"
    assert report["mode"] == "executed"
    assert len(report["executed"]) == len(actionable)
    assert all(v["ok"] for v in report["verification"])
    assert fake.writes, "execute must have written via the fake"
    assert execution_mod.WRITE_ENABLED is False  # gate restored per call
    verified = _call(tools, "organizer_verify", {"log_path": log_path})
    assert verified["status"] == "success"
    assert verified["verified_ok"] == verified["total"] == len(actionable)
    writes_before = len(fake.writes)
    rolled = _call(tools, "organizer_rollback", {"log_path": log_path})
    assert rolled["status"] == "success"
    assert rolled["mode"] == "dry-run"
    assert len(rolled["would_restore"]) == len(actionable)
    assert len(fake.writes) == writes_before  # rollback dry-run writes nothing
    assert execution_mod.WRITE_ENABLED is False


def test_report_renders_html(tools, tmp_path):
    folder = _mock_dir(tmp_path)
    _call(tools, "organizer_scan", {
        "folder": folder, "vision": "off",
        "report_path": str(tmp_path / "scan.json"),
    })
    out = _call(tools, "organizer_report", {
        "scan_report": str(tmp_path / "scan.json"),
        "image_dir": folder,
        "out_path": str(tmp_path / "report.html"),
    })
    assert out["status"] == "success"
    assert out["files"] == 2
    assert os.path.isfile(tmp_path / "report.html")


# --- plugin hygiene ---

def test_no_delete_or_credential_reads_in_plugin():
    roots = [PLUGIN_DIR]
    for root in roots:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not (path.endswith(".py") and os.path.isfile(path)):
                continue
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            assert "files delete" not in src, f"{path} calls files delete"
            assert '"delete"' not in src, f"{path} has a delete method call"
            assert "emptyTrash" not in src, f"{path} empties trash"
            assert "os.environ" not in src, f"{path} reads env directly"
            assert "api_key=" not in src, f"{path} takes an api key"


def test_demo_flow_runs_clean():
    proc = subprocess.run(
        [sys.executable, os.path.join(PLUGIN_DIR, "demo", "demo_flow.py")],
        capture_output=True, text=True, timeout=300,
        cwd=PLUGIN_DIR,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DEMO OK" in proc.stdout
    assert "0 writes" in proc.stdout  # dry-run step proves zero writes
    assert "no real Drive writes" in proc.stdout
