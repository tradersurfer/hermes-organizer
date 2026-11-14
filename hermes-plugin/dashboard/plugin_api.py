"""Hermes Organizer dashboard backend (plugin_api.py, v0.1.0).

FastAPI router mounted under ``/api/plugins/hermes-organizer/`` by the
Hermes gateway (see the Desktop Plugin SDK docs: a plugin's
``dashboard/manifest.json`` declares ``{"name": "<id>", "api":
"plugin_api.py"}``, and the pane reaches it through ``ctx.rest`` /
``ctx.socket``). The pane (``desktop/plugin.js``) is the only client.

What this module does:
  - Reads the proposals / approvals / execution-log files the agent
    tools wrote, from one data directory, and serves them as JSON.
  - Approve / plan / execute / rollback / verify go through the
    existing tool handlers (``hermes_plugin.tools``) and the core
    gates (``hermes_organizer/proposals.py`` + ``execution.py``).
    Nothing here re-implements approval or execution logic, and
    nothing here touches Drive directly.

What it never does:
  - Never reads or writes outside the data directory. Every file
    name is resolved inside it; ``..`` and absolute paths are
    refused, mirroring the ``ctx.rest`` traversal rule.
  - Never accepts credential-like values in a request body, and
    never echoes one.
  - Never deletes anything. There is no delete call in this module.

FastAPI is provided by the Hermes gateway at mount time. When it
cannot be imported (local dev / tests), a minimal stand-in records
routes so the module still imports and the handlers stay testable
with plain async calls. The stand-in is not a server — it only
exists so the handlers can be exercised without the gateway.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- FastAPI

try:
    from fastapi import APIRouter, WebSocket  # type: ignore

    _HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - local dev / tests without FastAPI
    _HAVE_FASTAPI = False

    class APIRouter:  # type: ignore[no-redef]
        """Stand-in: records (method, path, handler), serves nothing."""

        def __init__(self) -> None:
            self.routes: list[tuple[str, str, object]] = []

        def _register(self, method: str, path: str):
            def deco(fn):
                self.routes.append((method, path, fn))
                return fn

            return deco

        def get(self, path: str):
            return self._register("GET", path)

        def post(self, path: str):
            return self._register("POST", path)

        def websocket(self, path: str):
            return self._register("WS", path)

    class WebSocket:  # type: ignore[no-redef]
        """Typing placeholder; tests use a fake with the same methods."""


router = APIRouter()

# ---------------------------------------------------------------- paths

_DASHBOARD_DIR = Path(__file__).resolve().parent
_PLUGIN_DIR = _DASHBOARD_DIR.parent
_REPO_ROOT = _PLUGIN_DIR.parent

PLUGIN_ID = "hermes-organizer"


def get_data_dir() -> str:
    """Folder holding proposals / approvals / execution-log files.

    ``ORGANIZER_DATA_DIR`` wins; otherwise the current working
    directory. The agent (or the user, at install) points this at
    the folder the tools write to — in this repo, ``sample_reports/``.
    """
    return os.environ.get("ORGANIZER_DATA_DIR") or os.getcwd()


def _safe_path(name: str) -> str:
    """Resolve ``name`` inside the data directory, or refuse it.

    Mirrors the ``ctx.rest`` traversal rule: no absolute paths, no
    ``..`` segments, and the resolved path must stay inside the data
    directory. Returns the absolute path as a string.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("a file name is required")
    if os.path.isabs(name):
        raise ValueError(f"refused absolute path: {name!r}")
    parts = Path(name).parts
    if ".." in parts:
        raise ValueError(f"refused path outside the data directory: {name!r}")
    root = Path(get_data_dir()).resolve()
    target = (root / name).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"refused path outside the data directory: {name!r}")
    return str(target)


def _list_data_files() -> dict:
    """Proposals / approvals / execution-log files in the data dir."""
    root = Path(get_data_dir())
    if not root.is_dir():
        return {"proposals": [], "approvals": [], "logs": []}
    return {
        "proposals": sorted(p.name for p in root.glob("*proposals*.json")),
        "approvals": sorted(p.name for p in root.glob("*approvals*.json")),
        "logs": sorted(p.name for p in root.glob("execution-log*.jsonl")),
    }


def _latest(files: list[str]) -> str | None:
    """Most recently modified file name, or None when there is none."""
    if not files:
        return None
    root = Path(get_data_dir())
    return max(files, key=lambda n: (root / n).stat().st_mtime).replace("\\", "/")


