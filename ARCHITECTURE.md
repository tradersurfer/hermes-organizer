# Hermes Organizer — Autonomous File & Media Organizer Agent

Source of truth for the Hermes Organizer project. Handoff from Adrian, 2026-11-11.
Project home is the "Hermes Organizer" side chat. Future work continues there.

## Standing constraints

- **v0.1 is READ-ONLY — scan, classify, propose. No renames, moves, or deletes in Adrian's real Drive without his explicit yes per folder.**
- Local prototype only — no GitHub push/PR, no API keys, no paid services, no purchases.
- Never fake a classification result — report live vs stubbed per component.

## 1. System Overview & Executive Summary

The Autonomous File & Media Organizer Agent is designed to automatically index, evaluate, rename, and structure unstructured file stores (specifically Google Drive environments) with a heavy emphasis on zero-metadata or visually ambiguous assets (e.g., IMG_0042.jpg, scan_3.pdf, unnamed screenshots).

The system operates as a hybrid pipeline using:

- Google Drive API v3 for metadata indexing, file stream retrieval, and folder restructuring.
- Multimodal Vision/OCR Processing (Gemini / Claude Vision / Local OCR bridges) for inspecting binary image data, extracting visible text, identifying visual context, and assigning semantic tags.
- Hermes Agent Orchestration / Model Context Protocol (MCP) to run periodic background synchronization, route batch processing queues, and trigger autonomous file organization jobs based on user rules.

## 2. Technical Architecture & Component Breakdown

```
[Google Drive API] <---> [Indexing & Fetch Worker] -> [Multimodal Vision & OCR Engine] -> [Taxonomy & Classification LLM] -> [Drive Restructuring & Execution Engine]
```

Component Details:

- **Indexing Engine:** Enumerates non-indexed or newly added files. Flags files lacking user titles or metadata tags.
- **Ingestion Pipeline:** Streams down image/document byte arrays for visual inspection without persistent cloud storing of private data.
- **Vision & Feature Extraction Engine:** Processes standard image formats (PNG, JPEG, WEBP) and PDF renders (pdf2image). Extracts full-text OCR (invoices, receipts, tax forms) and structural visual tags (whiteboards, diagrams, personal IDs, contract signatures).
- **Taxonomy Resolver:** Compares extracted features against a user-defined taxonomy or generates standard folder hierarchies (e.g., /Finance/Receipts/2026/, /Legal/Contracts/).
- **Execution Layer:** Calls drive.files.update to rename files systematically (2026-04-15_VendorName_Invoice_1042.pdf) and update parent folder mappings (addParents, removeParents).

## 3. Data Schema & Folder Taxonomy Strategy

Metadata Tagging Schema (JSON): file_id, original_name, mime_type, extracted_text, visual_classification {category, subcategory, confidence}, proposed_filename, target_folder_path, target_folder_id.

Example: IMG_9823.PNG -> 2026-10-12_Receipt_Vendor.png, /Finances/Receipts/2026/, confidence 0.96.

Taxonomy Ruleset:

- **Financial:** Receipts, Tax Documents, Bank Statements, Invoices, Credit Reports, Crypto Transactions.
- **Legal & Personal Identity:** Contracts, ID Documentation, Property Records.
- **Technical & Work Assets:** Architecture Diagrams, Whiteboard Brainstorms, Code Screenshots, Domain Research, Brand Assets, Product Photos.
- **Fallback / Unsorted:** Requires_Human_Review (for files with low OCR/Vision confidence scores < 0.60).

v0.4 (2026-11-12) added Crypto Transaction, Domain Research, and Brand Assets after the v0.3 live vision test showed Gemini describing those files well while the taxonomy had no keywords for them; Code Screenshot keywords were also strengthened for IDE / hosted-editor (Replit) screenshots. Runtime rules live in `hermes_organizer/taxonomy.py`, mirrored in `taxonomy_rules.json`.

v0.5 (2026-11-12) added Product Photo after the first real-folder test (beyond-vision, 18 apparel images) classified 0/18: vision described the leggings/sports-bra/t-shirt mockups well but no subcategory existed for the description to land in. The vision bridge also retries HTTP 503 (high demand) with the same exponential backoff as HTTP 429 (up to 3 retries); the folder test hit 7x 503 and 11x 429-after-retries in two runs.

