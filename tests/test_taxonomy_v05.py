"""Tests for the v0.5 taxonomy: Product Photo, and the Gemini 503 retry.

The vision texts below are the REAL texts copied verbatim from the
beyond-vision folder scan (sample_reports/beyond-vision-scan.json, 18
files, 0/18 classified in v0.4). The folder holds apparel product photos
(leggings / sports-bra / t-shirt mockups on plain backgrounds); OCR finds
0-5 chars in them, so classification depends entirely on the vision
description — and v0.4 had no category for that description to land in.
The 5 files that got anything reached only 0.50 via the "logo" keyword
(best guess Brand Assets). A guard test below re-checks the copied texts
against the scan JSON when it is present, so a transcription slip fails
loudly instead of silently testing the wrong text.
"""
import io
import json
import os
import sys
import urllib.error
from datetime import datetime
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.classifier import classify  # noqa: E402
from hermes_organizer.naming import propose_filename  # noqa: E402
from hermes_organizer.ocr import (  # noqa: E402
    OcrResult,
    VisionBridge,
    VisionError,
    VisionOverloaded,
)
from hermes_organizer.taxonomy import CONFIDENCE_THRESHOLD  # noqa: E402

# --- real vision texts, verbatim from beyond-vision-scan.json ---

# attachment (15).jpg — t-shirt mockup; OCR found nothing usable.
TSHIRT_TEXT = (
    'SN VS TRANSCRIPTION: VISION DESCRIPTION: This image is a digital '
    'graphic mockup showing the front and back views of a black '
    'short-sleeved t-shirt. Both views feature abstract red tribal-style '
    'line designs along with a stylized red logo reading "VISION" located '
    'on the front chest area and lower back right hem. The t-shirt design '
    'is displayed side by side against a light-colored background.'
)

# attachment (13).jpg — leggings + sports bra, camouflage print.
LEGGINGS_CAMO_TEXT = (
    "TRANSCRIPTION: No readable text. DESCRIPTION: This is a product "
    "image displaying a set of matching women's athletic wear against a "
    "plain white background. On the left are full-length leggings with a "
    "solid pink waistband, and on the right is a sports bra shown from "
    "the back view with black trim. Both pieces feature a pink and dark "
    "charcoal camouflage pattern, along with a stylized crescent-shaped "
    "logo visible on the waistband, near the ankle, and on the back of "
    "the bra."
)

# attachment (11).jpg — leggings + sports bra, glitch-effect print.
LEGGINGS_GLITCH_TEXT = (
    "TRANSCRIPTION: No readable text. DESCRIPTION: This image is a "
    "product mockup displaying a matching women's athletic wear set "
    "against a plain white background, featuring high-waisted leggings "
    "on the left and a sports bra on the right. Both items feature a "
    "colorful, holographic glitch-effect print incorporating a stylized "
    "face wiping a tear. The leggings have a black waistband centered "
    "with a purple crescent-shaped logo, while the sports bra includes "
    "black trim and a small white logo on the chest."
)

# attachment (8).jpg — leopard-print leggings.
LEGGINGS_LEOPARD_TEXT = (
    "TRANSCRIPTION: No readable text. DESCRIPTION: This image is a "
    "product mockup showing a pair of high-waisted women's athletic "
    "leggings set against a plain white background. The leggings feature "
    "a vibrant pink faux-fur or textured print with bold black leopard "
    "spots, alongside a solid black waistband. A small pink abstract "
    "logo icon is centered on the front of the waistband."
)

# attachment (5).jpg — rash guard; note the "mock-up" spelling.
RASH_GUARD_TEXT = (
    "TRANSCRIPTION: No readable text. DESCRIPTION: This image is a 3D "
    "digital product mock-up of a women's long-sleeved cropped rash "
    "guard or athletic shirt set against a plain light gray background. "
    "The garment displays a bold, abstract liquid-marble pattern "
    "featuring vibrant shades of pink, yellow, blue, and black. A small "
    "black crescent-like logo icon is visible on the upper left chest "
    "area of the top."
)

