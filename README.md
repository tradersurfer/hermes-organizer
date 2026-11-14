# Hermes Organizer — Autonomous File & Media Organizer Agent (v0.1)

Read-only prototype. It scans a small sample of Google Drive files (or a
local folder), runs OCR + taxonomy classification, and proposes new filenames
and target folders with confidence scores. **Nothing is renamed, moved, or
deleted.** Proposals only.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Scan the included real-Drive sample (6 files, already downloaded)
.venv/bin/python run_scan.py --dir samples/drive --report sample_reports/scan-2026-11-11.json

# Or scan any local folder of images/PDFs
.venv/bin/python run_scan.py --dir /path/to/folder --report out.json

# Regenerate the generic mock samples (receipt, whiteboard, screenshot, invoice, contract)
.venv/bin/python mock_samples/generate_mock_samples.py
.venv/bin/python run_scan.py --dir mock_samples --report sample_reports/mock-scan.json
```

## Layout

```
hermes_organizer/
  schemas.py      Pydantic models (FileMetadata, VisualClassification, OrganizerResult)
  indexer.py      Drive API v3 read-only listing (rate-limited) + local-dir indexer
  ocr.py          RapidOCR wrapper (local, no key) + Gemini vision bridge (v0.3)
  classifier.py   Keyword/regex taxonomy scoring over OCR text + filename signals
  taxonomy.py     Category/subcategory rules, folder map, confidence threshold
  naming.py       Proposed filename + target folder builder
  audit.py        JSONL audit log (proposals only in v0.1)
  watcher.py      drive_watcher skeleton: poll/filter/inspect/resolve/execute loop
  execution.py    STUB — refuses to run in v0.1 (write mode is gated)
  prompts.py      Classifier system prompt (Pydantic output parsing)
taxonomy_rules.json   The taxonomy ruleset
run_scan.py       CLI entry point
tests/            pytest suite (taxonomy, naming, classifier)
```

## How classification works (v0.1)

1. OCR extracts visible text with local RapidOCR.
2. The classifier scores each subcategory by weighted keyword hits in the
   OCR text plus filename signals (`IMG_`, `Screenshot`, `scan`, `invoice`…).
3. The top score becomes the confidence. Under **0.60** the file goes to
   `Requires_Human_Review` with no rename proposed.
4. `naming.py` builds `YYYY-MM-DD_<Subcategory>_<Topic>.ext` (date from OCR
   text first, else file modified time) and the target folder from the
   taxonomy (e.g. `/Finance/Receipts/2026/`).

## What is stubbed / what needs keys

- **Vision model bridge** (`ocr.py:VisionBridge`): WIRED in v0.3 to the
  Google Gemini API free tier (see the v0.3 section below). Local RapidOCR
  still covers text-heavy files on its own; vision is a second opinion for
  files OCR leaves in review.
- **Drive `changes.watch` webhook** (`watcher.py`): skeleton only. Polling
  via `files list` works today; the push webhook needs a registered endpoint.
- **Execution** (`execution.py`): stub that refuses. Enabling writes needs
  Drive `drive.file`/`drive` scope (currently readonly) and Adrian's
  explicit yes per folder.
- **PDF rendering**: `pdf2image` + poppler not bundled; PDF files are
  indexed but skipped for OCR in v0.1 unless poppler is installed.

## Safety

- v0.1 makes **zero** Drive write calls. `grep -r "files update" hermes_organizer/`
  returns only the stub's refusal message and comments.
- The sample run downloads local working copies of 6 real Drive files for
  classification; the originals are untouched.

## v0.2 (2026-11-12) — read-only improvement pass

Same boundary as v0.1: scan, classify, propose. `execution.py` is still a
gated stub (`WRITE_ENABLED=False`), no Drive write calls anywhere.

- **Boundary-aware keyword matching** (`classifier.py`): short keywords
  (len ≤ 6) now match on word boundaries with an optional plural, so "tax"
  no longer fires on "taxi", "paid" not on "unpaid", "irs" not on "first",
  "api" not on "capital", "deed" not on "indeed", "git" not on "digital".
  Phrases match as substrings; long words match as substrings (OCR jams
  words together). One pre-compiled matcher serves both scoring and the
  signals list, so signals never lie. Filename signals unchanged.
- **CamelCase splitting** (`classifier.py:clean_text`): "YourCreditScores"
  → "Your Credit Scores", "ValueError" → "Value Error", before scoring,
  signals, and naming. Matching runs against BOTH the raw and the split
  text (union), so the split only ever adds signal — "TransUnion" still
  matches "transunion" on the raw text. Credit-report confidence went
  0.786/0.727 → 0.79/0.79 after this fix (v0.1 values kept or better).
- **best_guess on review results**: a below-threshold result now carries
  the top-scoring subcategory as `best_guess` (None only when nothing
  scored). Classified results keep `best_guess=None`.
- **Content-hash dedup**: SHA-256 per file (`DriveFile`/`FileMetadata`
  `content_hash`), repeats flagged with `OrganizerResult.duplicate_of`
  pointing at the first file's name. Duplicates are still classified
  normally — flagged only, never deleted or skipped. Hashes also appear in
  the audit log.
- **DOB/expiry-safe date extraction** (`naming.py`): `extract_date` now
  considers every date in the text and skips ones preceded by "date of
  birth", "dob", "expires", "expiration", "valid thru", or "valid through",
  so an ID's birth date no longer becomes the file date.
- **PDF temp-file fix** (`ocr.py`): one `NamedTemporaryFile` per page,
  unlinked after OCR (was a single fixed `/tmp` path for every page).
- **Visual HTML report** (`hermes_organizer/report.py`, stdlib only):
  self-contained page with run summary, one card per file (thumbnail,
  category/subcategory + confidence bar, best guess + signal chips on
  review, proposed filename → folder, OCR excerpt, duplicate badge).
  All user-derived text is HTML-escaped.

### How to generate the HTML report

```bash
# Single scan, straight from run_scan.py
.venv/bin/python run_scan.py --dir mock_samples \
  --report sample_reports/mock-scan.json \
  --html sample_reports/mock-scan.html

