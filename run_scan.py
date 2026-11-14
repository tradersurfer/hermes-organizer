#!/usr/bin/env python3
"""CLI: read-only scan of a local folder (or a small Drive sample) -> report.

Usage:
  python run_scan.py --dir samples/drive --report sample_reports/scan.json
  python run_scan.py --drive --limit 10 --download-dir samples/drive --report sample_reports/drive-scan.json

Writes a JSON report + a readable summary to stdout. Proposes only — never
renames, moves, or deletes anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.indexer import DriveIndexer  # noqa: E402
from hermes_organizer.ocr import VisionBridge  # noqa: E402
from hermes_organizer.report import build_image_lookup, render_html  # noqa: E402
from hermes_organizer.watcher import scan_drive_sample, scan_local_folder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes Organizer v0.1 — read-only scan")
    ap.add_argument("--dir", default="samples/drive", help="Local folder to scan")
    ap.add_argument("--drive", action="store_true", help="Scan Drive (read-only list + download)")
    ap.add_argument("--folder", default=None, help="Drive folder ID to scan (implies --drive); only images directly inside it")
    ap.add_argument("--limit", type=int, default=10, help="Max Drive files (default 10)")
    ap.add_argument("--download-dir", default="samples/drive", help="Where Drive working copies go")
    ap.add_argument("--report", default="sample_reports/scan.json", help="JSON report path")
    ap.add_argument("--proposals", default=None, help="Also write a proposals file (JSON) for run_execute.py approval flow")
    ap.add_argument("--html", default=None, help="Also write a visual HTML report to this path")
    ap.add_argument("--audit-log", default="audit_log.jsonl", help="JSONL audit log path")
    ap.add_argument("--vision", default="auto", choices=["off", "auto", "always"],
                    help="Vision second opinion: off (OCR only), auto (review / thin-text files only), always (every image). Provider from VISION_PROVIDER (cloudflare default, gemini). Default: auto.")
    args = ap.parse_args()

    # Vision bridge: built only when not off. Provider from
    # VISION_PROVIDER (cloudflare default, gemini). Gemini credentials
    # come from GEMINI_API_KEY or, when unset, the stored custom.gemini
    # connector credential; Cloudflare credentials come from
    # CLOUDFLARE_API_TOKEN (or the stored custom.cloudflare connector)
    # + CLOUDFLARE_ACCOUNT_ID (or ~/.config/hermes-organizer/
    # cloudflare_account_id). None of them are ever printed.
    vision = VisionBridge() if args.vision != "off" else None
    if args.vision == "off":
        print("Vision: off (OCR only — nothing is sent anywhere)")
    elif vision is not None and vision.available():
        print(f"Vision: {args.vision} ({vision.provider}, model {vision.model})")
    else:
        if vision is not None and vision.provider == "cloudflare":
            hint = ("set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID "
                    "to enable")
        else:
            hint = "set GEMINI_API_KEY or connect Gemini to enable"
        print(f"Vision: {args.vision} (no {vision.provider} credentials "
              f"found — OCR only; {hint})")

    if args.drive or args.folder:
        results = scan_drive_sample(DriveIndexer(), args.download_dir, args.limit,
                                    args.audit_log, vision=vision, vision_mode=args.vision,
                                    folder_id=args.folder)
        if args.folder:
            source = f"Google Drive folder {args.folder} (read-only, limit {args.limit})"
        else:
            source = f"Google Drive (read-only, limit {args.limit})"
    else:
        results = scan_local_folder(args.dir, args.audit_log,
                                    vision=vision, vision_mode=args.vision)
        source = f"local folder {args.dir}"

    if vision is not None and vision.auth_error:
        # One warning line for the whole scan, not one per file.
        print(f"WARNING: {vision.provider} vision failed — {vision.auth_error}. "
              "Vision was skipped; results are OCR-only.")

    payload = {
        "source": source,
        "action": "propose-only (v0.3 read-only)",
        "vision_mode": args.vision,
        "vision_used_count": sum(1 for r in results if r.vision_used),
        "files_scanned": len(results),
        "results": [r.model_dump() for r in results],
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    if args.proposals:
        from hermes_organizer.proposals import write_proposals  # noqa: E402

        write_proposals(results, args.proposals, source=source)
        print(f"Proposals file: {args.proposals} "
              "(approve by copying it to an approvals file and "
              "flipping approved to true; see run_execute.py --help)")

    if args.html:
        image_dir = args.download_dir if args.drive else args.dir
        lookup = build_image_lookup([image_dir])
        page = render_html([payload], lookup)
        os.makedirs(os.path.dirname(os.path.abspath(args.html)), exist_ok=True)
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(page)
        print(f"HTML report: {args.html}")

    print(f"\nHermes Organizer v0.3 — read-only scan of {source}")
    print(f"Files scanned: {len(results)} | report: {args.report}\n")
    for r in results:
        c = r.visual_classification
        print(f"- {r.metadata.original_name}")
        print(f"  {c.category} / {c.subcategory}  (confidence {c.confidence:.2f})")
        print(f"  OCR chars: {len(r.extracted_text)} | engine: {r.ocr_engine}")
        if r.extracted_text:
            print(f"  text: {r.extracted_text[:160]!r}")
        print(f"  propose: {r.proposed_filename or '(none — human review)'} -> {r.target_folder_path}")
    flagged = sum(1 for r in results if r.visual_classification.subcategory == "Requires_Human_Review")
    print(f"\n{flagged} file(s) flagged for human review (confidence < 0.60).")
    print("No files were renamed, moved, or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
