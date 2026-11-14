"""Visual HTML report for Hermes Organizer v0.2 (stdlib only, no new deps).

`render_html(reports, image_lookup)` turns one or more scan JSON payloads
(the dicts run_scan.py writes) into a self-contained HTML page: inline CSS,
no external assets, thumbnails embedded as base64 data URIs when the image
exists and is under 1 MB.

CLI (combine runs into one report):
  python -m hermes_organizer.report --report a.json [--report b.json ...] \\
      --image-dir dir [--image-dir dir2] --out report.html
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import sys

MAX_THUMBNAIL_BYTES = 1_000_000
OCR_EXCERPT_CHARS = 300

_CSS = """
body { font-family: system-ui, -apple-system, sans-serif; background: #0f1115;
  color: #e8e8e8; margin: 0; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.summary { color: #9aa0a6; margin-bottom: 20px; }
.summary b { color: #e8e8e8; }
.card { background: #1a1d24; border: 1px solid #2c303a; border-radius: 10px;
  padding: 16px; margin-bottom: 16px; display: flex; gap: 16px; }
.thumb { flex: 0 0 180px; }
.thumb img { width: 180px; border-radius: 6px; border: 1px solid #2c303a; }
.thumb .note { color: #9aa0a6; font-size: 12px; }
.body { flex: 1; min-width: 0; }
.name { font-weight: 700; font-size: 16px; word-break: break-all; }
.class { margin: 6px 0; }
.bar { background: #2c303a; border-radius: 4px; height: 10px; width: 260px;
  max-width: 100%; overflow: hidden; display: inline-block; vertical-align: middle; }
.fill { height: 100%; }
.fill.green { background: #34a853; }
.fill.amber { background: #f9ab00; }
.conf { font-size: 13px; color: #9aa0a6; margin-left: 8px; }
.chips { margin: 8px 0; }
.chip { display: inline-block; background: #2c303a; border-radius: 12px;
  padding: 2px 10px; margin: 0 4px 4px 0; font-size: 12px; }
.chip.guess { background: #3b2f00; color: #f9ab00; }
.badge { display: inline-block; background: #5b21b6; color: #fff; border-radius: 4px;
  padding: 2px 8px; font-size: 12px; margin-left: 8px; }
.badge.vision { background: #0f766e; }
.propose { font-family: monospace; font-size: 13px; margin: 8px 0; word-break: break-all; }
.ocr { color: #9aa0a6; font-size: 13px; white-space: pre-wrap; word-break: break-word; }
"""

_MIME_FALLBACK = {".png": "image/png", ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg", ".webp": "image/webp"}


def build_image_lookup(folders: list[str]) -> dict[str, str]:
    """Map filename -> local path for every file in the given folders."""
    lookup: dict[str, str] = {}
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name not in lookup:
                lookup[name] = path
    return lookup


def _thumbnail_data_uri(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    if os.path.getsize(path) >= MAX_THUMBNAIL_BYTES:
        return None
    mime, _ = mimetypes.guess_type(path)
    if mime is None:
        mime = _MIME_FALLBACK.get(os.path.splitext(path)[1].lower())
    if mime is None or not mime.startswith("image/"):
        return None
    with open(path, "rb") as fh:
        return "data:" + mime + ";base64," + base64.b64encode(fh.read()).decode("ascii")


def _card(result: dict, image_lookup: dict[str, str]) -> str:
    meta = result.get("metadata") or {}
    name = str(meta.get("original_name") or "?")
    vc = result.get("visual_classification") or {}
    category = str(vc.get("category") or "Unsorted")
    subcategory = str(vc.get("subcategory") or "Requires_Human_Review")
    try:
        confidence = float(vc.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    signals = vc.get("signals") or []
    best_guess = vc.get("best_guess")
    flagged = subcategory == "Requires_Human_Review"
    duplicate_of = result.get("duplicate_of")
    proposed = result.get("proposed_filename")
    folder = str(result.get("target_folder_path") or "")
    ocr_text = str(result.get("extracted_text") or "")

    esc = html.escape
    parts = ['<div class="card">']

    thumb = _thumbnail_data_uri(image_lookup.get(name))
    if thumb:
        parts.append(f'<div class="thumb"><img src="{thumb}" alt="{esc(name)}"></div>')
    else:
        parts.append('<div class="thumb"><div class="note">No thumbnail '
                     '(image missing or over 1&nbsp;MB)</div></div>')

    parts.append('<div class="body">')
    dup_badge = (f'<span class="badge">Duplicate of {esc(str(duplicate_of))}</span>'
                 if duplicate_of else "")
    vision_badge = ('<span class="badge vision">Gemini vision used</span>'
                    if result.get("vision_used") else "")
    parts.append(f'<div class="name">{esc(name)}{dup_badge}{vision_badge}</div>')

    colour = "green" if confidence >= 0.60 else "amber"
    width = max(0, min(100, int(round(confidence * 100))))
    parts.append(
        f'<div class="class">{esc(category)} / {esc(subcategory)} '
        f'<span class="bar"><span class="fill {colour}" '
        f'style="display:block;width:{width}%"></span></span>'
        f'<span class="conf">{confidence:.2f}</span></div>'
    )

    if flagged:
        chips = ""
        if best_guess:
            chips += f'<span class="chip guess">best guess: {esc(str(best_guess))}</span>'
        for sig in signals:
            chips += f'<span class="chip">{esc(str(sig))}</span>'
        if chips:
            parts.append(f'<div class="chips">{chips}</div>')

    if proposed:
        parts.append(f'<div class="propose">{esc(str(proposed))} &rarr; {esc(folder)}</div>')
    else:
        parts.append('<div class="propose">Human review &mdash; no rename proposed '
                     f'&rarr; {esc(folder)}</div>')

    excerpt = ocr_text[:OCR_EXCERPT_CHARS]
    if len(ocr_text) > OCR_EXCERPT_CHARS:
        excerpt += "…"
    parts.append(f'<div class="ocr">{esc(excerpt)}</div>' if excerpt
                 else '<div class="ocr">(no OCR text)</div>')
    parts.append("</div></div>")
    return "".join(parts)


def render_html(reports: list[dict], image_lookup: dict[str, str]) -> str:
    """Render one self-contained HTML page for the given scan payloads."""
    all_results: list[dict] = []
    sources: list[str] = []
    for payload in reports:
        sources.append(str(payload.get("source") or "scan"))
        all_results.extend(payload.get("results") or [])

    total = len(all_results)
    classified = sum(
        1 for r in all_results
        if (r.get("visual_classification") or {}).get("subcategory")
        != "Requires_Human_Review"
    )
    flagged = total - classified
    duplicates = sum(1 for r in all_results if r.get("duplicate_of"))
    vision_used = sum(1 for r in all_results if r.get("vision_used"))

    esc = html.escape
    cards = "".join(_card(r, image_lookup) for r in all_results)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<title>Hermes Organizer — scan report</title>
<style>{_CSS}</style></head>
<body>
<h1>Hermes Organizer — scan report</h1>
<div class="summary">Sources: {esc("; ".join(sources))}<br>
Scanned: <b>{total}</b> &middot; Classified: <b>{classified}</b> &middot;
Flagged for review: <b>{flagged}</b> &middot; Duplicates: <b>{duplicates}</b> &middot;
Gemini vision used: <b>{vision_used}</b><br>
Read-only: proposals only — nothing was renamed, moved, or deleted.</div>
{cards}
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Hermes Organizer v0.2 — render scan JSON(s) as one HTML report")
    ap.add_argument("--report", action="append", required=True,
                    help="Scan JSON payload (repeatable)")
    ap.add_argument("--image-dir", action="append", default=[],
                    help="Folder holding the scanned images, for thumbnails (repeatable)")
    ap.add_argument("--out", required=True, help="HTML report path to write")
    args = ap.parse_args(argv)

    reports = []
    for path in args.report:
        with open(path, encoding="utf-8") as fh:
            reports.append(json.load(fh))
    lookup = build_image_lookup(args.image_dir)
    page = render_html(reports, lookup)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    total = sum(len(r.get("results") or []) for r in reports)
    print(f"Wrote {args.out} ({len(reports)} report(s), {total} file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