# Combine several scans into one report (thumbnails from all folders)
.venv/bin/python -m hermes_organizer.report \
  --report sample_reports/mock-scan-v02.json \
  --report sample_reports/drive-scan-v02.json \
  --report sample_reports/drive-scan-live-v02.json \
  --image-dir mock_samples --image-dir samples/drive \
  --image-dir samples/drive-live \
  --out sample_reports/scan-report-v0.2.html
```

## v0.3 (2026-11-12) — Gemini vision bridge (free tier)

Same boundary as v0.1/v0.2: scan, classify, propose. `execution.py` is still
a gated stub (`WRITE_ENABLED=False`), no Drive write calls anywhere.

The stubbed `VisionBridge` is now wired to the **Google Gemini API free
tier** (`hermes_organizer/ocr.py`, stdlib `urllib` only — no google-genai
SDK, no new dependencies). Vision is a **second opinion, not a
replacement**: OCR runs first and classifies as before; vision only steps
in for files OCR leaves in review or with very thin OCR text (or on every
image with `--vision always`). A vision result is kept only when it
improves the OCR-only result (newly classified, or higher confidence) —
otherwise the OCR-only result stands, and `vision_used` stays `False`.

### Getting a free key

1. Go to [Google AI Studio](https://aistudio.google.com) and sign in with
   a Google account. No credit card required for the free tier.
2. Create an API key (Get API key → Create API key).
3. Set it in the environment before scanning:

```bash
export GEMINI_API_KEY="your-free-key-here"
```

(`VISION_API_KEY` also works as a fallback. The key is sent only as the
`x-goog-api-key` request header, never in the URL, and is never logged,
printed, or stored. Model override: `GEMINI_MODEL`, default
`gemini-3.5-flash` — verified live 2026-11-12; `gemini-2.5-flash` returns
HTTP 404 for new keys and `gemini-3.8-flash` 503s under load.)

No env key? The bridge falls back to the stored `custom.gemini`
connector credential (added via the secure link; see
`~/workspace/custom-connectors/custom.gemini/CONNECTOR.md`). It resolves
through the authd surrogate (`?key=` placement) and is likewise never
logged or stored. Env key takes precedence when both exist.

### Running a scan with vision

```bash
# auto (default): vision only on review / thin-text files
GEMINI_API_KEY="..." .venv/bin/python run_scan.py --dir samples/drive \
  --report sample_reports/drive-scan-v03.json --vision auto

