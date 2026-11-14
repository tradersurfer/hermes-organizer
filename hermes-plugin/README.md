# Hermes Organizer — Hermes Plugin (v0.1.0)

The Organizer as an installable Hermes plugin: index, OCR/vision-classify, and propose renames for unstructured Google Drive files — then rename and move them only after the owner's per-item approval, with verification and rollback.

- **Version:** 0.1.0 — the fresh public start of the plugin. The core tool in the same repo is v0.7.0.
- **Layout:** this `hermes-plugin/` directory is the plugin (manifest, handlers, schemas, skills, demo). The core package `hermes_organizer/` lives beside it in the same repo; the plugin wraps it and re-implements nothing.
- **Flow:** scan → propose → approve → execute → verify, with rollback. Read-only by default; dry-run is the default for every write.
- **Tools (7):** `organizer_scan`, `organizer_propose`, `organizer_approve`, `organizer_execute`, `organizer_verify`, `organizer_rollback`, `organizer_report`.
- **Skills (3):** `organizer-scan`, `organizer-approve-execute`, `organizer-verify-rollback`.
- **Demo:** `demo/demo_flow.py` runs the whole loop on mock samples only — no network, no credentials, no real Drive.
- **Install (local, for now):** copy this directory to `~/.hermes/plugins/hermes-organizer/` and enable it (`hermes plugins enable hermes-organizer`). The public repo and catalog listing come later — see below.

## The gates (read this first)

- **Approval is per item, per scan.** `organizer_approve` records the user's yes for exact proposal entries. A re-scan changes the proposals, the hashes change, and the old approval is refused as stale. Approval binds to ONE scan's proposals — never re-scan between approval and execution.
- **`organizer_execute` is a dry-run unless `execute: true` is passed** — and the agent must only pass that after the user's explicit yes on the exact plan. A casual ack or reaction is never a yes.
- **Nothing is ever deleted.** There is no delete call in the plugin or the core. Rollback restores names and parents; it never removes a file.
- **Confidence threshold is locked at 0.60.** It is shown in `config_schema` for transparency and is not user-lowerable. Files below 0.60 go to human review with no proposed filename.
- **Vision is a second opinion only.** OCR-only scanning works with no credentials at all.

## Configuration

- `vision_provider`: `cloudflare` (default) or `gemini`.
- `approval_required`: bool, default `true`. Do not disable.
- `confidence_threshold`: shown as `"0.60"`, locked — changing it has no effect.
- **Credentials come from the host, never from the plugin or the repo.** The manifest lists `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `GEMINI_API_KEY` under `requires_env` (prompted at install; leave them empty for OCR-only use). The handlers refuse any credential-like value in tool arguments and never echo one.

## Dependencies — decided, not silently bundled

- The plugin layer itself (`tools.py`, `schemas.py`, `__init__.py`) is **stdlib-only**.
- The core's heavy dependencies are declared in `plugin.yaml` as `python_dependencies` with **bounded ranges**, per catalog rule 10 (reviewers ask for the oldest API-compatible floor plus an upper bound): `pydantic>=2.0,<3`, `rapidocr-onnxruntime>=1.3,<2`.
- **Nothing is vendored.** No model weights and no native libraries ship in the repo. What the dependencies bring at install time:
  - `onnxruntime` arrives as a pip wheel (native code through the dependency channel, allowed under catalog rule 10 — it is not a binary shipped in the repo under rule 17).
  - RapidOCR downloads its ONNX model weights on first use from its upstream — a network call to a third-party service, disclosed here per catalog rule 13.
- **Disclosures (catalog rule 13):** network calls to Google Drive (list/get/update/create) and to the vision provider (Cloudflare Workers AI or Gemini); reads outside the plugin's own data (the folders the user asks to scan); shell commands to `hatch_gws_cli` for Drive access in the current core (see the pre-catalog item below); no telemetry, no usage reporting.

## Dashboard — the surface exists (verified 2026-11-14)

The live dashboard Adrian wants — a panel pinned in Hermes Desktop showing organizing in real time, with the approve / verify loop — **is buildable, but it is a second, JavaScript half, not part of the Python plugin.**

- **Source:** the official Desktop Plugin SDK docs — https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk — and the SDK source at `website/docs/developer-guide/desktop-plugin-sdk.md` in `NousResearch/hermes-agent` (main).
- **How it works:** a desktop plugin is a single ESM file (`$HERMES_HOME/desktop-plugins/<id>/plugin.js`) that default-exports a `HermesPlugin` and imports only `@hermes/plugin-sdk`. It registers a pane with `placement: 'left'` (or `'main' | 'right' | 'top' | 'bottom'`) plus a `dock` gesture — `dock: { pane: 'sessions', pos: 'left' }` lands the pane next to sessions, which is the closest match to "pinned top-left by sessions".
- **Two honest limits:** (1) placement is a semantic role — the pane stacks as tabs with existing panes of that role and the user can drag it anywhere afterward, so it is a docked starting position, not a locked position. (2) The desktop half ships off by default and is enabled separately in Capabilities → Plugins.
- **Proven in the wild:** Agent Mixer (Hermes Marketplace) ships `desktop/plugin.js` + `dashboard/plugin_api.py` as one unified package — status pill, dial panel, and a sidebar console page, loading natively into Desktop and the web dashboard.
- **The backend:** the pane talks to the plugin's own backend namespace (`/api/plugins/<id>`) via `ctx.rest` / `ctx.socket`, backed by a `dashboard/plugin_api.py` that reads the same proposals / approvals / execution-log files the Python tools use — never a second source of truth.
- **What ships in v0.1.0:** the Python tools only. The dashboard half (`desktop/plugin.js` + `dashboard/plugin_api.py`, pane docked left of sessions) is the next build step after this plugin lands. The web dashboard has its own, unrelated plugin system and is not the target.

## What remains before the catalog PR

- **Public repo (name still open).** Adrian decided: a NEW fresh public repo with no old history, starting at v0.1. The current private `tradersurfer/hermes-organizer` keeps its history privately. Do not create the remote until Adrian picks the name.
- **Plugin.yaml location — verify.** The docs show a plugin as one directory holding `plugin.yaml` at its root (`~/.hermes/plugins/<name>/plugin.yaml`). This build keeps `plugin.yaml` at `hermes-plugin/plugin.yaml` inside the repo. Before the catalog PR, verify whether `hermes plugins install owner/repo` finds a manifest in a subdirectory, or whether the fresh public repo must put `plugin.yaml` at its root (with the core package beside it).
- **Drive auth — catalog-ready path.** The core reaches Drive through `hatch_gws_cli` today (the local-dev channel). Catalog rule 11 requires credentials through Hermes routes only: declare the Google OAuth client under `oauth:` in `plugin.yaml`, sign in with `hermes auth add`, and use it through Hermes's helper — a plugin never reads another tool's token file. That migration is a pre-catalog item, not part of v0.1.0.
- **Vision credentials — same rule.** Today they resolve from env vars or the host's registered connections. For the catalog they move to `agent.secret_scope.get_secret(NAME)` per rule 11. Also verify the `requires_env` install prompts are skippable for OCR-only users.
- **Catalog submission itself:** regular catalog merge only — a human-reviewed PR to `hermes-agent` with a full 40-char SHA pin. No Typeform, no X demo video. PR description structured like a README, new things in bullet points.
- **After the catalog PR + Nous RFC (in parallel):** MCP server adapter, then GPT actions, then T3 scoping.
