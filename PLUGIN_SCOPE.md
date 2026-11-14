# Hermes Organizer — Plugin Packaging Scope (v0.7.0 baseline)

**Status:** scope doc, 2026-11-13. Not built yet. The build starts when Adrian confirms the decisions in §8.
**Baseline:** standalone Python CLI (`run_scan.py`, `run_execute.py`), v0.7.0 execution + approval flow built and tested (110/110, uncommitted). Drive read via `hatch_gws_cli`, vision via Cloudflare (default) / Gemini, keyword taxonomy, confidence threshold locked at 0.60.

## 1. Goal

One codebase, usable as a plugin for **any** agent — T3, OpenClaw, Claude Code, OpenAI as a GPT — with the Hermes plugin first, because that's the provable one.

- **Hermes plugin:** the Organizer as an installable Hermes plugin (same shape as CEO Pack: `plugin.yaml` + tools + skills), submitted to the Hermes catalog.
- **RFC / issue to Nous for core:** filed in parallel with the catalog PR, proposing the Organizer as a core Hermes capability. The plugin proves it; the RFC argues it.
- **Multi-agent adapters after:** the same core, thin wrappers per host (see §5).
- **PR description rule (Adrian):** structured and organized like a README/MD, with all new things listed in bullet points.

## 2. Repo decision

Two options. Recommendation: **same repo** — add a `hermes-plugin/` packaging layer inside `tradersurfer/hermes-organizer`. The plugin is a wrapper around the core package, not a fork. Splitting repos means two histories that drift.

- **Same repo (recommended):** one history, one version line (plugin version tracks core, e.g. plugin v0.7.0 = core v0.7.0), the catalog PR points at a path in the same repo, fixes land once.
- **Separate repo:** cleaner plugin boundary, but every core fix has to be ported, and the plugin version can silently fall behind the tool.

**Blocker to decide with this:** the Organizer repo is currently **private**. A Hermes catalog entry needs a **public** repo (catalog entries are submitted by the owner from a public repo — same rule CEO Pack followed). Options: make `tradersurfer/hermes-organizer` public (the repo holds no private business data, prices, or credentials by design), or publish the plugin from a separate public repo. Decide before the catalog PR.

## 3. Hermes plugin layer

Modeled on CEO Pack's proven shape (`plugin.yaml` at the root, `tools.py` handlers that return JSON and never raise, `agents/`, `skills/`, `demo/`).

- **`hermes-plugin/plugin.yaml`:** name `hermes-organizer`, version tracking core, description, author `tradersurfer93`, license MIT, `requires_hermes: ">=0.21.6"`, `provides_tools` (below), `config_schema` (below).
- **Tools (one per CLI capability):**
  - `organizer_scan` — scan a Drive folder (or local folder), classify, propose. Read-only.
  - `organizer_propose` — write the proposals file for a scan.
  - `organizer_approve` — record per-item approvals against the proposals file (hash-checked).
  - `organizer_execute` — rename/move only approved items. Refuses without `--execute`-equivalent approval + hash match.
  - `organizer_verify` — re-fetch executed files, confirm names + parents.
  - `organizer_rollback` — restore original names + parents from an execution log.
  - `organizer_report` — render the HTML scan/verification report.
- **`config_schema` (install-time, no secrets in the plugin):**
  - `vision_provider`: `cloudflare` (default) or `gemini`
  - `approval_required`: bool, default `true` — the read-only + per-folder-yes rule is the default, not an option to silently disable
  - `confidence_threshold`: shown, locked at `0.60` — not user-lowerable
  - Credentials (Drive, Cloudflare, Gemini) come from the host's Secure Vault / registered connections, never from the plugin or the repo.
- **Open design question:** CEO Pack is stdlib-only ("catalog-safe"). The Organizer core depends on pydantic, rapidocr-onnxruntime, and onnxruntime. The plugin layer must decide how those travel — declared install-time dependencies, or a thin wrapper that calls a locally installed Organizer. Decide during the build; do not silently bundle a model.
- **Demo:** a `demo/` flow that runs the whole loop (scan → propose → approve → dry-run execute → verify → rollback) against mock samples, no real Drive, no credentials — same pattern as CEO Pack's `demo_flow.py`.

## 4. Dashboard — Adrian's differentiator

The plugin's edge over "plain Hermes with a prompt" is a **live dashboard pinned in Hermes Desktop** (top-left, by sessions) showing organizing in real time, plus the approve / verify loop.

- **Status: UNVERIFIED.** CEO Pack has no UI surface (tools / skills / agents only). Before promising the dashboard, verify against the Hermes Desktop extension points whether a plugin can pin a live panel. If no such surface exists, the honest fallback is the HTML report (already built) served locally, and the RFC asks Nous for the panel surface as part of core.
- **What the dashboard shows when it exists:** live scan progress, per-file proposals with approve/reject, execution results, verification status, rollback button. It is a view over the same proposals/approvals/execution-log files — never a second source of truth.

## 5. Multi-agent adapters (after the Hermes plugin)

Same core, thin wrappers. Order:

1. **MCP server (first adapter):** a stdio MCP server exposing the same seven tools. Covers Claude Code, OpenClaw, and any MCP host in one move.
2. **OpenAI GPT actions:** an OpenAPI actions schema over the same tools, for use as a GPT. (GPTs cannot hold the approval loop the same way — the approvals file stays the gate.)
3. **T3:** adapter last — T3's plugin surface is the least mapped of the three; scope it once the MCP server exists.

The adapters never re-implement classification, naming, or execution — they call the core package.

## 6. Nous RFC / issue for core

Filed at the same time as the catalog PR. Argues: file organization (index → classify → propose → approve → execute → verify) belongs in core because every agent user has unstructured Drive/local files, and the approval-gated execution pattern (proposals file + hash + verify + rollback) is the safe shape for any agent that writes to user files. Links the catalog PR as the working proof.

## 7. Submission rules (standing, from CEO Pack)

The hackathon is over. All future Nous submissions are **regular catalog merges only** — a human-reviewed catalog PR to `hermes-agent` with a **full 40-char commit SHA pin**. No Typeform, no X demo video for anything submitted to Nous. The PR description follows Adrian's rule: structured like a README, all new things in bullet points.

## 8. Decisions needed from Adrian

**Decided 2026-11-13 ~23:55 (Adrian):**

1. **Repo layout:** same repo — the plugin lives alongside the core in one repo (`hermes-plugin/` layer), not a separate repo.
2. **Public vs private:** a **NEW public repo**, fresh from the public's eyes — no old history. The public repo starts at **v0.1** again. (The current private `tradersurfer/hermes-organizer` keeps its history privately; the public repo is a clean start.) **Open:** the public repo's name — `tradersurfer/hermes-organizer` is taken by the private repo, so it needs a new name or a rename of the private one. Do not create the remote until Adrian picks the name.
3. **Dashboard:** per the recommendation — build the tools first, verify the Hermes Desktop panel surface, dashboard after the check.
4. **Build order:** per the recommendation — Hermes plugin → catalog PR + RFC in parallel → MCP adapter → GPT actions → T3 scoping.

## 9. Build order (after §8 is answered)

1. `hermes-plugin/` layer: `plugin.yaml`, tool handlers wrapping the core, `config_schema`, skills, demo flow on mock samples.
2. Local verification: install the plugin in Hermes, run the demo loop end to end, tests green.
3. Catalog PR (full SHA) + Nous RFC in parallel.
4. MCP server adapter, then GPT actions, then T3 scoping.