# always: vision on every image (uses more free-tier quota)
GEMINI_API_KEY="..." .venv/bin/python run_scan.py --dir samples/drive \
  --report sample_reports/drive-scan-v03.json --vision always

# off: OCR only, exactly as v0.2 behaved
.venv/bin/python run_scan.py --dir mock_samples \
  --report sample_reports/mock-scan.json --vision off
```

The scan prints one line at startup stating the vision mode and whether a
key was found (never the key itself). Results carry `vision_used: true`
and an `ocr_engine` like `rapidocr-local+gemini-3.5-flash` (or just
`gemini-3.5-flash` when OCR found no text at all).

### Free-tier limits and fallback behavior

- Pacing: at least 4 seconds between vision calls (free tier is ~15
  requests/minute). On HTTP 429 the bridge retries up to 3 times with
  exponential backoff, then raises `VisionRateLimited`.
- A 400/403 (bad or missing key) raises `VisionAuthError` and prints a
  single warning line in the scan summary — vision is skipped and results
  stay OCR-only.
- A vision failure on one file never kills the batch: the failure is
  written to the audit log as `vision-error: <short reason>` and the
  OCR-only result is kept.
- Images over ~1.5 MB are downscaled first (longest side max 1568 px,
  JPEG quality 85, in memory) before upload.
- PDFs stay OCR-only: the bridge takes image bytes, not rendered pages.

### Privacy note

`--vision auto` / `--vision always` sends the images it looks at to
Google's Gemini API (those files only — classified files with plenty of
OCR text are never sent in auto mode). `--vision off` sends nothing
anywhere: local RapidOCR only, same as v0.2.

### Live vision test (2026-11-12)

First live run with the connected free-tier key (`gemini-3.5-flash`,
stored `custom.gemini` credential, no env key): 13 files scanned
(`samples/drive` + `samples/drive-live`), 9 vision attempts, 1 kept.
The one kept file is a Cash App Bitcoin order screenshot — vision read
it as a crypto purchase receipt and lifted it 0.14 → 0.40 (still
review, best guess Receipt). No file was rescued out of review: vision
describes these files well (domain pricing, Replit UI, logos), but the
taxonomy has no matching keywords, so the description alone cannot
classify them. That points at taxonomy work, not a vision failure.
One call timed out even at the 180s timeout and was logged as
`vision-error` with the OCR-only result kept — the batch never dies.
Every vision attempt (kept or not) is now written to the audit log
(`action: vision`, with old/new confidence). Combined visual report:
`sample_reports/scan-report-v0.3.html` (18 files, 1 vision badge).

## v0.4 (2026-11-12) — taxonomy fix

The v0.3 live test showed vision describing files well while the taxonomy
had no keywords for them, so nothing classified. v0.4 adds three
subcategories and strengthens a fourth (threshold stays 0.60, vision
bridge and model untouched):

- **Financial / Crypto Transaction** — `/Finance/Crypto Transactions/{year}/`.
  Keywords: crypto, bitcoin, btc, order filled, cryptocurrency, ethereum,
  wallet, exchange, coinbase, cash app, satoshi, blockchain, average
  price. Filename signals: crypto, btc.
- **Technical & Work Assets / Domain Research** — `/Work/Domain Research/{year}/`.
  Keywords: domain, domain search, registrar, suggested results, renew,
  per year, yr, year, premium, ssl, dns, whois, add to cart, retail.
  Filename signal: domain.
- **Technical & Work Assets / Brand Assets** — `/Work/Brand Assets/{year}/`.
  Keywords: logo, brand, wordmark, emblem, logotype, brand mark,
  trademark. Filename signal: logo.
- **Code Screenshot strengthened** — replit, code editor, ide, editor,
  github, vscode, visual studio, workspace, repository, tsx, jsx, src,
  shell. Filename signal: replit. ("replit" matches inside "replit.com"
  because the "." after it is a word boundary; "tsx" matches after the
  "." in ".tsx". ".com" itself is still not a keyword — it cannot match
  inside a domain name.)

Naming: `_topic_from_text` skips the new doc-type words and short
all-caps OCR noise (kept acronyms: BTC, ETH, USD, IRS, EIN, NDA, API,
LLC, INC, SSN), so proposed filenames stay readable.

### Live re-scan results (v0.4, stored credential, `--vision auto`)

13 files, 4 vision attempts total (most files now classify on OCR alone,
so vision is rarely needed). Before → after (v0.3 → v0.4):

| File | Before | After |
| --- | --- | --- |
| Cash App BTC screenshot | 0.40 review | **0.667 Crypto Transaction** |
| Domain screenshot 141325 | 0.14 review | **0.625 Domain Research** |
| Domain screenshot 141348 | 0.14 review | **0.727 Domain Research** |
| Replit screenshot (x2) | 0.46 review | **0.793 Code Screenshot** |
| logo.jpg | 0.00 review | **0.625 Brand Assets** (vision kept, 0.40 → 0.625) |
| Credit reports (x3) | 0.79 / 0.79 / 0.82 | 0.786 / 0.786 / 0.824 (unchanged) |

Still in review, correctly: the JECI services webpage (0.538 — vision
hit a free-tier 429 on both runs, OCR-only result kept) and the JECI
marketing graphic (0.50). Mock scan: 9/9 classified, 0 flagged
(originals unchanged at 0.80 / 0.79 / 0.84 / 0.78 / 0.83; new mocks
0.786 / 0.833 / 0.846 / 0.769). Combined visual report:
`sample_reports/scan-report-v0.4.html` (22 files, 1 vision badge).

### Folder-scoped scans (v0.4 follow-up)

The Drive indexer can now aim at one folder instead of sweeping the
whole Drive. Pass the folder's Drive ID with `--folder` (implies
`--drive`); only images directly inside that folder are listed,
downloaded as working copies, and classified. Still read-only:
proposals only, originals untouched.

```bash
.venv/bin/python run_scan.py --folder <drive-folder-id> \
  --download-dir samples/drive-test/<folder-name> \
  --report sample_reports/<folder-name>-scan.json \
  --html sample_reports/<folder-name>-scan.html --vision auto
