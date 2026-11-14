"""Hermes Organizer — Hermes plugin registration (v0.1.0).

Registers 7 tools and 3 bundled skills. The flow is
scan -> propose -> approve -> execute -> verify, with rollback.
No network calls, no credential reads, no file writes outside the
paths the caller passes in. All renames and moves are flagged for
owner approval by the tools themselves; this plugin does not
auto-approve anything, and organizer_execute is a dry-run unless
the caller passes execute: true after the user's explicit yes.
"""
from __future__ import annotations

import logging

from . import schemas, tools

logger = logging.getLogger(__name__)

_TOOLSET = "hermes-organizer"

_TOOLS = [
    ("organizer_scan", schemas.ORGANIZER_SCAN, tools.organizer_scan),
    ("organizer_propose", schemas.ORGANIZER_PROPOSE, tools.organizer_propose),
    ("organizer_approve", schemas.ORGANIZER_APPROVE, tools.organizer_approve),
    ("organizer_execute", schemas.ORGANIZER_EXECUTE, tools.organizer_execute),
    ("organizer_verify", schemas.ORGANIZER_VERIFY, tools.organizer_verify),
    ("organizer_rollback", schemas.ORGANIZER_ROLLBACK, tools.organizer_rollback),
    ("organizer_report", schemas.ORGANIZER_REPORT, tools.organizer_report),
]


def _apply_install_config(ctx) -> None:
    """Apply install-time config to the tools module, if available.

    Defensive by design: the host's config surface varies, and a
    missing or unreadable config must never break registration.
    Only the two settable keys are applied; confidence_threshold is
    locked at 0.60 in the core and is never read from config.
    """
    try:
        config = None
        get_config = getattr(ctx, "get_config", None)
        if callable(get_config):
            config = get_config()
        if config is None:
            config = getattr(ctx, "config", None)
        if not isinstance(config, dict):
            return
        if isinstance(config.get("vision_provider"), str):
            tools.CONFIG["vision_provider"] = config["vision_provider"]
        if isinstance(config.get("approval_required"), bool):
            tools.CONFIG["approval_required"] = config["approval_required"]
    except Exception:  # noqa: BLE001 - config must never break registration
        logger.debug("Hermes Organizer: install config unreadable; defaults kept.")


def register(ctx) -> None:
    _apply_install_config(ctx)
    for name, schema, handler in _TOOLS:
        ctx.register_tool(name=name, toolset=_TOOLSET, schema=schema, handler=handler)
    logger.info("Hermes Organizer: registered %d tools.", len(_TOOLS))
