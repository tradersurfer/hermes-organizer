"""Tests for the Gemini vision bridge (v0.3) and the watcher fallback.

No live Gemini calls here: urllib.request.urlopen is mocked everywhere.
The live test waits for a real GEMINI_API_KEY (see README v0.3 section).
"""
import base64
import io
import json
import os
import sys
import urllib.error
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.ocr import (  # noqa: E402
    OcrResult,
    VisionAuthError,
    VisionBridge,
    VisionError,
    VisionRateLimited,
)

FAKE_KEY = "test-key-123"
LOGO_JPG = os.path.join(REPO_ROOT, "samples", "drive", "logo.jpg")


def _success_payload(text="TRANSCRIBED TEXT\nDESCRIPTION TEXT"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeResponse:
    """Minimal urlopen stand-in: context manager with .read() and .status."""

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


def _bridge(**kwargs):
    kwargs.setdefault("min_interval", 0)
    kwargs.setdefault("backoff_base", 0)
    return VisionBridge(provider="gemini", api_key=FAKE_KEY, **kwargs)


def _http_error(url, code, body=b"{}"):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


# --- bridge: success path ---

def test_gemini_success_returns_text_and_uses_header_key():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["key_header"] = req.get_header("X-goog-api-key")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(_success_payload())

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.extract(LOGO_JPG)
    assert "TRANSCRIBED TEXT" in result.text
    assert "DESCRIPTION TEXT" in result.text
    assert result.engine == "gemini-3.5-flash"
    assert result.char_count == len(result.text)
    assert "gemini-3.5-flash" in captured["url"]
    assert ":generateContent" in captured["url"]
    assert FAKE_KEY not in captured["url"]
    assert captured["key_header"] == FAKE_KEY
    part = captured["body"]["contents"][0]["parts"][1]["inline_data"]
    assert part["mime_type"] == "image/jpeg"
    assert part["data"]  # base64 image bytes present


def test_gemini_missing_parts_returns_empty_text_not_crash():
    bridge = _bridge()
    with mock.patch("urllib.request.urlopen",
                    return_value=FakeResponse({"candidates": []})):
        result = bridge.extract(LOGO_JPG)
    assert result.text == ""
    assert result.char_count == 0


# --- bridge: rate limits and auth ---

def test_gemini_429_then_success_retries():
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(req.full_url, 429, b'{"error": "slow down"}')
        return FakeResponse(_success_payload())

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=flaky):
        result = bridge.extract(LOGO_JPG)
    assert calls["n"] == 2
    assert "TRANSCRIBED TEXT" in result.text


def test_gemini_timeout_raises_vision_error():
    """A raw read timeout surfaces as VisionError, not a raw traceback."""
    def slow(req, timeout=None):
        raise TimeoutError("The read operation timed out")

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=slow):
        with pytest.raises(VisionError):
            bridge.extract(LOGO_JPG)


def test_gemini_persistent_429_raises_rate_limited():
    def always_429(req, timeout=None):
        raise _http_error(req.full_url, 429, b"{}")

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=always_429):
        with pytest.raises(VisionRateLimited):
            bridge.extract(LOGO_JPG)


def test_gemini_400_raises_auth_error_and_sets_flag():
    def bad_key(req, timeout=None):
        raise _http_error(req.full_url, 400, b'{"error": "API key not valid"}')

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=bad_key):
        with pytest.raises(VisionAuthError):
            bridge.extract(LOGO_JPG)
    assert bridge.auth_error is not None
    assert FAKE_KEY not in bridge.auth_error


def test_missing_key_raises_without_request(monkeypatch):
    # Fully keyless: no env key AND no stored credential resolves.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    monkeypatch.setattr(VisionBridge, "_vault_url", lambda self, url: None)
    bridge = VisionBridge(provider="gemini", api_key=None, min_interval=0, backoff_base=0)
    assert not bridge.available()
    with mock.patch("urllib.request.urlopen") as fake:
        with pytest.raises(VisionAuthError):
            bridge.extract(LOGO_JPG)
        fake.assert_not_called()


# --- bridge: stored-credential (vault) fallback ---

def test_vault_fallback_used_when_no_env_key(monkeypatch):
    """No env key + a resolving vault: the request goes out on the
    surrogated URL with no x-goog-api-key header."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    fake_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash:generateContent?key=hsurr:fake-surrogate"
    )
    monkeypatch.setattr(
        VisionBridge, "_vault_url", lambda self, url: fake_url)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["key_header"] = req.get_header("X-goog-api-key")
        return FakeResponse(_success_payload())

    bridge = VisionBridge(provider="gemini", api_key=None, min_interval=0, backoff_base=0)
    assert bridge.available()
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.extract(LOGO_JPG)
    assert "TRANSCRIBED TEXT" in result.text
    assert result.engine == "gemini-3.5-flash"
    assert captured["url"] == fake_url
    assert captured["key_header"] is None


def test_vault_unavailable_raises_auth_error(monkeypatch):
    """No env key and the vault does not resolve: no request is made."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    monkeypatch.setattr(VisionBridge, "_vault_url", lambda self, url: None)
    bridge = VisionBridge(provider="gemini", api_key=None, min_interval=0, backoff_base=0)
    assert not bridge.available()
    with mock.patch("urllib.request.urlopen") as fake:
        with pytest.raises(VisionAuthError):
            bridge.extract(LOGO_JPG)
        fake.assert_not_called()


