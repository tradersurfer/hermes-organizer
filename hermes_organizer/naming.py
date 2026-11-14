"""Proposed filename + target folder builder (proposals only, v0.1).

Format: YYYY-MM-DD_<Subcategory>_<Topic>.<ext>
- Date: first date found in OCR text (MM/DD/YYYY, YYYY-MM-DD, "Oct 12, 2026"),
  else the file's modified time.
- Topic: the strongest matched keyword, else the first meaningful OCR words,
  sanitized to [A-Za-z0-9_-], max 24 chars. Never invents a vendor name.
  v0.6.1: descriptive words (garment / print / colour / material) are
  preferred over generic verbs ("The image presents..."), and
  deduplicate_filenames() guarantees no scan emits the same
  (filename, folder) twice — repeats get a _2, _3, ... suffix.
  v0.7.1: deduplicate_filenames() also takes the filenames already
  present in the target folders (``existing``), read at scan time —
  so a proposal colliding with a file that is already there gets its
  suffix at proposal time instead of being refused at execute time.
- Target folder: taxonomy folder template with {year} filled in.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from .taxonomy import REVIEW_FOLDER, RULES

_DATE_PATTERNS = [
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),  # 2026-10-12
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),  # 10/12/2026
    re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    ),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}

_STOPWORDS = {
    "the", "and", "for", "with", "your", "you", "this", "that", "from",
    "total", "amount", "date", "invoice", "receipt", "payment", "paid",
    "thank", "thanks", "please", "account", "number", "balance", "due",
    "terms", "net", "between",
}


# Short all-caps acronyms that carry meaning as a filename topic
# (kept by _topic_from_text; other short all-caps tokens are OCR noise).
_ACRONYMS = {"BTC", "ETH", "USD", "EUR", "IRS", "EIN", "NDA", "API", "LLC", "INC", "SSN"}


# Dates preceded (within ~30 chars) by one of these are DOB/expiry dates,
# not the document date — skip them when picking the file date.
_SKIP_DATE_MARKERS = (
    "date of birth", "dob", "expires", "expiration", "valid thru", "valid through",
)


def extract_date(text: str, fallback: datetime | None = None) -> datetime:
    # Collect ALL dates from all patterns with their positions, earliest first.
    candidates: list[tuple[int, int, re.Match]] = []
    for idx, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(text):
            candidates.append((m.start(), idx, m))
    candidates.sort(key=lambda c: c[0])
    for start, idx, m in candidates:
        before = text[max(0, start - 30):start].lower()
        if any(marker in before for marker in _SKIP_DATE_MARKERS):
            continue
        try:
            if idx == 0:  # YYYY-MM-DD
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if idx == 1:  # MM/DD/YYYY
                return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            month = _MONTHS[m.group(1).lower()[:3]]  # "Oct 12, 2026"
            return datetime(int(m.group(3)), month, int(m.group(2)))
        except ValueError:
            continue
    return fallback or datetime.now()


# Words that describe what a file *shows* — garment, print, colour,
# material. The topic picker prefers these (v0.6.1 fix: vision texts all
# start "The image presents/depicts..." so the old first-two-words rule
# collapsed 18 apparel files to 7 names). Matching is exact on the
# lowercased word; plurals are listed explicitly.
_TOPIC_GARMENT = {
    "tshirt", "shirt", "shirts", "legging", "leggings", "bra", "bras",
    "top", "tops", "dress", "dresses", "pants", "shorts", "hoodie",
    "hoodies", "jacket", "jackets", "sweater", "sweaters", "skirt",
    "skirts", "blouse", "tank", "jeans", "sleeve", "sleeves", "waistband",
    "neck", "collar", "hem", "crop", "cropped", "sleeved", "sleeveless",
    "sports", "high", "waisted", "waist", "crew",
}
_TOPIC_PRINT = {
    "leopard", "camouflage", "camo", "tiger", "psychedelic", "abstract",
    "floral", "striped", "stripes", "stripe", "checkered", "plaid",
    "graphic", "graphics", "tribal", "holographic", "glitch", "geometric",
    "dotted", "polka", "paisley", "print", "prints", "pattern", "patterns",
    "patterned", "spotted", "spots", "spot", "swirl", "swirls", "face",
    "faces", "animal", "marble", "galaxy", "neon",
}
_TOPIC_COLOR = {
    "orange", "green", "black", "white", "red", "pink", "purple",
    "magenta", "teal", "blue", "yellow", "brown", "charcoal", "gray",
    "grey", "beige", "navy", "maroon", "coral", "turquoise", "lavender",
    "gold", "silver", "multicolor", "colorful", "vibrant", "pastel",
    "mustard", "sage", "blush", "tan",
}
_TOPIC_MATERIAL = {
    "cotton", "polyester", "spandex", "denim", "silk", "wool", "linen",
    "leather", "nylon", "rayon", "velvet", "faux", "fur",
}
_TOPIC_DESCRIPTIVE = (
    _TOPIC_GARMENT | _TOPIC_PRINT | _TOPIC_COLOR | _TOPIC_MATERIAL
)

# Generic nouns: true of almost any file of the type, so they only ever
# fill a topic slot when nothing descriptive exists ("product_mockup"
# beats "Document", but "leggings_leopard" beats both).
_TOPIC_GENERIC = {
    "product", "mockup", "item", "items", "clothing", "apparel", "wear",
    "shot", "studio", "background", "setting", "set", "pair", "piece",
    "pieces", "view", "views", "front", "back", "side", "left", "right",
    "overall", "effect",
}

# Whole words that only ever come from the vision prompt itself
# ("Transcribe ALL visible text faithfully, line by line") or from a
# URL in the transcription ("www.quicksilver.com") — never a topic.
_SKIP_WORDS = {"line", "write", "www", "com", "http", "https"}


def _topic_from_text(text: str, subcategory: str) -> str:
    # Doc-type words: skip any candidate containing one of these substrings,
    # so the topic is the *subject* of the file, not its own document type.
    skip_substrings = (
        "receipt", "invoice", "contract", "agreement", "service",
        "whiteboard", "brainstorm", "screenshot", "terminal", "code",
        "statement", "tax", "billto", "bill", "due",
        # v0.4: doc-type words for the new subcategories, so the topic is
        # the subject of the file, not its own document type.
        "crypto", "bitcoin", "order", "filled", "average", "price",
        "worth", "domain", "logo", "brand", "search", "result",
        "replit", "editor", "design", "cart", "retail", "add",
        # Vision section labels are not topics.
        "transcription", "description",
        # v0.5: the vision prompt's own labels leak into the text
        # ("TRANSCRIPTION: No readable text. DESCRIPTION: ...",
        # "TRANSCRIPTION: VISION DESCRIPTION: ..."). Without these skips
        # the beyond-vision files got the junk topic "readable_text" /
        # "VISION_image". Product words (product, mockup, leggings,
        # garment, apparel) are deliberately NOT skipped — they are the
        # topic for a Product Photo file.
        "readable", "text", "vision",
        # v0.6.1: generic vision verbs — every description opens "The
        # image presents/depicts/appears...", so these can never be the
        # topic or 18 files collapse to a handful of names. Also the
        # prompt echo ("Transcribe ALL visible text faithfully") and
        # "digital rendering", which says nothing about the subject.
        "image", "present", "depict", "appear", "show", "display",
        "featur", "showcas", "seem", "look", "illustrat", "captur",
        "portray", "photo", "picture", "photograph", "digital",
        "render", "transcrib", "visible", "faithful",
    )
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)
    # Candidates: (position, word, score). Descriptive words score 2,
    # generic nouns 0 (fallback only), everything else 1.
    candidates: list[tuple[int, str, int]] = []
    for pos, w in enumerate(words):
        lw = w.lower()
        if lw in _STOPWORDS or lw == subcategory.lower():
            continue
        if lw in _SKIP_WORDS:
            continue
        if any(s in lw for s in skip_substrings):
            continue
        # Short all-caps tokens are usually OCR noise from screenshots
        # ("OCL", "INOV"); real acronyms that carry meaning are kept.
        if w.isupper() and len(w) <= 4 and w not in _ACRONYMS:
            continue
        score = 2 if lw in _TOPIC_DESCRIPTIVE else (0 if lw in _TOPIC_GENERIC else 1)
        candidates.append((pos, w, score))
    if not candidates:
        return "Document"
    # Pick order: first garment word, then first print word, then first
    # colour, then the best remaining descriptive/other words — so the
    # topic reads like the item ("shirt_camouflage", "leggings_leopard")
    # instead of the sentence starter. Each word is used at most once.
    used: set[str] = set()
    picks: list[tuple[int, str]] = []

    def _first_in(vocab: set[str]) -> tuple[int, str] | None:
        for pos, w, _score in candidates:
            if w.lower() in vocab and w.lower() not in used:
                used.add(w.lower())
                return (pos, w)
        return None

    for vocab in (_TOPIC_GARMENT, _TOPIC_PRINT, _TOPIC_COLOR):
        pick = _first_in(vocab)
        if pick is not None:
            picks.append(pick)
        if len(picks) >= 2:
            break
    if len(picks) < 2:
        for pos, w, score in sorted(candidates, key=lambda c: (-c[2], c[0])):
            if w.lower() in used:
                continue
            used.add(w.lower())
            picks.append((pos, w))
            if len(picks) >= 2:
                break
    picks.sort(key=lambda p: p[0])  # topic reads in text order
    # Join whole words only — never cut a word mid-way at the 24-char cap.
    topic = ""
    for _pos, w in picks:
        candidate = f"{topic}_{w}" if topic else w
        if len(candidate) > 24:
            break
        topic = candidate
    return topic or picks[0][1][:24]


def propose_filename(
    original_name: str,
    subcategory: str,
    extracted_text: str,
    modified_time: datetime | None = None,
) -> tuple[str, str]:
    """Returns (proposed_filename, target_folder_path)."""
    ext = os.path.splitext(original_name)[1].lower() or ".png"
    date = extract_date(extracted_text, fallback=modified_time)
    topic = _topic_from_text(extracted_text, subcategory)
    safe_sub = re.sub(r"[^A-Za-z0-9]", "", subcategory)
    filename = f"{date.strftime('%Y-%m-%d')}_{safe_sub}_{topic}{ext}"
    folder = RULES[subcategory]["folder"].format(year=date.strftime("%Y"))
    return filename, folder


def deduplicate_filenames(
    pairs: list[tuple[str | None, str]],
    existing: list[tuple[str | None, str]] | None = None,
) -> list[tuple[str | None, str]]:
    """Make (filename, folder) pairs unique within one scan.

    When a pair would repeat, append _2, _3, ... before the extension
    ("shirt_abstract.jpg" -> "shirt_abstract_2.jpg"), counting up until
    the pair is free — so a scan can never emit two identical
    destinations and executing the renames can never overwrite a file.
    Same filename in *different* folders is fine and left alone.
    Review results (filename None — no rename happens) pass through
    untouched.

    ``existing`` seeds the check with (filename, folder) pairs that
    already live in the target folders (read at scan time — see
    watcher._existing_target_names). A proposal colliding with an
    existing file gets its _2/_3/... suffix at proposal time, so the
    executor's target-name-free check never has to refuse it. Without
    ``existing`` only collisions *within* the scan are deduped.
    """
    seen: set[tuple[str, str]] = set()
    for filename, folder in existing or []:
        if filename is not None:
            seen.add((folder, filename))
    out: list[tuple[str | None, str]] = []
    for filename, folder in pairs:
        if filename is None:
            out.append((filename, folder))
            continue
        stem, dot, ext = filename.rpartition(".")
        candidate = filename
        n = 2
        while (folder, candidate) in seen:
            candidate = f"{stem}_{n}.{ext}" if dot else f"{filename}_{n}"
            n += 1
        seen.add((folder, candidate))
        out.append((candidate, folder))
    return out


def review_proposal(original_name: str) -> tuple[None, str]:
    """Low-confidence files: no rename, review folder only."""
    return None, REVIEW_FOLDER