v0.6 (2026-11-13) added a second vision provider: Cloudflare Workers AI (Llama 3.2 11B Vision) via the native /ai/run endpoint, selected with VISION_PROVIDER (default is cloudflare since v0.6.1; VISION_PROVIDER=gemini switches back). Same prompt, same OcrResult shape, same 180s timeout and 4s pacing. Error 3036 (daily free allowance spent) is never retried — the allowance only resets at 00:00 UTC; error 3040 (capacity) and 5xx retry with backoff like Gemini's 429/503. Credentials: token from CLOUDFLARE_API_TOKEN or the stored custom.cloudflare connector (authd surrogate, Bearer placement); account ID from CLOUDFLARE_ACCOUNT_ID or ~/.config/hermes-organizer/cloudflare_account_id (the token needs Workers AI Read + Edit, via the dashboard's "Create a Workers AI API Token" template). Free tier: 10,000 Neurons/day, reset 00:00 UTC (~40 Neurons/image live, ~250 images/day). v0.6.1 (2026-11-13) corrected the endpoint — the OpenAI-compatible /ai/v1/chat/completions route rejects image calls (HTTP 400, code 3030) — and flipped the default to cloudflare after the live test passed: attachment (9).jpg classified Product Photo at 0.812-0.842, beating Gemini's 0.786 on the same file.

v0.6.2 (2026-11-13) fixed the namer: the v0.6.1 Beyond Vision re-scan classified 18/18 but proposed only 7 distinct filenames (topic picker took the first words of the vision text). Generic vision verbs are now skipped and garment/print/colour/material words preferred, so topics read like the item; deduplicate_filenames() appends _2/_3/... when a (filename, folder) pair would repeat within a scan, so executing renames can never overwrite a file. 89/89 tests.
v0.7.0 (2026-11-13) adds the execution + approval flow, superseding the v0.1 "execution stubbed" status below: run_scan.py --proposals emits one entry per file with a proposal_hash (SHA-256 of file_id + proposed_filename + target_folder_path) and approved:false; the user copies it to an approvals file and flips approved to true. run_execute.py (the only write entry point; the scan path never writes) defaults to dry-run and performs renames+moves only with --execute, which mints a fresh per-call write token for that one call — the module-global WRITE_ENABLED flag that v0.7.0 shipped with was removed 2026-11-21 (Grok review) because two concurrent gateway requests could interleave on it. Gates: --execute + a valid write token; valid approvals file; every actionable proposal approved with a matching hash (a re-scan makes old approvals stale); target name free in the target folder (listed before each rename); fresh Drive state re-checked before each write. Target folders resolve from Drive root (or --root), creating missing ones; rename+move is one files.update call (name + addParents + removeParents). Every change is appended write-through to a JSONL execution log (file_id, original_name, original_parent_ids, new_name, new_parent_ids, timestamp, proposal_hash); rollback restores from it in reverse order; verification re-fetches each file by ID and compares name + parents. Never delete: no files.delete call anywhere, grep-verified and test-asserted. Drive write-scope status (probed 2026-11-13): the channel supports writes (CLI + connector doc both document files update/create as approval-gated operations; Drive connected as Adrian Jordan) but the token's OAuth scope cannot be confirmed read-only, so a 403 surfaces as a clean WriteScopeError. Live writes happened 2026-11-13 during the Beyond Vision wave demo (an --execute check ran with demo approvals against real Drive — a mistake, not a planned execute): files update applied server-side while the CLI hung past 120s, twice; 2 files renamed+moved into new /Work/Product Photos/2026/ and 3 folders created, verified live; recovery log reconstructed at sample_reports/execution-log-recovery-2026-11-13.jsonl. Timeout handling was hardened after: _run converts subprocess timeouts to DriveTimeoutError, execute() re-fetches on timeout (applied -> log and continue, else stop the run), and folder creations are logged. 110/110 tests.

## 4. API Specification & Integration Boundaries

- Google Drive API Scope Requirements: drive.readonly (initial scanning); drive.file / drive (moving & renaming assets).
- Model Provider APIs: Structured Outputs enabled for taxonomy resolution (Pydantic schema enforcement).

