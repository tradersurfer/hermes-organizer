# Hermes Organizer — Hermes Plugin (v0.1.2)

The Organizer as an installable Hermes plugin: index, OCR/vision-classify, and propose renames for unstructured Google Drive files — then rename and move them only after the owner's per-item approval, with verification and rollback.

- **Version:** 0.1.2 — Grok-review fixes (per-call write token, approve_all removed, pagination, optional vision keys). The core tool in the same repo is v0.7.0.
- **Layout:** this `hermes-plugin/` directory is the plugin (manifest, handlers, schemas, skills, demo). The core package `hermes_organizer/` lives beside it in the same repo; the plugin wraps it and re-implements nothing.
- **Flow:** scan → propose → approve → execute → verify, with rollback. Read-only by default; dry-run is the default for every write.
- **Tools (7):** `organizer_scan`, `organizer_propose`, `organizer_approve`, `organizer_execute`, `organizer_verify`, `organizer_rollback`, `organizer_report`.
- **Skills (3):** `organizer-scan`, `organizer-approve-execute`, `organizer-verify-rollback`.
- **Demo:** `demo/demo_flow.py` runs the whole loop on mock samples only — no network, no credentials, no real Drive.
- **Dashboard:** `desktop/plugin.js` + `dashboard/plugin_api.py` + `dashboard/manifest.json` — a live pane docked left of sessions in Hermes Desktop, showing proposals, approvals, execution, verification, and rollback over the same files the tools use.
- **Install (local, for now):** copy this directory to `~/.hermes/plugins/hermes-organizer/` and enable it (`hermes plugins enable hermes-organizer`). The public repo and catalog listing come later — see below.

## The gates (read this first)

- **Approval is per item, per scan.** `organizer_approve` records the user's yes for exact proposal entries. A re-scan changes the proposals, the hashes change, and the old approval is refused as stale. Approval binds to ONE scan's proposals — never re-scan between approval and execution. The approvals file is trusted input: the hash binds an approval to its proposal, but it does not protect against someone who can write the approvals file (they can compute a matching hash) — see ARCHITECTURE.md §6 for the threat model. Approval is always explicit per file ID; there is no approve-all.
- **`organizer_execute` is a dry-run unless `execute: true` is passed** — and the agent must only pass that after the user's explicit yes on the exact plan. A casual ack or reaction is never a yes.
- **Nothing is ever deleted.** There is no delete call in the plugin or the core. Rollback restores names and parents; it never removes a file.
- **Confidence threshold is locked at 0.60.** It is shown in `config_schema` for transparency and is not user-lowerable. Files below 0.60 go to human review with no proposed filename.
- **Vision is a second opinion only.** OCR-only scanning works with no credentials at all.

## Configuration