VISION_TEXTS = {
    "attachment (15).jpg": TSHIRT_TEXT,
    "attachment (13).jpg": LEGGINGS_CAMO_TEXT,
    "attachment (11).jpg": LEGGINGS_GLITCH_TEXT,
    "attachment (8).jpg": LEGGINGS_LEOPARD_TEXT,
    "attachment (5).jpg": RASH_GUARD_TEXT,
}

# Exact confidences the v0.5 keyword weights produce on the texts above
# (score / (score + 3)): t-shirt 5 -> 0.625, camo 16 -> 0.842,
# glitch 15 -> 0.833, leopard 13 -> 0.812, rash guard 9 -> 0.75.
EXPECTED = {
    "attachment (15).jpg": 0.625,
    "attachment (13).jpg": 0.842,
    "attachment (11).jpg": 0.833,
    "attachment (8).jpg": 0.812,
    "attachment (5).jpg": 0.75,
}


def test_vision_texts_match_scan_json():
    """Guard: the constants above must be verbatim copies of the scan
    JSON. Skipped when the (gitignored) scan JSON is not present."""
    path = os.path.join(REPO_ROOT, "sample_reports", "beyond-vision-scan.json")
    if not os.path.exists(path):
        pytest.skip("beyond-vision-scan.json not present")
    data = json.load(open(path))
    by_name = {r["metadata"]["original_name"]: r for r in data["results"]}
    for name, text in VISION_TEXTS.items():
        assert by_name[name]["extracted_text"] == text, name
        assert by_name[name]["vision_used"] is True, name


def test_product_photo_tshirt_mockup():
    c = classify("attachment (15).jpg", TSHIRT_TEXT)
    assert c.subcategory == "Product Photo"
    assert c.confidence == EXPECTED["attachment (15).jpg"]
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_product_photo_leggings_camo():
    c = classify("attachment (13).jpg", LEGGINGS_CAMO_TEXT)
    assert c.subcategory == "Product Photo"
    assert c.confidence == EXPECTED["attachment (13).jpg"]


def test_product_photo_leggings_glitch():
    c = classify("attachment (11).jpg", LEGGINGS_GLITCH_TEXT)
    assert c.subcategory == "Product Photo"
    assert c.confidence == EXPECTED["attachment (11).jpg"]


def test_product_photo_leggings_leopard():
    c = classify("attachment (8).jpg", LEGGINGS_LEOPARD_TEXT)
    assert c.subcategory == "Product Photo"
    assert c.confidence == EXPECTED["attachment (8).jpg"]


def test_product_photo_mock_up_spelling():
    # "mock-up" (with hyphen) must match — "mockup" alone does not, and
    # without it this file scores only 6 (0.667); with it, 9 (0.75).
    c = classify("attachment (5).jpg", RASH_GUARD_TEXT)
    assert c.subcategory == "Product Photo"
    assert c.confidence == EXPECTED["attachment (5).jpg"]
    assert "text:mock-up" in c.signals
    assert "text:garment" in c.signals


def test_all_five_vision_texts_classify():
    for name, text in VISION_TEXTS.items():
        c = classify(name, text)
        assert c.subcategory == "Product Photo", name
        assert c.confidence >= CONFIDENCE_THRESHOLD, name