```

45 tests (41 + 4 folder-scan tests) in `tests/test_folder_scan.py`.

## v0.5 (2026-11-12) — Product Photo + Gemini 503 retry

The first real-folder test (beyond-vision, 18 apparel images) classified
0/18 in v0.4: vision described the leggings / sports-bra / t-shirt
mockups well but no subcategory existed for the description to land in.
v0.5 adds one subcategory and retries the other free-tier failure mode
(threshold stays 0.60, model stays gemini-3.5-flash):

- **Technical & Work Assets / Product Photo** — `/Work/Product Photos/{year}/`.
  Keywords: product, product photo, product image, mockup, mock-up,
  leggings, garment, apparel, photograph, isolated against, white
  background, sports bra, waistband, t-shirt, clothing, fashion,
  outfit, camouflage, print, pattern, dress. Filename signal: product.
  No signal for "attachment" — every file in the folder is named that.
  Two deliberate choices: "plain white background" is NOT a keyword,
  because "white background" already matches inside it as a substring
  and adding both would double-count one span and let a bare logo on a
  white background outscore Brand Assets 4 to 3; "mock-up" is a keyword
  alongside "mockup" because vision writes both spellings. "print" and
  "dress" are word-boundary keywords (no match inside "blueprint" /
  "address"). A logo-only description on a white background still lands
  Brand Assets (8 pts vs 2).
- **Vision bridge retries HTTP 503** with the same exponential backoff
  as HTTP 429 (up to 3 retries, Retry-After honoured, then a distinct
  `VisionOverloaded` error naming the 503, logged as `vision-error` —
  the batch never dies). The v0.4 folder test hit 7x 503 in two runs.
  180s timeout and 4s pacing unchanged.
- **Naming:** the vision prompt's own labels ("TRANSCRIPTION: No
  readable text. DESCRIPTION: …") no longer become the topic — "readable",
  "text", and "vision" are skipped, so product words flow into proposed
  filenames (e.g. `2026-11-12_ProductPhoto_apparel_mockup.jpg`). Product
  words themselves are deliberately NOT skipped as doc-type words.
- **Prompts:** Product Photo added to the classifier taxonomy list;
  the vision prompt now asks for product-photo / apparel-mockup
  descriptions by name (item, setting, logo, print).
- New mock `IMG_0103_product.png` (10 mocks total). 61 tests:
  `tests/test_taxonomy_v05.py` classifies the 5 real vision texts copied
  verbatim from `beyond-vision-scan.json` (0.625 / 0.842 / 0.833 / 0.812 /
  0.75), plus cross-talk, boundary, naming, 503-retry, mock-scan, and a
  v0.4-scan reproduction test.

### Live re-scan results (v0.5, stored credential, `--vision auto`)

18 files, 387s. **1 of 18 classified** — and the 1 is the proof the
taxonomy fix works end to end:

| File | Before (v0.4) | After (v0.5) |
| --- | --- | --- |
| attachment (9).jpg | 0.00 review (vision never got through) | **0.786 Product Photo** — vision: "apparel mockup … full-length leggings … plain white studio background … waistband … patterned graphic print"; proposed `2026-11-12_ProductPhoto_apparel_mockup.jpg` |
| attachment (15) / (13) / (11) / (8) / (5).jpg | 0.50 review, best guess Brand Assets (logo only) | 0.00 review — **vision never got through** (see below), so the new category was never tested on them live |

**Vision error counts (v0.5 run): 17x HTTP 429 after 3 retries, 0x 503,
0 timeouts.** The free tier was hard rate-limited for the whole run —
every file except (9) burned 4 attempts (initial + 3 retries) and still
got 429. That is a free-tier health result, not a code failure: the
threshold was not lowered and no keyword was weakened to chase it. The
429s were already retried in v0.4; the new 503 path never fired live
(it is covered by mocked tests). Likely cause is cumulative usage — three
full folder scans plus the v0.3/v0.4 scans all ran on the same free key
in one day. The 5 logo-only files are proven to land Product Photo
**in tests** on their verbatim vision texts (0.625–0.842); a re-scan
when the tier recovers should confirm them live. No file regressed to
a wrong category — the 17 unclassified files sit at 0.00 review with
vision never attempted successfully. Visual report:
`sample_reports/beyond-vision-scan-v05.html` (18 files, 1 vision badge).

## v0.6.1 (2026-11-13) — Cloudflare live, default flipped

Gemini's free tier kept rate-limiting folder scans (17x 429 in the v0.5
re-scan), so v0.6 added a second vision provider alongside Gemini:
**Cloudflare Workers AI, Llama 3.2 11B Vision**
(`@cf/meta/llama-3.2-11b-vision-instruct`). v0.6.1 corrects the
endpoint the v0.6 build got wrong and flips the default.

- **Endpoint correction (live-verified):** the OpenAI-compatible
  `/ai/v1/chat/completions` endpoint **rejects image calls** (HTTP
  400, code 3030 — "no user-supplied messages"), despite the docs
  example. The bridge now uses the native
  `POST /client/v4/accounts/{account_id}/ai/run/{model}` endpoint
  with `{"messages": [...]}` in the body (model in the URL) and reads
  `result.response`. First live call: the model terms gate (HTTP 403,
  code 5016) — accepted by submitting the prompt `agree` to the model
  once, which records the Meta license acceptance.
- **Switching providers:** the default is now `cloudflare`
  (`VISION_PROVIDER=gemini` to go back). Same `run_scan.py --vision
  auto` flow, same prompt, same `OcrResult` shape. The engine string
  names the provider (`cloudflare-llama-3.2-11b-vision`).
- **Credentials:** token from `CLOUDFLARE_API_TOKEN`, or the stored
  `custom.cloudflare` connector credential via the authd surrogate
  (Bearer placement); account ID from `CLOUDFLARE_ACCOUNT_ID`, or
  the file `~/.config/hermes-organizer/cloudflare_account_id`. Get
  both in the Cloudflare dashboard: AI > Workers AI > Use REST API >
  **Create a Workers AI API Token** (the template pre-fills the
  permission) and copy the Account ID from the same page. The token
  travels only as the `Authorization: Bearer` header and is never
  logged or stored. Missing credentials raise a clear error, and the
  scan falls back to OCR-only.
- **Free-tier limits:** 10,000 Neurons/day, reset 00:00 UTC. One
  image cost **~40 Neurons live** — roughly **250 images/day**
  inside the free allowance. Past that, Workers Free stops with error
  **3036** (HTTP 429); Workers Paid ($5/mo) bills overage at
  $0.011/1,000 Neurons.
- **Error handling:** 3036 (daily allowance spent) is **never
  retried** — the allowance only resets at 00:00 UTC, so retrying
  cannot help (`VisionDailyLimit`, a `VisionRateLimited` subclass, so
  existing callers still treat it as rate-limited). Error 3040
  (capacity) and 5xx retry with the same exponential backoff as
  Gemini's 429/503 (up to 3 retries). HTTP 403 carrying code 5016
  means the Llama 3.2 model terms have not been agreed — accept them
  in the dashboard, or submit the prompt `agree` to the model once.
  180s timeout and 4s pacing are unchanged from the Gemini bridge.
- **Tests:** 80 total (61 existing + 19 in
  `tests/test_vision_cloudflare.py`): success path (native /ai/run
  URL, Bearer header, messages-only body, `result.response` parse),
  3036 raises once with no retries, 3040/429/5xx retry-then-raise,
  timeout, 5016 terms error, provider selection + invalid provider +
  cloudflare default, missing-credentials (both vars, token-only),
  account-ID config-file fallback (env wins), vault token fallback
  (surrogate Bearer header) + vault unavailable, downscale reuse, and
  a watcher test proving the `cloudflare-llama-3.2-11b-vision` engine
  label flows through to the report.
- **Live test: PASSED 2026-11-13.** Token from the stored
  `custom.cloudflare` connection, account ID from the config file —
  no env vars set. Three bridge calls on
  `samples/drive-test/beyond-vision/attachment (9).jpg` (the leggings
  mockup) all classified **Product Photo at 0.812–0.842**, beating
  Gemini's 0.786 on the same file. Proposed filename
  `2026-11-13_ProductPhoto_image_product.jpg` →
  `/Work/Product Photos/2026/`. Default flipped to `cloudflare`.

## v0.6.2 (2026-11-13) — Namer disambiguation

The v0.6.1 Beyond Vision re-scan classified 18/18 files but the namer
collapsed them to 7 distinct filenames (`image_presents` x5, …) — the
topic picker took the first words of the vision text ("The image
presents…"). Executing those renames would have overwritten files.

- **Richer topic:** generic vision verbs (image, presents, depicts,
  appears, shows, displays, photo, picture…) are now skipped, and
  garment / print / colour / material words are preferred, so topics
  read like the item (`shirt_camouflage`, `leggings_abstract`,
  `cropped_leopard`). Prompt-echo junk (`Transcribe_visible`,
  `Quicksilver_www`) is skipped too. Format
  `date_Subcategory_topic.ext` unchanged.
- **Guaranteed uniqueness:** `deduplicate_filenames()` appends `_2`,
  `_3`, … when a (filename, folder) pair would repeat within a scan,
  so a scan can never emit two identical destinations. Same filename
  in different folders is left alone; review results pass through.
- **Tests:** 89 total (80 existing + 9 new in
  `tests/test_naming_v061.py`, fixture
  `tests/fixtures/beyond_vision_v061_texts.json` — the 18 verbatim
  v0.6.1 vision texts). The 18 texts now yield 18 distinct names, 16
  on topic alone, 2 via the counter suffix.

## v0.7.0 (2026-11-13) — Execution + approval flow

The read-only prototype can now rename and move files — only on
explicit per-item approval, and never by the scan path. The scan
still only proposes; `run_execute.py` is the only entry point that
writes, and it defaults to dry-run.

**The flow:** scan → proposals file → approve → execute → verify →
rollback if needed.

```sh
# 1. Scan (read-only) and emit a proposals file:
python run_scan.py --drive --folder <folder_id> --limit 20 \
    --report sample_reports/scan.json \
    --proposals sample_reports/proposals.json

