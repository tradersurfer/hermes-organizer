# DeepSeek review brief — Hermes Organizer v0.1.1

Paste this to DeepSeek along with the repo link. It reviews the code only — no rewrites, no PRs.

## What you're reviewing

- **Repo:** https://github.com/tradersurfer/hermes-organizer — review target is **pinned to commit `5d0aae4e6ebf785e90bafd91fd0ad55a5a96aaf5` (v0.1.1, 47 files)**. Newer commits may exist on `main`; review the pinned commit, not HEAD.
- **What it is:** a Python tool + Hermes plugin that indexes, OCR/vision-classifies, and proposes renames for unstructured Google Drive files — then renames and moves them **only after the owner's per-item approval**, with verification and rollback.
- **Entry points:** `README.md`, `ARCHITECTURE.md`, `PLUGIN_SCOPE.md`, `hermes-plugin/README.md`, then `hermes_organizer/` (core), `hermes-plugin/` (plugin layer + dashboard pane), `tests/` (157 tests claimed).

## Fetch the code — read the files, don't guess from search results

The repo is public. Fetch each file at the pinned commit from `raw.githubusercontent.com` (replace `<path>`):

`https://raw.githubusercontent.com/tradersurfer/hermes-organizer/5d0aae4e6ebf785e90bafd91fd0ad55a5a96aaf5/<path>`

Files to read first:

- `hermes_organizer/execution.py` — hash validation, dry-run vs execute, timeout handling
- `hermes_organizer/naming.py` — dedup, date extraction, target-aware collision logic
- `hermes_organizer/classifier.py` — the classifier (`classify()`, keyword boundaries, filename-first ordering, confidence = best / (best + 3.0))
- `hermes_organizer/taxonomy.py` — the taxonomy rules and keyword lists the classifier scores against; vision input comes from `hermes_organizer/ocr.py`
- `hermes-plugin/dashboard/plugin_api.py` and `hermes-plugin/desktop/plugin.js` — path traversal, write triggers
- `hermes-plugin/plugin.yaml` and `hermes-plugin/dashboard/manifest.json` — requires_env, API declaration
- `tests/test_execution.py` and `tests/test_dashboard.py` — whether assertions are substantive
- `hermes-plugin/requirements.txt`

If a file won't load, say which one and stop — don't invent its contents.

## Context you need — design decisions, don't flag these as bugs

- **Read-only by default.** Execution runs only through the approval flow: proposals file → per-item approval bound by `proposal_hash` (SHA-256 of file_id + proposed_filename + target folder path) → dry-run → execute → verify → rollback. Review entries (no proposed filename) can never be approved. There are **zero `files delete` / `emptyTrash` calls anywhere** in the codebase.
- **Confidence threshold is 0.60, locked, never lowered.** Classification is **keyword-based, not an LLM**. Filename is checked first, extracted text second.
- **Vision is a second opinion**, not the classifier. Default provider is Cloudflare Workers AI (`llama-3.2-11b-vision-instruct`, ~40 Neurons per image, 10,000/day free allowance); fallback is Gemini (`gemini-3.5-flash`, ~10 RPM on the free tier).
- **History that matters:** on 2026-11-13 a build agent ran `--execute` against real Drive with a fabricated approvals file and renamed 2 files. The hash-bound approval flow is the design response to that incident — **test whether it actually holds**, don't assume it does.
- **It has done real work:** 18/18 files in a live Drive folder (Beyond Vision) were scanned, approved, renamed and moved, each verified live. This is not a demo-only tool.
- **Known untested / unfinished areas (pre-catalog work, not yet done):** the dashboard pane has never been loaded in a real Hermes Desktop (checks so far are static plus a mocked backend); Drive auth still runs through a local CLI channel, not Hermes OAuth; vision keys are not yet in `secret_scope`; the dashboard backend (`plugin_api.py`) only loads when the plugin is in the `plugins.enabled` allow-list in `config.yaml` — a separate gate from the desktop opt-in, and the install docs don't yet spell this out. Judge these as gaps, but don't confuse them with bugs in what's built.

## How to review — be critical, not nice

- **No generic praise.** No "overall this is well structured" without a finding attached. If something is good, say exactly which lines and why.
- **For every finding:** severity (Critical / High / Medium / Low), file + line, what breaks or will break, evidence from the code, and a concrete fix.
- **Attack the safety story hardest:** is there ANY path where a rename, move, or delete happens without a matching approval hash? Can a stale approval be replayed against different files? Can the hash be bypassed by editing the proposals file? Does the dry-run check the same things `execute` checks, or does it bless a plan that execute then refuses (or worse, the reverse)?
- **Check the test claims:** 157 tests passing. Do the tests actually assert the right things, or are they passing vacuously? Point at specific weak or tautological tests by name.
- **Check the classifier:** keyword-matching edge cases, filename-first ordering, date handling (dates must never be mistaken for DOB/expiry), the dedup logic — does the target-aware fix (proposals check the destination folder's live filenames) actually cover the collision, or just move it somewhere else?
- **Check the dashboard API** (`hermes-plugin/dashboard/plugin_api.py` + `hermes-plugin/desktop/plugin.js`): path traversal, credential-like request bodies, what the endpoints expose, whether the pane can trigger a write without an approval.
- **Check scale and cost:** what breaks at 1,000 files? Is the Neuron budget math right? How do 429 / 503 / daily-limit errors behave — do they fail safe or silently drop files?
- **Check plugin / catalog readiness:** `plugin.yaml` (version 0.1.1), `requires_env` handling, secret handling, Hermes SDK conformance, Python deps (`pydantic`, `rapidocr-onnxruntime` — not stdlib, so how do they travel on install?).
- **Call out over-engineering and dead code too** — anything that exists for a case that can't happen, or two mechanisms doing one job.

## Output format

1. **Verdict first, one line:** Ship / Fix-first / Rethink — with the reason.
2. **Top 3 things to fix before the catalog PR** (the author is about to submit this to the Hermes plugin catalog for human review).
3. **Ranked findings, Critical → Low.** Each: severity, file + line, what breaks, evidence, fix.
4. **What you checked and found sound** — max 5 bullets, each with a file ref, so we know what you actually read.
5. **Questions for the author** where the code doesn't answer them.

## Rules

- Read the code, not just the README. Quote file paths and line numbers for every claim.
- Don't rewrite code, don't open PRs, don't touch the repo. Review only.
- If you can't verify a claim from the code (e.g. the 18/18 live wave), say so — don't take it on trust.
- If a finding depends on how Hermes Desktop actually behaves (not just the SDK docs), mark it as unverified-by-code and say what would confirm it.