# --- bridge: downscale ---

def test_large_image_is_downscaled_before_upload(tmp_path):
    from PIL import Image

    # 3000x2000 noisy PNG — well over the 1.5 MB upload threshold.
    data = os.urandom(3000 * 2000 * 3)
    img = Image.frombytes("RGB", (3000, 2000), data)
    big = tmp_path / "big.png"
    img.save(big, format="PNG")
    assert big.stat().st_size > 1_500_000

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(_success_payload())

    bridge = _bridge()
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        bridge.extract(str(big))
    part = captured["body"]["contents"][0]["parts"][1]["inline_data"]
    assert part["mime_type"] == "image/jpeg"
    raw = base64.b64decode(part["data"])
    with Image.open(io.BytesIO(raw)) as sent:
        assert max(sent.size) <= 1568


# --- watcher fallback ---

class StubVision:
    """Fake bridge: returns fixed text, or raises, and records calls."""

    model = "gemini-3.5-flash"

    def __init__(self, text=None, exc=None):
        self.text = text
        self.exc = exc
        self.calls = []

    def available(self):
        return True

    def extract(self, path):
        self.calls.append(path)
        if self.exc is not None:
            raise self.exc
        return OcrResult(text=self.text, engine=self.model, char_count=len(self.text))


def test_watcher_fallback_rescues_review_file(tmp_path):
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    shutil.copy(LOGO_JPG, tmp_path / "logo.jpg")
    stub = StubVision(
        text="YourCreditScores CreditHeroScore EQUIFAX TransUnion experian 763")
    results = scan_local_folder(str(tmp_path), audit_log=None, vision=stub)
    assert len(results) == 1
    r = results[0]
    assert r.visual_classification.subcategory == "Credit Report"
    assert r.vision_used is True
    assert r.ocr_engine == "rapidocr-local+gemini-3.5-flash"
    assert len(stub.calls) == 1


def test_watcher_vision_error_keeps_ocr_result(tmp_path):
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    shutil.copy(LOGO_JPG, tmp_path / "logo.jpg")
    stub = StubVision(exc=VisionRateLimited("rate limited"))
    log = tmp_path / "audit.jsonl"
    results = scan_local_folder(str(tmp_path), audit_log=str(log), vision=stub)
    assert len(results) == 1
    r = results[0]
    assert r.visual_classification.subcategory == "Requires_Human_Review"
    assert r.vision_used is False
    assert len(stub.calls) == 1
    assert "vision-error" in log.read_text()


def test_vision_attempt_logged_even_when_not_kept(tmp_path):
    """Vision runs on a review file but adds no signal: the OCR-only
    result stands, and the audit log still records the attempt."""
    import json
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    shutil.copy(LOGO_JPG, tmp_path / "logo.jpg")
    stub = StubVision(text="a plain photograph of a landscape")
    log = tmp_path / "audit.jsonl"
    results = scan_local_folder(str(tmp_path), audit_log=str(log), vision=stub)
    assert len(results) == 1
    assert results[0].vision_used is False
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    vision_entries = [e for e in entries if e["action"] == "vision"]
    assert len(vision_entries) == 1
    assert vision_entries[0]["kept"] is False
    assert vision_entries[0]["engine"] == "rapidocr-local+gemini-3.5-flash"


def test_vision_always_calls_bridge_but_auto_skips_classified(tmp_path):
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    shutil.copy(
        os.path.join(REPO_ROOT, "mock_samples", "IMG_0042_receipt.png"),
        tmp_path / "IMG_0042_receipt.png",
    )
    # Always mode: bridge runs even though OCR already classified it. The
    # vision text adds no keywords, so the OCR result stands (vision_used
    # stays False) — but the call happened.
    stub_always = StubVision(text="a plain photograph of a landscape")
    results = scan_local_folder(
        str(tmp_path), audit_log=None, vision=stub_always, vision_mode="always")
    assert len(stub_always.calls) == 1
    assert results[0].visual_classification.subcategory == "Receipt"
    assert results[0].vision_used is False
    # Auto mode: OCR classified it with plenty of text, so no vision call.
    stub_auto = StubVision(text="a plain photograph of a landscape")
    results = scan_local_folder(str(tmp_path), audit_log=None, vision=stub_auto)
    assert len(stub_auto.calls) == 0
    assert results[0].visual_classification.subcategory == "Receipt"