- `vision_provider`: `cloudflare` (default) or `gemini`.
- `approval_required`: bool, default `true`. Do not disable.
- `confidence_threshold`: shown as `"0.60"`, locked — changing it has no effect.
- **Credentials come from the host, never from the plugin or the repo.** The manifest lists `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `GEMINI_API_KEY` under `optional_env` — vision is optional, OCR-only scanning is a supported configuration with no credentials at all (leave them unset). For the catalog they move to `agent.secret_scope.get_secret(NAME)` per rule 11. The handlers refuse any credential-like value in tool arguments and never echo one.

## Dependencies — decided, not silently bundled

- The plugin layer itself (`tools.py`, `schemas.py`, `__init__.py`) is **stdlib-only**.
- The core's heavy dependencies are declared in `plugin.yaml` as `python_dependencies` with **bounded ranges**, per catalog rule 10 (reviewers ask for the oldest API-compatible floor plus an upper bound): `pydantic>=2.0,<3`, `rapidocr-onnxruntime>=1.3,<2`.
- **Nothing is vendored.** No model weights and no native libraries ship in the repo. What the dependencies bring at install time:
  - `onnxruntime` arrives as a pip wheel (native code through the dependency channel, allowed under catalog rule 10 — it is not a binary shipped in the repo under rule 17).
  - RapidOCR downloads its ONNX model weights on first use from its upstream — a network call to a third-party service, disclosed here per catalog rule 13.
- **Disclosures (catalog rule 13):** network calls to Google Drive (list/get/update/create) and to the vision provider (Cloudflare Workers AI or Gemini); reads outside the plugin's own data (the folders the user asks to scan); shell commands to `hatch_gws_cli` for Drive access in the current core (see the pre-catalog item below); no telemetry, no usage reporting.

## Dashboard — the pane is built (v0.1.0)

The live dashboard Adrian wants — a panel pinned in Hermes Desktop showing organizing in real time, with the approve / verify loop — **is built and ships in this plugin**: `desktop/plugin.js` (the pane) + `dashboard/plugin_api.py` (the backend namespace) + `dashboard/manifest.json`.

- **Source (verified 2026-11-14, re-checked from the primary docs):** the official Desktop Plugin SDK docs — https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk — and the SDK source at `website/docs/developer-guide/desktop-plugin-sdk.md` in `NousResearch/hermes-agent` (main). Everything below was checked against those pages, not taken secondhand.
- **How it works:** a desktop plugin is a single ESM file (`$HERMES_HOME/desktop-plugins/<id>/plugin.js`) that default-exports a `HermesPlugin` and imports only `@hermes/plugin-sdk`. The unified package mode (`$HERMES_HOME/plugins/<id>/desktop/plugin.js` + `dashboard/plugin_api.py`) puts the agent plugin and the desktop pane in one repo — exactly this layout. The pane reaches the backend namespace (`/api/plugins/hermes-organizer/`) via `ctx.rest` / `ctx.socket`; the backend is a FastAPI router (`router = APIRouter()`) declared in `dashboard/manifest.json` as `{"name": "hermes-organizer", "api": "plugin_api.py"}`.
- **Dock:** the pane registers `placement: 'left'` with `dock: { pane: 'sessions', pos: 'left' }` — next to sessions, Adrian's "pinned top-left by sessions". Nuance, verified in the docs: the example in the docs docks `workspace`/`bottom`; `sessions`/`left` is a valid application of the documented ranges (`dock.pane` is any pane id including `sessions`; `dock.pos` is `top | bottom | left | right | center`), not a verbatim docs example.
- **Two honest limits (verified):** (1) placement is a semantic role — the pane stacks as tabs with existing panes of that role and the user can drag it anywhere afterward, so it is a docked starting position, not a locked position. Closing the pane's only tab disables the plugin (re-enable in Capabilities → Plugins). (2) The desktop half ships off by default (`defaultEnabled: false`) and is enabled separately in Capabilities → Plugins; the Python backend is gated separately again by the `plugins.enabled` allow-list in `config.yaml` (a security boundary, GHSA-mcfc-hp25-cjv7). The pane degrades gracefully to a notice when the backend is off.
- **What the pane shows:** live scan progress (via the backend state), per-file proposals with approve/reject checkboxes, the dry-run plan, execution results, verification status per file, and a rollback button (dry-run preview first, then a two-step confirm). It is a view over the proposals / approvals / execution-log files — never a second source of truth.
- **How the backend finds the files:** it serves one data directory — `ORGANIZER_DATA_DIR` if set, else the working directory. Point it at the folder the tools write to (in this repo, `sample_reports/`). All file names are resolved inside that directory; `..` and absolute paths are refused. Approve / execute / rollback go through the existing tool handlers and core gates (per-item approval + hash match); the pane never touches Drive directly and never deletes anything.
- **Live updates:** a socket to the backend namespace, with a 5 s poll as the fallback — the socket is a no-op on OAuth remotes, so polling is the feed there. (Future improvement: the backend can push via `broadcast_plugin_event` after approve/execute; the pane already listens on the socket and reloads on any message.)
- **What the pane does NOT do:** no JSX (it loads uncompiled — `jsx()`/`jsxs()` only), no imports beyond `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` (the loader's allowlist), no `eval`, no prototype patching, no reaching into app internals, no hardcoded colors (theme variables only). An automated test (`tests/test_dashboard.py`) greps for all of this, and `node --check` validates the syntax.
- **Enable it (local dev):** link or copy this repo's `hermes-plugin/` into `~/.hermes/plugins/hermes-organizer/` (linked package: `ln -s <repo>/hermes-plugin ~/.hermes/plugins/hermes-organizer` — a linked package's desktop copy hot-reloads on save), set `ORGANIZER_DATA_DIR` to the folder with your proposals files, enable the plugin in Capabilities → Plugins, add `hermes-organizer` to `plugins.enabled` in `config.yaml`, restart the gateway.
- **Reference shape:** the unified package layout above is the documented shape in the SDK docs; the reference demos live in the companion `hermes-example-plugins` repo. (An earlier build note named Agent Mixer as a shipped example of this layout — not re-verified here, so it is not claimed as proof.)

## What remains before the catalog PR

- **Public repo (name still open).** Adrian decided: a NEW fresh public repo with no old history, starting at v0.1. The current private `tradersurfer/hermes-organizer` keeps its history privately. Do not create the remote until Adrian picks the name.
- **Dashboard — BUILT in v0.1.0** (`desktop/plugin.js` + `dashboard/plugin_api.py` + `dashboard/manifest.json`, 20 tests in `tests/test_dashboard.py`). Still to do before the catalog PR: load the unified package in Hermes Desktop once and run the pane against the real backend — the pane is verified by static checks + `node --check` + the mocked API harness, not yet by a live Desktop load.
- **Plugin.yaml location — VERIFIED 2026-11-14.** A manifest in a subdirectory is supported: catalog entries carry a `subdir` field ("Path to the plugin inside the repo for monorepos", default repo root), and direct installs accept `owner/repo/path/to/plugin` (which downloads only that folder's files). So `hermes-plugin/plugin.yaml` inside the fresh public repo is fine — the catalog entry declares `subdir: hermes-plugin`. No need to flatten `plugin.yaml` to the repo root. Sources: the Plugin Catalog docs (`/docs/user-guide/features/plugin-catalog`) and the Plugins docs (`/docs/user-guide/features/plugins`).
- **Drive auth — catalog-ready path.** The core reaches Drive through `hatch_gws_cli` today (the local-dev channel). Catalog rule 11 requires credentials through Hermes routes only: declare the Google OAuth client under `oauth:` in `plugin.yaml`, sign in with `hermes auth add`, and use it through Hermes's helper — a plugin never reads another tool's token file. That migration is a pre-catalog item, not part of v0.1.0.
- **Vision credentials — same rule.** Today they resolve from env vars or the host's registered connections. For the catalog they move to `agent.secret_scope.get_secret(NAME)` per rule 11. Vision keys are declared under `optional_env` in the manifest (not `requires_env`), so install never requires them and OCR-only is a supported configuration.
- **Catalog submission itself:** regular catalog merge only — a human-reviewed PR to `hermes-agent` with a full 40-char SHA pin. No Typeform, no X demo video. PR description structured like a README, new things in bullet points.
- **After the catalog PR + Nous RFC (in parallel):** MCP server adapter, then GPT actions, then T3 scoping.
