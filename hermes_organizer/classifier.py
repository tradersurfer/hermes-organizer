"""Taxonomy classifier for Hermes Organizer v0.2.

Scores each subcategory by weighted keyword hits in the OCR text plus
filename signals. No LLM, no invented evidence: every point comes from a
matched keyword or filename pattern, and the matched signals are returned
with the result so the call is auditable.

Confidence = best subcategory score / (best score + 3.0), i.e. a single
weak hit scores low, several strong hits score high. Under 0.60 the file
goes to Requires_Human_Review, but the top-scoring subcategory is still
recorded as best_guess so a human sees the closest call.

Matching (v0.2, boundary-aware — one matcher shared by scoring and the
signals list, so signals never lie about what matched):
- phrase keywords (contain a space, e.g. "credit score") -> plain substring.
- short single words (len <= 6, e.g. "tax", "paid", "irs") -> word-boundary
  regex `(?<!\\w)keyword(s)?(?!\\w)` so "tax" never matches "taxi",
  "paid" never matches "unpaid", "irs" never matches "first".
- long single words (len > 6, e.g. "agreement") -> plain substring, because
  OCR often jams words together ("SERVICEAGREEMENT" must still match).
Regexes are pre-compiled once at import; filename signals stay substring.
"""
from __future__ import annotations

import re

from .schemas import VisualClassification
from .taxonomy import CONFIDENCE_THRESHOLD, FILENAME_SIGNALS, RULES

_CAMEL_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _build_matcher(keyword: str) -> tuple[str, str, re.Pattern | None]:
    """Return (kind, lowered keyword, compiled regex or None)."""
    kw = keyword.lower()
    if " " in kw:
        return ("substring", kw, None)
    if len(kw) <= 6:
        return ("regex", kw, re.compile(r"(?<!\w)" + re.escape(kw) + r"(s)?(?!\w)"))
    return ("substring", kw, None)


# Pre-compiled once at import: subcategory -> keyword -> matcher.
_MATCHERS: dict[str, dict[str, tuple[str, str, re.Pattern | None]]] = {
    sub: {kw: _build_matcher(kw) for kw in rule["keywords"]}
    for sub, rule in RULES.items()
}


def _keyword_matches(keyword: str, sub: str, lowered_texts: list[str]) -> bool:
    """True if the keyword matches ANY variant. v0.2 fix: the CamelCase split
    must only ADD signal, never remove it — "TransUnion" matches "transunion"
    on the raw text, while "YourCreditScores" matches "credit score" on the
    split text. Both variants are boundary-checked the same way."""
    kind, kw, pattern = _MATCHERS[sub][keyword]
    for lowered_text in lowered_texts:
        if kind == "regex":
            if pattern.search(lowered_text) is not None:  # type: ignore[union-attr]
                return True
        elif kw in lowered_text:
            return True
    return False


def _score_text(lowered_texts: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for sub, rule in RULES.items():
        total = 0.0
        for keyword, weight in rule["keywords"].items():
            if _keyword_matches(keyword, sub, lowered_texts):
                total += weight
        if total:
            scores[sub] = total
    return scores


def _score_filename(name: str) -> dict[str, float]:
    lowered = name.lower()
    scores: dict[str, float] = {}
    for pattern, sub, weight in FILENAME_SIGNALS:
        if pattern in lowered:
            scores[sub] = scores.get(sub, 0.0) + weight
    return scores


def classify(original_name: str, extracted_text: str) -> VisualClassification:
    cleaned = clean_text(extracted_text)
    # Match against BOTH the raw text and the CamelCase-split text: the split
    # only ever adds matches (see _keyword_matches). Signals stay honest —
    # they use the exact same union check as scoring.
    raw_lowered = re.sub(r"\s+", " ", extracted_text).strip().lower()[:2000]
    lowered_texts = [raw_lowered, cleaned.lower()]
    text_scores = _score_text(lowered_texts)
    name_scores = _score_filename(original_name)

    combined: dict[str, float] = {}
    for sub, score in list(text_scores.items()) + list(name_scores.items()):
        combined[sub] = combined.get(sub, 0.0) + score

    signals: list[str] = []
    for sub, rule in RULES.items():
        for keyword in rule["keywords"]:
            if _keyword_matches(keyword, sub, lowered_texts):
                signals.append(f"text:{keyword}")
    for pattern, sub, _ in FILENAME_SIGNALS:
        if pattern in original_name.lower():
            signals.append(f"filename:{pattern}")

    if not combined:
        return VisualClassification(
            category="Unsorted",
            subcategory="Requires_Human_Review",
            confidence=0.0,
            signals=signals,
            best_guess=None,
        )

    best_sub = max(combined, key=lambda s: combined[s])
    best_score = combined[best_sub]
    # Diminishing-returns curve: 3 pts -> 0.50, 6 pts -> 0.67, 9 pts -> 0.75,
    # 12 pts -> 0.80. A bare filename hint alone stays under threshold.
    confidence = round(best_score / (best_score + 3.0), 3)
    category = RULES[best_sub]["category"]

    if confidence < CONFIDENCE_THRESHOLD:
        return VisualClassification(
            category="Unsorted",
            subcategory="Requires_Human_Review",
            confidence=confidence,
            signals=signals,
            best_guess=best_sub,
        )
    return VisualClassification(
        category=category,
        subcategory=best_sub,
        confidence=confidence,
        signals=signals,
        best_guess=None,
    )


def is_unorganized(name: str, parent_names: list[str] | None = None) -> bool:
    """Handoff filter: IMG_*, screenshots, scans, root parents, #unorganized."""
    lowered = name.lower()
    if any(p in lowered for p in ("img_", "screenshot", "scan_", "dsc_", "pxl_")):
        return True
    if parent_names and any(p.lower() in ("root", "my drive") for p in parent_names):
        return True
    if "#unorganized" in lowered:
        return True
    return False


def clean_text(text: str, limit: int = 2000) -> str:
    # Split CamelCase first ("YourCreditScores" -> "Your Credit Scores",
    # "ValueError" -> "Value Error") so jammed OCR words match phrases and
    # word-boundary keywords. Every consumer (scoring, signals, naming)
    # benefits because classify() and watcher both go through here.
    text = _CAMEL_SPLIT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]
