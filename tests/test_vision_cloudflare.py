"""Tests for the Cloudflare Workers AI vision provider (v0.6).

No live Cloudflare calls here: urllib.request.urlopen is mocked
everywhere. The live test waits for a real CLOUDFLARE_API_TOKEN +
CLOUDFLARE_ACCOUNT_ID (see README v0.6 section).
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
    DEFAULT_CLOUDFLARE_MODEL,
    OcrResult,
    VisionAuthError,
    VisionBridge,
    VisionDailyLimit,
    VisionError,
    VisionOverloaded,
    VisionRateLimited,
)

FAKE_TOKEN = "test-cf-token-123"
FAKE_ACCOUNT = "test-acct-123"
LOGO_JPG = os.path.join(REPO_ROOT, "samples", "drive", "logo.jpg")

CF_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{FAKE_ACCOUNT}/ai/run/{DEFAULT_CLOUDFLARE_MODEL}"
)


def _success_payload(text="TRANSCRIBED TEXT\nDESCRIPTION TEXT"):
    return {"result": {"response": text}}


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


def _bridge(monkeypatch, **kwargs):
    """Cloudflare bridge with env credentials, no pacing, no backoff."""
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", FAKE_ACCOUNT)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    # Isolate from a real ~/.config/hermes-organizer account-ID file.
    monkeypatch.setattr(
        "hermes_organizer.ocr._read_account_id_file", lambda: None
    )
    kwargs.setdefault("min_interval", 0)
    kwargs.setdefault("backoff_base", 0)
    return VisionBridge(provider="cloudflare", api_key=FAKE_TOKEN, **kwargs)


def _http_error(url, code, body=b"{}"):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


# --- bridge: success path ---

def test_cloudflare_success_returns_text_and_uses_bearer_token(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth_header"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(_success_payload())

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.extract(LOGO_JPG)
    assert "TRANSCRIBED TEXT" in result.text
    assert "DESCRIPTION TEXT" in result.text
    assert result.engine == "cloudflare-llama-3.2-11b-vision"
    assert result.char_count == len(result.text)
    assert captured["url"] == CF_URL
    assert captured["auth_header"] == f"Bearer {FAKE_TOKEN}"
    assert FAKE_TOKEN not in captured["url"]
    body = captured["body"]
    # Native /ai/run endpoint: the model is in the URL, never in the body.
    assert "model" not in body
    assert DEFAULT_CLOUDFLARE_MODEL in captured["url"]
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert "TRANSCRIPTION" in parts[0]["text"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert len(parts[1]["image_url"]["url"]) > len("data:image/jpeg;base64,")


def test_cloudflare_missing_result_returns_empty_text_not_crash(monkeypatch):
    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen",
                    return_value=FakeResponse({"result": {}})):
        result = bridge.extract(LOGO_JPG)
    assert result.text == ""
    assert result.char_count == 0


# --- bridge: rate limits, daily allowance, auth ---

def test_cloudflare_3036_daily_limit_raises_once_no_retries(monkeypatch):
    """Error 3036 = the day's 10,000 free Neurons are spent. The
    allowance only resets at 00:00 UTC, so retrying cannot help:
    exactly one attempt, then VisionDailyLimit."""
    calls = {"n": 0}

    def limited(req, timeout=None):
        calls["n"] += 1
        raise _http_error(
            req.full_url, 429,
            b'{"success": false, "errors": [{"code": 3036, "message": '
            b'"You have used up your daily free allocation of 10,000 '
            b'neurons."}]}',
        )

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=limited):
        with pytest.raises(VisionDailyLimit) as excinfo:
            bridge.extract(LOGO_JPG)
    assert calls["n"] == 1
    assert "3036" in str(excinfo.value)
    # VisionDailyLimit is a VisionRateLimited, so existing callers that
    # catch the parent type still treat it as rate-limited.
    assert isinstance(excinfo.value, VisionRateLimited)


def test_cloudflare_3040_capacity_retries_then_raises(monkeypatch):
    """Error 3040 = temporarily out of capacity: retryable, like a
    plain 429. Four attempts (initial + 3 retries), then the error."""
    calls = {"n": 0}

    def busy(req, timeout=None):
        calls["n"] += 1
        raise _http_error(
            req.full_url, 429,
            b'{"success": false, "errors": [{"code": 3040, "message": '
            b'"Capacity temporarily exceeded, please try again."}]}',
        )

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=busy):
        with pytest.raises(VisionRateLimited):
            bridge.extract(LOGO_JPG)
    assert calls["n"] == 4


def test_cloudflare_429_then_success_retries(monkeypatch):
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(req.full_url, 429, b'{"error": "slow down"}')
        return FakeResponse(_success_payload())

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=flaky):
        result = bridge.extract(LOGO_JPG)
    assert calls["n"] == 2
    assert "TRANSCRIBED TEXT" in result.text


def test_cloudflare_5xx_retries_then_raises_overloaded(monkeypatch):
    calls = {"n": 0}

    def broken(req, timeout=None):
        calls["n"] += 1
        raise _http_error(req.full_url, 500, b'{"error": "boom"}')

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=broken):
        with pytest.raises(VisionOverloaded):
            bridge.extract(LOGO_JPG)
    assert calls["n"] == 4


def test_cloudflare_timeout_raises_vision_error(monkeypatch):
    def slow(req, timeout=None):
        raise TimeoutError("The read operation timed out")

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=slow):
        with pytest.raises(VisionError):
            bridge.extract(LOGO_JPG)


def test_cloudflare_403_terms_not_agreed_names_it(monkeypatch):
    """HTTP 403 carrying internal code 5016 means the Llama 3.2 model
    terms have not been agreed in the dashboard — the error must say so."""
    def forbidden(req, timeout=None):
        raise _http_error(
            req.full_url, 403,
            b'{"success": false, "errors": [{"code": 5016, "message": '
            b'"User has not agreed to Llama3.2 model terms"}]}',
        )

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=forbidden):
        with pytest.raises(VisionAuthError) as excinfo:
            bridge.extract(LOGO_JPG)
    assert "5016" in str(excinfo.value) or "terms" in str(excinfo.value).lower()
    assert bridge.auth_error is not None
    assert FAKE_TOKEN not in bridge.auth_error


# --- bridge: provider selection and credentials ---

def test_provider_defaults_to_cloudflare(monkeypatch):
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    monkeypatch.setattr(
        "hermes_organizer.ocr._read_account_id_file", lambda: None
    )
    bridge = VisionBridge(api_key="test-token-123", min_interval=0, backoff_base=0)
    assert bridge.provider == "cloudflare"
    assert bridge.engine_label == "cloudflare-llama-3.2-11b-vision"


def test_provider_switches_on_vision_provider_env(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", FAKE_ACCOUNT)
    bridge = VisionBridge(min_interval=0, backoff_base=0)
    assert bridge.provider == "cloudflare"
    assert bridge.available()
    assert bridge.engine_label == "cloudflare-llama-3.2-11b-vision"


def test_unknown_provider_raises_value_error(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        VisionBridge(min_interval=0, backoff_base=0)


def test_cloudflare_missing_credentials_raises_without_request(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    monkeypatch.setattr(
        "hermes_organizer.ocr._read_account_id_file", lambda: None
    )
    bridge = VisionBridge(provider="cloudflare", min_interval=0, backoff_base=0)
    assert not bridge.available()
    with mock.patch("urllib.request.urlopen") as fake:
        with pytest.raises(VisionAuthError) as excinfo:
            bridge.extract(LOGO_JPG)
        fake.assert_not_called()
    assert "CLOUDFLARE_API_TOKEN" in str(excinfo.value)
    assert "CLOUDFLARE_ACCOUNT_ID" in str(excinfo.value)


def test_cloudflare_missing_account_only_raises_without_request(monkeypatch):
    """Token set but no account ID: still unavailable, still no request."""
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", FAKE_TOKEN)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    monkeypatch.setattr(
        "hermes_organizer.ocr._read_account_id_file", lambda: None
    )
    bridge = VisionBridge(provider="cloudflare", min_interval=0, backoff_base=0)
    assert not bridge.available()
    with mock.patch("urllib.request.urlopen") as fake:
        with pytest.raises(VisionAuthError):
            bridge.extract(LOGO_JPG)
        fake.assert_not_called()


def test_cloudflare_account_id_from_config_file(monkeypatch, tmp_path):
    """Account ID falls back to the local config file when unset."""
    import hermes_organizer.ocr as ocr_mod

    cfg = tmp_path / "cloudflare_account_id"
    cfg.write_text("file-acct-999\n", encoding="utf-8")
    monkeypatch.setattr(ocr_mod, "_CLOUDFLARE_ACCOUNT_ID_FILE", str(cfg))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", FAKE_TOKEN)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    bridge = VisionBridge(provider="cloudflare", min_interval=0, backoff_base=0)
    assert bridge.account_id == "file-acct-999"
    assert bridge.available()


def test_cloudflare_account_id_env_wins_over_config_file(monkeypatch, tmp_path):
    import hermes_organizer.ocr as ocr_mod

    cfg = tmp_path / "cloudflare_account_id"
    cfg.write_text("file-acct-999\n", encoding="utf-8")
    monkeypatch.setattr(ocr_mod, "_CLOUDFLARE_ACCOUNT_ID_FILE", str(cfg))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", FAKE_ACCOUNT)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    bridge = VisionBridge(provider="cloudflare", min_interval=0, backoff_base=0)
    assert bridge.account_id == FAKE_ACCOUNT


def test_cloudflare_vault_token_used_when_no_env_token(monkeypatch):
    """No env token: the stored custom.cloudflare credential attaches
    via the authd surrogate instead of failing."""
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", FAKE_ACCOUNT)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    monkeypatch.setattr(
        "hermes_organizer.ocr._read_account_id_file", lambda: None
    )
    monkeypatch.setattr(VisionBridge, "_vault_token_ready", lambda self: True)

    def fake_apply(self, req):
        req.add_header("Authorization", "Bearer hsurr:test-token")

    monkeypatch.setattr(VisionBridge, "_apply_vault_bearer", fake_apply)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["auth_header"] = req.get_header("Authorization")
        return FakeResponse(_success_payload())

    bridge = VisionBridge(provider="cloudflare", min_interval=0, backoff_base=0)
    assert bridge.available()
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.extract(LOGO_JPG)
    assert "TRANSCRIBED TEXT" in result.text
    assert captured["auth_header"] == "Bearer hsurr:test-token"


def test_cloudflare_vault_unavailable_raises_without_request(monkeypatch):
    """No env token and no stored credential: unavailable, no request."""
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", FAKE_ACCOUNT)
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    monkeypatch.setattr(
        "hermes_organizer.ocr._read_account_id_file", lambda: None
    )
    monkeypatch.setattr(VisionBridge, "_vault_token_ready", lambda self: False)
    bridge = VisionBridge(provider="cloudflare", min_interval=0, backoff_base=0)
    assert not bridge.available()
    with mock.patch("urllib.request.urlopen") as fake:
        with pytest.raises(VisionAuthError):
            bridge.extract(LOGO_JPG)
        fake.assert_not_called()


# --- bridge: downscale reuse ---

def test_cloudflare_large_image_is_downscaled_before_upload(monkeypatch, tmp_path):
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

    bridge = _bridge(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        bridge.extract(str(big))
    url = captured["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as sent:
        assert max(sent.size) <= 1568


# --- watcher: engine label flows through ---

class StubVisionCF:
    """Fake Cloudflare bridge: fixed text, engine_label set."""

    model = "@cf/meta/llama-3.2-11b-vision-instruct"
    engine_label = "cloudflare-llama-3.2-11b-vision"

    def __init__(self, text):
        self.text = text
        self.calls = []

    def available(self):
        return True

    def extract(self, path):
        self.calls.append(path)
        return OcrResult(text=self.text, engine=self.engine_label,
                         char_count=len(self.text))


def test_watcher_uses_cloudflare_engine_label(tmp_path):
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    shutil.copy(LOGO_JPG, tmp_path / "logo.jpg")
    stub = StubVisionCF(
        text="YourCreditScores CreditHeroScore EQUIFAX TransUnion experian 763")
    results = scan_local_folder(str(tmp_path), audit_log=None, vision=stub)
    assert len(results) == 1
    r = results[0]
    assert r.visual_classification.subcategory == "Credit Report"
    assert r.vision_used is True
    assert r.ocr_engine == "rapidocr-local+cloudflare-llama-3.2-11b-vision"
    assert len(stub.calls) == 1