def test_no_cross_talk():
    # A logo-only description on a white background must still land
    # Brand Assets, not Product Photo: Brand scores logo 3 + wordmark 3
    # (+ filename logo 2) = 8, Product Photo scores only "white
    # background" 2. This is why "plain white background" is NOT a
    # Product Photo keyword — it would double-count one span and let a
    # bare logo outscore Brand Assets 4 to 3.
    logo_on_white = (
        "TRANSCRIPTION: No readable text. DESCRIPTION: This is a logo for "
        "a company. The image shows the company wordmark in bold letters "
        "on a plain white background."
    )
    c = classify("logo.jpg", logo_on_white)
    assert c.subcategory == "Brand Assets"
    assert c.confidence >= CONFIDENCE_THRESHOLD
    assert "text:white background" in c.signals  # the cue is present...
    assert c.subcategory != "Product Photo"  # ...and still loses
    # The v0.4 logo vision text (plain background, no white) also stays.
    logo_plain = (
        "TRANSCRIPTION: 700 CREDIT CLUB EXPERTS\nDESCRIPTION: This is a "
        "logo for 700 Credit Club Experts, a credit repair company. The "
        "image shows the company wordmark in bold letters on a plain "
        "background."
    )
    c = classify("logo.jpg", logo_plain)
    assert c.subcategory == "Brand Assets"
    # A plain store receipt must NOT become a product photo.
    c = classify("IMG_0042.png",
                 "RECEIPT #8812 TOTAL $17.36 VISA **** 4242 THANK YOU")
    assert c.subcategory == "Receipt"
    assert "text:product" not in c.signals
    # A credit report must NOT become a product photo.
    c = classify("credit_client_results.jpg",
                 "YourCreditScores CreditHeroScore EQUIFAX TransUnion "
                 "experian 763 VeryGood")
    assert c.subcategory == "Credit Report"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_boundary_print_and_dress():
    # "print" (5 chars) is word-boundary: it must not match "blueprint".
    # "dress" (4 chars) is word-boundary: it must not match "address".
    c = classify("x.png", "a blueprint of the house at 123 address lane")
    assert c.subcategory == "Requires_Human_Review"
    assert c.confidence == 0.0
    assert c.best_guess is None
    assert "text:print" not in c.signals
    assert "text:dress" not in c.signals
    # ...but both DO match as standalone words.
    c = classify("x.png", "a summer dress on a white background")
    assert "text:dress" in c.signals
    assert "text:white background" in c.signals
    assert c.best_guess == "Product Photo"
    c = classify("x.png", "a textured print on a plain white background")
    assert "text:print" in c.signals
    assert c.best_guess == "Product Photo"


def test_naming_product_photo():
    mtime = datetime(2026, 11, 12)
    _, folder = propose_filename("x.png", "Product Photo",
                                 "DATE: 10/12/2026", mtime)
    assert folder == "/Work/Product Photos/2026/"
    # Product words must flow into the topic, not be skipped as doc-type
    # words — and the vision-section labels must not become the topic
    # (before the v0.5 naming fix these files got "readable_text").
    for name in ("attachment (13).jpg", "attachment (11).jpg",
                 "attachment (8).jpg"):
        proposed, _ = propose_filename(name, "Product Photo",
                                       VISION_TEXTS[name], mtime)
        assert "ProductPhoto" in proposed, name
        assert "product" in proposed.lower(), name
        assert "readable_text" not in proposed, name
    # The t-shirt file's topic comes from its own words, not the labels.
    proposed, _ = propose_filename("attachment (15).jpg", "Product Photo",
                                   TSHIRT_TEXT, mtime)
    assert "VISION_image" not in proposed
    assert "readable_text" not in proposed


def test_taxonomy_json_synced():
    """taxonomy_rules.json mirrors taxonomy.py for the new subcategory."""
    data = json.load(open(os.path.join(REPO_ROOT, "taxonomy_rules.json")))
    assert data["version"] == "0.5"
    photos = data["categories"]["Technical & Work Assets"]["Product Photos"]
    assert photos["folder"] == "/Work/Product Photos/{year}/"
    from hermes_organizer.taxonomy import RULES  # noqa: E402

    assert set(photos["keywords"]) == set(RULES["Product Photo"]["keywords"])


# --- Gemini 503 retry (mocked urlopen, no live calls) ---

FAKE_KEY = "test-key-123"
LOGO_JPG = os.path.join(REPO_ROOT, "samples", "drive", "logo.jpg")


def _success_payload(text="TRANSCRIBED TEXT\nDESCRIPTION TEXT"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeResponse:
    """Minimal urlopen stand-in: context manager with .read()."""

    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _http_error(url, code, body=b"{}"):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


def _bridge(**kwargs):
    kwargs.setdefault("min_interval", 0)
    kwargs.setdefault("backoff_base", 0)
    return VisionBridge(provider="gemini", api_key=FAKE_KEY, **kwargs)


def test_gemini_503_then_success_retries():
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(
                req.full_url, 503, b'{"error": "high demand"}')
        return FakeResponse(_success_payload())

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=flaky):
        result = bridge.extract(LOGO_JPG)
    assert calls["n"] == 2
    assert "TRANSCRIBED TEXT" in result.text