# 2. Approve: copy proposals to approvals and flip approved to true
#    on the entries you accept. Do not edit filenames or folders.
cp sample_reports/proposals.json sample_reports/approvals.json

# 3. Dry-run (default): prints the plan, zero Drive write calls.
python run_execute.py --proposals sample_reports/proposals.json \
    --approvals sample_reports/approvals.json --log execution-log.jsonl

# 4. Execute for real.
python run_execute.py --proposals sample_reports/proposals.json \
    --approvals sample_reports/approvals.json --log execution-log.jsonl \
    --execute

# 5. Roll back from the execution log (dry-run unless --execute).
python run_execute.py --rollback execution-log.jsonl [--execute]

# 6. Verify a past execution log against live Drive (read-only).
python run_execute.py --verify execution-log.jsonl
```

- **Proposals file:** one entry per scanned file with
  `proposal_hash` (SHA-256 of file_id + proposed_filename +
  target_folder_path) and `approved: false`. Files the scan could
  not classify have `proposed_filename: null` and can never be
  approved — they are reported as skipped. The executor only acts
  on entries whose approval matches the proposals file by file_id,
  has `approved: true`, and whose hash still matches: a re-scan
  changes the proposal, the hash changes, and the old approval is
  refused as stale.
- **Safety gates** (the executor refuses unless ALL hold):
  `--execute` passed AND module `WRITE_ENABLED` set (only
  `run_execute.py --execute` sets it); a valid approvals file;
  every actionable proposal approved with a matching hash; the
  target name does not already exist in the target folder (checked
  by listing the folder before each rename — the namer's dedup
  suffix only handles collisions *within* one scan); the file's
  Drive state is re-checked right before each write, so a file
  moved or renamed since the plan aborts the run. **Never delete:**
  there is no `files delete` call anywhere in the codebase —
  grep-verified, and a test asserts it.
- **Target folders:** a target path like `/Work/Product
  Photos/2026/` is resolved from Drive root (or `--root`), creating
  missing folders and recording their IDs. A rename+move happens
  in one `files.update` call (name + addParents + removeParents).
- **Execution log (JSONL):** every executed change records
  file_id, original_name, original_parent_ids, new_name,
  new_parent_ids, timestamp, proposal_hash — appended
  write-through, one record per change, so a crash mid-batch still
  leaves a rollback trail. Rollback restores names + parents in
  reverse log order from this log.
- **Verification:** after execution, each file is re-fetched by ID
  (`files.get`) and its name + parents compared against the plan;
  the report lists ok/mismatch per file. Success is verified,
  never assumed.
- **Drive write scope (probed 2026-11-13):** the channel supports
  writes — `hatch_gws_cli drive files update` (rename + move) and
  `files create` (folders) both exist, and the Google Drive
  connector doc documents them as normal approval-gated
  operations. Drive is connected as Adrian Jordan
  (jordanad46@gmail.com). The token's OAuth scope could NOT be
  confirmed read-only (no scope field in status/about, and token
  introspection is out of bounds), so a 403 surfaces as a clean
  `WriteScopeError` ("reconnect Google Drive with write access").
  **Live writes happened 2026-11-13 during the Beyond Vision wave
  demo** (a `--execute` dry-run check was run with demo approvals
  against real Drive — that was a mistake, not a planned execute):
  `files update` applied server-side while the CLI call hung past
  the 120s timeout, twice. Verified live afterward: 2 files renamed
  + moved into the new `/Work/Product Photos/2026/` and 3 folders
  created (`Work`, `Product Photos`, `2026`); the other 16 files
  untouched. No execution log existed for those changes (the log
  write happens after the update returns), so a recovery log was
  reconstructed from verified live state at
  `sample_reports/execution-log-recovery-2026-11-13.jsonl` and
  `--verify` confirms both files. Lesson built into the code:
  `DriveWriter._run` now converts a subprocess timeout into
  `DriveTimeoutError`, and `execute()` re-fetches the file on a
  timeout — applied means log it and continue, anything else stops
  the run — and folder creations are now logged too. The wave's
  remaining 15 files still need Adrian's approval on a fresh
  re-scan before any further execute.
- **Tests:** 110 total (89 existing + 21 new in
  `tests/test_execution.py`, against an in-memory fake Drive):
  refuses without `--execute`, without `WRITE_ENABLED`, without
  approvals, on hash mismatch, on stale approval after a re-scan,
  on target-name collision, on changed file state; dry-run makes
  zero write calls; happy path renames+moves one file and logs it
  (including folder_created audit records); rollback restores;
  verification detects a mismatch; update timeout with the change
  applied server-side is logged and the run continues, while a
  timeout with no change aborts; subprocess timeout converts to
  `DriveTimeoutError`; proposals writer round-trip; no-delete grep
  test.