def _read_json(name: str) -> dict:
    with open(_safe_path(name), encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{name}: expected a JSON object at the top level")
    return doc


def _read_jsonl(name: str) -> list[dict]:
    records: list[dict] = []
    with open(_safe_path(name), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _summarize(doc: dict) -> dict:
    """Counts the pane shows for one proposals/approvals document."""
    entries = doc.get("proposals", []) if isinstance(doc, dict) else []
    actionable = [e for e in entries if e.get("proposed_filename")]
    return {
        "entries": len(entries),
        "actionable": len(actionable),
        "approved": sum(1 for e in actionable if e.get("approved") is True),
        "flagged_for_review": len(entries) - len(actionable),
    }


# ---------------------------------------------------------------- tools

_TOOLS = None
_TOOLS_ERROR: str | None = None


def _load_tools():
    """Load hermes_plugin.tools (hyphenated dir) via importlib.

    Same loader the plugin tests use. Cached after the first load.
    Returns (tools_module, error_string).
    """
    global _TOOLS, _TOOLS_ERROR
    if _TOOLS is not None:
        return _TOOLS, None
    if _TOOLS_ERROR is not None:
        return None, _TOOLS_ERROR
    try:
        if "hermes_plugin" not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                "hermes_plugin",
                _PLUGIN_DIR / "__init__.py",
                submodule_search_locations=[str(_PLUGIN_DIR)],
            )
            pkg = importlib.util.module_from_spec(spec)
            sys.modules["hermes_plugin"] = pkg
            spec.loader.exec_module(pkg)
        import hermes_plugin.tools as tools_mod

        _TOOLS = tools_mod
        return _TOOLS, None
    except Exception as exc:  # noqa: BLE001 - degraded, never crash the API
        _TOOLS_ERROR = (
            "Organizer plugin tools unavailable "
            f"({_PLUGIN_DIR}: {exc}). Install the whole repository, "
            "not the dashboard directory alone."
        )
        return None, _TOOLS_ERROR


_CREDENTIAL_KEY = re.compile(r"api[_-]?key|token|secret|password|passwd|auth", re.I)


def _refuse_credentials(body: dict) -> None:
    """Refuse credential-like keys or values in a request body.

    Same rule as the tool handlers: credentials come from the host,
    never from a request, and are never echoed back.
    """
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    for key, value in body.items():
        if _CREDENTIAL_KEY.search(str(key)):
            raise ValueError(
                f"Refused: field {key!r} looks like a credential. "
                "Pass paths and IDs only."
            )
        if isinstance(value, str) and _CREDENTIAL_KEY.search(value):
            raise ValueError(
                "Refused: a field value looks like a credential. "
                "Pass paths and IDs only."
            )


def _call_tool(name: str, args: dict) -> dict:
    """Call one hermes_plugin.tools handler and parse its JSON."""
    tools_mod, err = _load_tools()
    if tools_mod is None:
        return {"status": "error", "error": err}
    try:
        raw = getattr(tools_mod, name)(args)
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {
            "status": "error", "error": f"{name} returned non-object JSON"}
    except Exception as exc:  # noqa: BLE001 - handlers never raise; be safe
        return {"status": "error", "error": f"{name} failed: {exc}"}


def _broadcast(event: str, payload: dict) -> None:
    """Push a plugin event to connected desktop clients, if possible.

    Uses the documented gateway door (``hermes_cli.plugin_events``);
    outside the gateway the import fails and this is a silent no-op.
    The socket + polling in the pane stay the primary feed either way.
    """
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
    except Exception:
        return
    try:
        broadcast_plugin_event(PLUGIN_ID, event, payload)
    except Exception:
        pass


# ---------------------------------------------------------------- reads


@router.get("/state")
async def get_state() -> dict:
    """Whole dashboard state in one call: file lists + latest docs."""
    files = _list_data_files()
    out: dict = {
        "status": "success",
        "data_dir": get_data_dir(),
        "files": files,
        "latest": {"proposals": None, "approvals": None, "log": None},
    }
    latest = _latest(files["proposals"])
    if latest:
        try:
            doc = _read_json(latest)
            out["latest"]["proposals"] = {
                "file": latest, "summary": _summarize(doc), "doc": doc,
            }
        except (OSError, ValueError) as exc:
            out["latest"]["proposals"] = {"file": latest, "error": str(exc)}
    latest = _latest(files["approvals"])
    if latest:
        try:
            doc = _read_json(latest)
            out["latest"]["approvals"] = {
                "file": latest, "summary": _summarize(doc), "doc": doc,
            }
        except (OSError, ValueError) as exc:
            out["latest"]["approvals"] = {"file": latest, "error": str(exc)}
    latest = _latest(files["logs"])
    if latest:
        try:
            records = _read_jsonl(latest)
            out["latest"]["log"] = {
                "file": latest,
                "records": len(records),
                "executed": sum(1 for r in records if "file_id" in r),
            }
        except (OSError, ValueError) as exc:
            out["latest"]["log"] = {"file": latest, "error": str(exc)}
    return out


@router.get("/files")
async def get_files() -> dict:
    """Names of the proposals / approvals / execution-log files."""
    return {"status": "success", "data_dir": get_data_dir(),
            "files": _list_data_files()}


@router.get("/proposals")
async def get_proposals(file: str) -> dict:
    """One proposals document, by file name inside the data dir."""
    try:
        doc = _read_json(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "success", "file": file,
            "summary": _summarize(doc), "doc": doc}


@router.get("/approvals")
async def get_approvals(file: str) -> dict:
    """One approvals document, by file name inside the data dir."""
    try:
        doc = _read_json(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "success", "file": file,
            "summary": _summarize(doc), "doc": doc}


@router.get("/log")
async def get_log(file: str) -> dict:
    """One execution log (JSONL), by file name inside the data dir."""
    try:
        records = _read_jsonl(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "success", "file": file,
            "records": len(records), "entries": records}


@router.get("/verify")
async def get_verify(log: str) -> dict:
    """Re-fetch every file in an execution log; confirm name + parents.

    Read-only (live Drive reads through the tools handler).
    """
    try:
        log_path = _safe_path(log)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    return _call_tool("organizer_verify", {"log_path": log_path})


# ---------------------------------------------------------------- actions


@router.post("/approve")
async def post_approve(body: dict) -> dict:
    """Record the user's approval for exact proposal entries.

    The pane must only call this after the user approved the exact
    items shown, in the pane. Unlisted entries stay unapproved.
    """
    try:
        _refuse_credentials(body)
        proposals_path = _safe_path(body["proposals_file"])
        approvals_path = _safe_path(body["approvals_file"])
    except (KeyError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    out = _call_tool("organizer_approve", {
        "proposals_path": proposals_path,
        "approvals_path": approvals_path,
        "approved_file_ids": body.get("approved_file_ids") or [],
        "approve_all": bool(body.get("approve_all", False)),
        "note": body.get("note", "approved in the Organizer pane"),
    })
    if out.get("status") == "success":
        _broadcast("approvals.updated",
                   {"approvals_file": body["approvals_file"],
                    "approved_count": out.get("approved_count")})
    return out


@router.post("/plan")
async def post_plan(body: dict) -> dict:
    """Dry-run the approved plan: steps, refusals, skips. Zero writes."""
    try:
        _refuse_credentials(body)
        proposals_path = _safe_path(body["proposals_file"])
        approvals_path = _safe_path(body["approvals_file"])
    except (KeyError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    return _call_tool("organizer_execute", {
        "proposals_path": proposals_path,
        "approvals_path": approvals_path,
        "log_path": (_safe_path(body["log_file"]) if body.get("log_file")
                    else os.path.join(get_data_dir(), "execution-log.jsonl")),
    })


@router.post("/execute")
async def post_execute(body: dict) -> dict:
    """Dry-run by default; executes only when ``execute`` is true.

    The pane must only send ``execute: true`` after the user
    confirmed the exact dry-run plan shown. Every gate in the core
    still applies (approvals + hash match + free target name).
    """
    try:
        _refuse_credentials(body)
        proposals_path = _safe_path(body["proposals_file"])
        approvals_path = _safe_path(body["approvals_file"])
        log_path = (_safe_path(body["log_file"]) if body.get("log_file")
                    else os.path.join(get_data_dir(), "execution-log.jsonl"))
    except (KeyError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    out = _call_tool("organizer_execute", {
        "proposals_path": proposals_path,
        "approvals_path": approvals_path,
        "log_path": log_path,
        "execute": bool(body.get("execute", False)),
    })
    if out.get("status") == "success" and out.get("mode") == "executed":
        _broadcast("execution.updated",
                   {"log_path": log_path,
                    "executed": len(out.get("executed", []))})
    return out


@router.post("/rollback")
async def post_rollback(body: dict) -> dict:
    """Dry-run the restore plan by default; restores on execute: true.

    Never deletes anything; a file missing from Drive is reported
    and skipped.
    """
    try:
        _refuse_credentials(body)
        log_path = _safe_path(body["log_file"])
    except (KeyError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    out = _call_tool("organizer_rollback", {
        "log_path": log_path,
        "execute": bool(body.get("execute", False)),
    })
    if out.get("status") == "success" and out.get("mode") == "executed":
        _broadcast("rollback.updated",
                   {"log_path": log_path,
                    "restored": len(out.get("restored", []))})
    return out


# ---------------------------------------------------------------- live feed


@router.websocket("/events")
async def events(websocket) -> None:
    """Live feed for the pane: a state snapshot on connect, then a
    fresh snapshot whenever the pane asks (``{"refresh": true}``).

    The socket is an accelerator over polling, never a replacement —
    it is a no-op on OAuth remotes, so the pane polls every 5 s
    either way.
    """
    await websocket.accept()
    try:
        await websocket.send_text(json.dumps(
            {"type": "state", "state": await get_state()}))
        while True:
            try:
                raw = await websocket.receive_text()
            except Exception:
                return  # client disconnected (or test fake is done)
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("refresh"):
                await websocket.send_text(json.dumps(
                    {"type": "state", "state": await get_state()}))
    except Exception:
        return