def test_gemini_persistent_503_raises_overloaded():
    calls = {"n": 0}

    def always_503(req, timeout=None):
        calls["n"] += 1
        raise _http_error(req.full_url, 503, b'{"error": "high demand"}')

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=always_503):
        with pytest.raises(VisionOverloaded) as excinfo:
            bridge.extract(LOGO_JPG)
    # Initial attempt + 3 retries, then a distinct error naming the 503.
    assert calls["n"] == 4
    assert "503" in str(excinfo.value)
    assert isinstance(excinfo.value, VisionError)


def test_watcher_503_error_keeps_ocr_result(tmp_path):
    """A 503 that survives all retries is logged as vision-error and the
    batch keeps the OCR-only result — it never dies."""
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    class StubVision:
        model = "gemini-3.5-flash"

        def available(self):
            return True

        def extract(self, path):
            raise VisionOverloaded(
                "Gemini free tier overloaded (HTTP 503) after 3 retries")

    shutil.copy(LOGO_JPG, tmp_path / "logo.jpg")
    log = tmp_path / "audit.jsonl"
    results = scan_local_folder(
        str(tmp_path), audit_log=str(log), vision=StubVision())
    assert len(results) == 1
    r = results[0]
    assert r.visual_classification.subcategory == "Requires_Human_Review"
    assert r.vision_used is False
    assert "vision-error" in log.read_text()
    assert "503" in log.read_text()


# --- full mock scan + v0.4 regression ---

def test_mock_scan_v05():
    """Full mock scan: the 9 originals keep their EXACT confidences and
    the new product mock lands Product Photo."""
    from hermes_organizer.watcher import scan_local_folder

    results = scan_local_folder(
        os.path.join(REPO_ROOT, "mock_samples"), audit_log=None)
    by_name = {r.metadata.original_name: r for r in results}
    assert len(results) == 10
    expected = {
        "IMG_0042_receipt.png": ("Receipt", 0.80),
        "IMG_0043_invoice.png": ("Invoice", 0.786),
        "IMG_0099_contract.png": ("Contract", 0.842),
        "IMG_0101_crypto.png": ("Crypto Transaction", 0.786),
        "IMG_0102_domain.png": ("Domain Research", 0.833),
        "Screenshot_code.png": ("Code Screenshot", 0.778),
        "Screenshot_code_editor.png": ("Code Screenshot", 0.846),
        "logo_brand.png": ("Brand Assets", 0.769),
        "scan_whiteboard.png": ("Whiteboard Brainstorm", 0.833),
        "IMG_0103_product.png": ("Product Photo", 0.778),
    }
    for name, (sub, conf) in expected.items():
        r = by_name[name]
        assert r.visual_classification.subcategory == sub, name
        assert r.visual_classification.confidence == conf, name


def test_v04_scans_reproduce():
    """The v0.4 sample-scan files re-classify the same under v0.5.

    Subcategory must match for every file. Confidence matches too, EXCEPT
    the three credit-report files: their stored (CamelCase-split) text
    scores 0.70 / 0.70 / 0.769, while the live scan recorded 0.786 /
    0.786 / 0.824 — because classify() ran on the RAW OCR text, where
    "TransUnion" and "CreditHeroScore" were jammed single words that the
    stored text splits apart. That is a stored-text artifact, not a v0.5
    regression: pre-v0.5 code gives the same 0.70 on the stored text
    (verified via git stash), and the jammed raw text still scores 0.786.
    """
    for path in ("sample_reports/drive-scan-v04.json",
                 "sample_reports/drive-live-scan-v04.json"):
        full = os.path.join(REPO_ROOT, path)
        if not os.path.exists(full):
            pytest.skip(f"{path} not present")
        data = json.load(open(full))
        for r in data["results"]:
            name = r["metadata"]["original_name"]
            old = r["visual_classification"]
            c = classify(name, r.get("extracted_text", ""))
            assert c.subcategory == old["subcategory"], (path, name)
            if old["subcategory"] == "Credit Report":
                assert c.confidence in (0.70, 0.769), (path, name)
            else:
                assert round(c.confidence, 3) == round(
                    old["confidence"], 3), (path, name)