## 5. Agent Workflow & Execution Steps

- **Poll / Hook:** Trigger on Google Drive Webhook (changes.watch) or scheduled Hermes cron routine.
- **Filter:** Select files matching unorganized criteria (name contains 'IMG_' or parents in root or tagged #unorganized).
- **Inspect:** Fetch image stream -> Execute Multimodal OCR & Classification.
- **Resolve Location:** Check if target folder path exists via standard lookup cache; create if non-existent.
- **Execute & Log:** Rename file, update parent references, write execution audit log to local sqlite/JSON datastore.

## 6. Failure Modes, Edge Cases & Mitigation Strategies

- **Ambiguous / Low Confidence Content:** If an image is blurry or contains conflicting information, assign the file to _Unresolved_Review and attach a Google Drive comment explaining why.
- **Rate Limits (Google Drive API / Vision API):** Implement exponential backoff retry algorithms with rate-limiting token buckets (e.g., max 100 Drive requests/minute).
- **Permission Constraints:** Handle read-only shared folders gracefully without crashing batch jobs; skip and log non-writeable files.

### Approval trust model — what the hash does and does not protect

- **The approvals file is trusted input.** It is written only by the owner: by hand (copy the proposals file and flip `approved`), by `organizer_approve` after the owner's explicit yes in chat, or by the dashboard pane after the owner's clicks in the pane. The dashboard backend and API never write approvals; they only read the proposals/approvals files and call the tool handlers.
- **`proposal_hash` binds an approval to its exact proposal** (SHA-256 of file_id + proposed filename + target folder path, NUL-joined). A re-scan changes the proposals, the hashes change, and old approvals are refused as stale (hash mismatch). Review entries (no proposed filename) can never be approved. `match_approvals()` refuses the whole run unless every actionable proposal has an explicit `approved: true` entry with a matching hash.
- **The hash does NOT protect against someone who can write the approvals file.** `proposal_hash` is deterministic and public, so a writer can compute a matching hash for any proposal they invent. The protection against that is provenance, not cryptography: only the owner writes approvals, and every execution is logged (execution-log JSONL) with the proposal hash of each change, so any write is attributable to the approvals file that authorized it.
- **Write authorization is per-call, not a shared flag.** `execute()` / `rollback()` require a fresh single-use token minted by `mint_write_token()` at the approved moment (run_execute.py `--execute`, or the plugin handlers with `execute: true`). The token is spent on entry, so one token authorizes exactly one call and two concurrent calls can never share authorization.
- **If a deployment needs stronger non-repudiation** (signed approvals, a second confirmation channel), that is future work — the current design documents the boundary instead of pretending the hash covers it.
- **Listing completeness:** every Drive list call is paginated (nextPageToken loop) with pageSize at the API maximum (1000). The token is only returned when requested in `fields` (Drive partial responses omit it otherwise), so every call site asks for it explicitly — verified live 2026-11-21 with a two-page round-trip against the real CLI (pageSize 5, then the returned token, 5 new files, zero overlap). A listing that exceeds the page cap raises rather than silently truncating.

## 7. Immediate Next Steps for Technical Lead

- Implement drive_watcher.py using google-api-python-client with service account credentials or OAuth2 user tokens.
- Define the visual classifier system prompt utilizing Pydantic output parsing.
- Set up local integration test cases using sample mock folders containing generic receipts and whiteboards.

## v0.1 scope (locked 2026-11-11)

Read-only scan + classify + propose. No renames, moves, or deletes. No real Drive writes until Adrian approves per folder.

## v0.1 implementation status (2026-11-11)

- OCR: local RapidOCR (ONNX, no key, no cloud) — working.
- Drive: read-only listing + download via the connected account.
- Classifier: weighted keyword/regex scoring over OCR text + filename signals, Pydantic-validated output. Confidence < 0.60 → Requires_Human_Review.
- Execution: stubbed — `execution.py` refuses to run.
- Vision model bridge (Gemini / Claude Vision): stub — needs an API key.
- Drive `changes.watch` webhook: skeleton only; polling works today.
- Sample run: 6 real Drive files scanned — see `sample_reports/scan-2026-11-11.json`.
