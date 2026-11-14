"""OCR + vision layer for Hermes Organizer v0.3.

Live: local RapidOCR (ONNX, runs on CPU, no key, no cloud).
Live: Gemini vision bridge (VisionBridge) on the Google Gemini API free
tier — stdlib urllib only, no SDK. Key from GEMINI_API_KEY (fallback
VISION_API_KEY), sent only as the x-goog-api-key header; when no env key
is set, the bridge falls back to the stored custom.gemini connector
credential via the authd surrogate (placed as ?key= by
dynamic_credentials, swapped for the real key on egress). The key or
surrogate is never logged or stored. Vision is a second opinion used
only for files OCR leaves in review (or with very thin OCR text), or on
every file with --vision always; OCR-only mode sends nothing anywhere.
PDFs: skipped unless poppler is installed (pdf2image). Reported honestly.

v0.6 adds a second provider: Cloudflare Workers AI (Llama 3.2 11B
Vision) via the native /ai/run endpoint — same prompt, same
OcrResult shape, same retry/backoff treatment. Provider selection via
VISION_PROVIDER (``cloudflare`` default, ``gemini``). Cloudflare
token from CLOUDFLARE_API_TOKEN, or the stored custom.cloudflare
connector credential via the authd surrogate (Bearer placement); the
account ID from CLOUDFLARE_ACCOUNT_ID or the file
~/.config/hermes-organizer/cloudflare_account_id. The token needs
Workers AI Read + Edit (the dashboard's "Create a Workers AI API
Token" template pre-fills both). Free tier: 10,000 Neurons/day,
reset 00:00 UTC; error 3036 (HTTP 429) means the day's allowance is
spent and is never retried. Live-verified 2026-11-13: the
OpenAI-compatible /ai/v1/chat/completions endpoint rejects image
calls (HTTP 400, code 3030), so the bridge uses the native /ai/run
endpoint and reads result.response. One image cost ~40 Neurons live.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .prompts import VISION_PROMPT

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Images larger than this are downscaled before upload (free-tier friendly).
_MAX_UPLOAD_BYTES = 1_500_000
_MAX_SIDE_PX = 1568

# Verified live 2026-11-12 with the connected free-tier key:
# gemini-2.5-flash answers generateContent with HTTP 404 ("no longer
# available to new users") and gemini-3.8-flash with HTTP 503 (high
# demand) at that time; gemini-3.5-flash works. Override with GEMINI_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# Cloudflare Workers AI vision (v0.6) — Llama 3.2 11B Vision via the
# native /ai/run endpoint. Live-verified 2026-11-13: the
# OpenAI-compatible /ai/v1/chat/completions endpoint rejects image
# calls (HTTP 400, code 3030), while POST
# /client/v4/accounts/{account_id}/ai/run/{model} with {"messages": [...]}
# answers with result.response. Authorization: Bearer <token>.
# Free tier: 10,000 Neurons/day, reset 00:00 UTC; one image cost
# ~40 Neurons live (~250 images/day inside the free allowance).
# Override the model with CLOUDFLARE_MODEL.
DEFAULT_CLOUDFLARE_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
# Display label for OcrResult.engine / audit logs — the raw model ID
# (@cf/meta/...-instruct) is noisy there.
CLOUDFLARE_ENGINE_LABEL = "cloudflare-llama-3.2-11b-vision"
_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4/accounts"

# Stored-credential fallback (custom.gemini connector, placement ?key=).
_VAULT_CREDENTIAL = "custom.gemini"
_VAULT_HOSTS = ["generativelanguage.googleapis.com"]
_DYNAMIC_CREDENTIALS_PATH = "/opt/muse/skills/custom-connector-setup/bin"


class VisionError(Exception):
    """Vision call failed (network, bad response, unsupported file)."""


class VisionRateLimited(VisionError):
    """Gemini free tier returned HTTP 429 after all retries."""


class VisionOverloaded(VisionError):
    """Gemini free tier returned HTTP 503 (high demand) after all retries."""


class VisionDailyLimit(VisionRateLimited):
    """Cloudflare daily free allowance used up (error 3036, HTTP 429).

    The allowance resets at 00:00 UTC, so retrying cannot help — the
    bridge raises this on the first attempt and never retries it.
    """


class VisionAuthError(VisionError):
    """No key set, or the key was rejected (HTTP 400/403)."""


def _truncate(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


_CLOUDFLARE_ACCOUNT_ID_FILE = os.path.expanduser(
    "~/.config/hermes-organizer/cloudflare_account_id"
)


def _read_account_id_file() -> str | None:
    """Account ID from the local config file, or None when absent.

    The file holds the plain 32-hex-char Account ID (not a secret);
    it is never committed to the repo. The env var wins when both exist.
    """
    try:
        with open(_CLOUDFLARE_ACCOUNT_ID_FILE, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value or None


@dataclass
class OcrResult:
    text: str
    engine: str
    char_count: int


class LocalOcr:
    """RapidOCR wrapper. Engine is loaded lazily on first use."""

    def __init__(self) -> None:
        self._engine = None

    def _load(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def extract(self, path: str) -> OcrResult:
        engine = self._load()
        result, _ = engine(path)
        if not result:
            return OcrResult(text="", engine="rapidocr-local", char_count=0)
        # result: list of [box, text, score]
        lines = [str(item[1]) for item in result if len(item) > 1 and item[1]]
        text = "\n".join(lines)
        return OcrResult(text=text, engine="rapidocr-local", char_count=len(text))


class VisionBridge:
    """Vision bridge with two providers (v0.6): Cloudflare Workers AI
    (Llama 3.2 11B Vision, default) and Gemini.

    stdlib urllib only — no provider SDKs. Provider selection via the
    ``provider`` argument or the VISION_PROVIDER env var (``gemini`` or
    ``cloudflare``; anything else raises ValueError).

    Gemini: key from GEMINI_API_KEY (fallback VISION_API_KEY) or the
    constructor, sent ONLY as the ``x-goog-api-key`` header; when no key
    is set at all, the bridge falls back to the stored custom.gemini
    connector credential, resolved per request through the authd
    surrogate (``?key=`` placement) and never logged, printed, or
    stored anywhere.

    Cloudflare: token from CLOUDFLARE_API_TOKEN, or the stored
    custom.cloudflare connector credential via the authd surrogate
    (Bearer placement); account ID from CLOUDFLARE_ACCOUNT_ID or the
    file ~/.config/hermes-organizer/cloudflare_account_id. The token
    travels only as the ``Authorization: Bearer`` header and is never
    logged, printed, or stored anywhere. Model from CLOUDFLARE_MODEL
    or the default ``@cf/meta/llama-3.2-11b-vision-instruct``.

    Shared behaviour: at least ``min_interval`` seconds between calls,
    exponential backoff on HTTP 429 and HTTP 503 (up to 3 retries
    each), 180s request timeout (image calls are slow; 60s timed out
    live on the Gemini free tier). Cloudflare error 3036 (daily free
    allowance spent) is never retried — the allowance only resets at
    00:00 UTC.
    """

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        min_interval: float = 4.0,
        backoff_base: float = 2.0,
    ) -> None:
        resolved = (
            provider or os.environ.get("VISION_PROVIDER") or "cloudflare"
        ).strip().lower()
        if resolved not in ("gemini", "cloudflare"):
            raise ValueError(
                f"unknown vision provider {resolved!r} — set "
                "VISION_PROVIDER to 'gemini' or 'cloudflare'"
            )
        self.provider = resolved
        if self.provider == "cloudflare":
            # api_key doubles as the Cloudflare API token when the
            # caller passes one explicitly; env vars are the normal path.
            # With no env token, the stored custom.cloudflare connector
            # credential resolves per request via the authd surrogate.
            self.api_token = api_key or os.environ.get("CLOUDFLARE_API_TOKEN")
            self.account_id = os.environ.get(
                "CLOUDFLARE_ACCOUNT_ID"
            ) or _read_account_id_file()
            self.model = (
                model
                or os.environ.get("CLOUDFLARE_MODEL")
                or DEFAULT_CLOUDFLARE_MODEL
            )
            self.engine_label = CLOUDFLARE_ENGINE_LABEL
            # Gemini-only attributes, kept so shared code can read them.
            self.api_key = None
        else:
            self.api_key = (
                api_key
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("VISION_API_KEY")
            )
            self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
            self.engine_label = self.model
            self.api_token = None
            self.account_id = None
        self.min_interval = min_interval
        self.backoff_base = backoff_base
        self._last_call = 0.0  # monotonic time of the last request attempt
        # Set when a 400/403 (or a missing key) is seen, so the CLI can print
        # one warning line instead of one per file. Never holds key material.
        self.auth_error: str | None = None
        # Tri-state cache for the vault probe (None = not probed yet). The
        # probe is one local authd socket call, no request to Google.
        self._vault_checked: bool = False
        self._vault_available: bool = False

    def _vault_url(self, base_url: str) -> str | None:
        """Build the request URL with the stored-credential surrogate.

        Returns None when the vault credential cannot be resolved (authd
        unreachable, connector not connected). The returned URL contains an
        hsurr: surrogate, never the real key — never log or print it.
        A fresh surrogate is fetched per call; surrogates may be single-use.
        """
        try:
            if _DYNAMIC_CREDENTIALS_PATH not in sys.path:
                sys.path.insert(0, _DYNAMIC_CREDENTIALS_PATH)
            from dynamic_credentials import (  # noqa: PLC0415 - lazy import
                DynamicCredentialError,
                url_with_surrogate_query_param,
            )

            return url_with_surrogate_query_param(
                base_url, _VAULT_CREDENTIAL, allowed_hosts=_VAULT_HOSTS
            )
        except (ImportError, OSError, DynamicCredentialError):
            return None

    def _vault_ready(self) -> bool:
        """Probe once whether the stored credential resolves."""
        if not self._vault_checked:
            self._vault_checked = True
            probe = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            )
            self._vault_available = self._vault_url(probe) is not None
        return self._vault_available

    def _vault_token_ready(self) -> bool:
        """Probe once whether the stored custom.cloudflare token resolves."""
        if not self._vault_checked:
            self._vault_checked = True
            try:
                if _DYNAMIC_CREDENTIALS_PATH not in sys.path:
                    sys.path.insert(0, _DYNAMIC_CREDENTIALS_PATH)
                from dynamic_credentials import (  # noqa: PLC0415 - lazy import
                    DynamicCredentialError,
                    add_surrogate_to_request,
                )

                probe = urllib.request.Request(
                    "https://api.cloudflare.com/client/v4/user/tokens/verify"
                )
                add_surrogate_to_request(
                    probe, "custom.cloudflare", allowed_hosts=["api.cloudflare.com"]
                )
                self._vault_available = True
            except (ImportError, OSError, DynamicCredentialError):
                self._vault_available = False
        return self._vault_available

    def available(self) -> bool:
        if self.provider == "cloudflare":
            return bool(self.account_id) and (
                bool(self.api_token) or self._vault_token_ready()
            )
        return bool(self.api_key) or self._vault_ready()

    # -- request plumbing -------------------------------------------------

    def _wait_for_slot(self) -> None:
        """Free-tier pacing: minimum gap between request attempts."""
        if self.min_interval <= 0:
            return
        wait_for = self._last_call + self.min_interval - time.monotonic()
        if wait_for > 0:
            time.sleep(wait_for)

    def _prepare_image(self, path: str) -> tuple[bytes, str]:
        """Read image bytes, downscaling with Pillow when the file is large.

        Returns (bytes, mime_type). Files over ~1.5 MB are resized so the
        longest side is at most 1568 px and re-encoded as JPEG quality 85
        in memory; smaller files go up untouched.
        """
        ext = os.path.splitext(path)[1].lower()
        mime = _MIME_BY_EXT.get(ext)
        if mime is None:
            raise VisionError(f"unsupported image type for vision: {ext or '(none)'}")
        size = os.path.getsize(path)
        if size <= _MAX_UPLOAD_BYTES:
            with open(path, "rb") as fh:
                return fh.read(), mime
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow is installed
            raise VisionError("Pillow is required to downscale large images") from exc
        with Image.open(path) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            if max(img.size) > _MAX_SIDE_PX:
                img.thumbnail((_MAX_SIDE_PX, _MAX_SIDE_PX), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"

    def _post(self, payload: dict) -> dict:
        """One POST attempt, dispatched to the configured provider.

        Raises VisionRateLimited (429) / VisionDailyLimit (Cloudflare
        3036) / VisionOverloaded (503 or other 5xx) / VisionAuthError /
        VisionError; callers handle retries. Credential material (env
        key/token, vault surrogate URL) is never logged or put in an
        error.
        """
        if self.provider == "cloudflare":
            return self._post_cloudflare(payload)
        return self._post_gemini(payload)

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError) -> float | None:
        """Retry-After header as seconds, or None when absent/unparseable."""
        try:
            value = exc.headers.get("Retry-After")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - headers may be absent
            return None
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _post_gemini(self, payload: dict) -> dict:
        """One Gemini POST attempt. Raises VisionRateLimited (429) /
        VisionOverloaded (503) / VisionAuthError / VisionError; callers
        handle retries. The key (env) travels only as the x-goog-api-key
        header; the vault path instead builds a surrogated URL
        (?key=hsurr:...) and sends no header. Neither the key nor the
        surrogated URL is ever logged or put in an error."""
        base_url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
            url = base_url
        else:
            surrogated = self._vault_url(base_url)
            if surrogated is None:
                self.auth_error = (
                    "No Gemini API key set — set GEMINI_API_KEY "
                    "(free key from Google AI Studio) or connect Gemini"
                )
                raise VisionAuthError(self.auth_error)
            url = surrogated
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        self._wait_for_slot()
        self._last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - best effort only
                pass
            if exc.code == 429:
                err = VisionRateLimited(
                    f"Gemini free tier rate limit (HTTP 429): {_truncate(body)}"
                )
                err.retry_after = self._retry_after(exc)  # type: ignore[attr-defined]
                raise err from exc
            if exc.code == 503:
                # v0.5: the free tier answers 503 ("high demand") when
                # overloaded — the beyond-vision folder test hit 7 of them
                # in two runs. Same treatment as 429: retryable, with the
                # Retry-After header honoured when the server sends one.
                err = VisionOverloaded(
                    f"Gemini free tier overloaded (HTTP 503): {_truncate(body)}"
                )
                err.retry_after = self._retry_after(exc)  # type: ignore[attr-defined]
                raise err from exc
            if exc.code in (400, 403):
                self.auth_error = (
                    "Gemini API key missing or invalid "
                    f"(HTTP {exc.code}) — check GEMINI_API_KEY or the "
                    "connected custom.gemini credential"
                )
                raise VisionAuthError(self.auth_error) from exc
            raise VisionError(
                f"Gemini request failed (HTTP {exc.code}): {_truncate(body)}"
            ) from exc
        except TimeoutError as exc:
            # A read timeout surfaces raw (not wrapped in URLError); convert
            # it so callers see the documented VisionError type.
            raise VisionError(
                f"Gemini request timed out after 180s: {exc}"
            ) from exc
        except urllib.error.URLError as exc:
            raise VisionError(f"Gemini request failed (network): {exc.reason}") from exc

    def _apply_vault_bearer(self, req: urllib.request.Request) -> None:
        """Attach the stored custom.cloudflare token as a Bearer surrogate.

        Raises VisionAuthError when the connector is not connected.
        """
        try:
            if _DYNAMIC_CREDENTIALS_PATH not in sys.path:
                sys.path.insert(0, _DYNAMIC_CREDENTIALS_PATH)
            from dynamic_credentials import (  # noqa: PLC0415 - lazy import
                DynamicCredentialError,
                add_surrogate_to_request,
            )

            add_surrogate_to_request(
                req, "custom.cloudflare", allowed_hosts=["api.cloudflare.com"]
            )
        except (ImportError, OSError, DynamicCredentialError) as exc:
            self.auth_error = (
                "No Cloudflare API token set — set CLOUDFLARE_API_TOKEN "
                "or connect Cloudflare (custom.cloudflare)"
            )
            raise VisionAuthError(self.auth_error) from exc

    def _post_cloudflare(self, payload: dict) -> dict:
        """One Cloudflare Workers AI /ai/run attempt.

        POSTs {"messages": [...]} to
        /client/v4/accounts/{account_id}/ai/run/{model} with
        ``Authorization: Bearer <token>`` (env token, or the stored
        custom.cloudflare credential via the authd surrogate). The
        token is never logged or put in an error. Error mapping (per
        the official errors page): HTTP 429 carrying internal code
        3036 means the day's free allocation of 10,000 Neurons is
        spent — VisionDailyLimit, never retried; any other 429 (3040,
        capacity) is retryable; 5xx is VisionOverloaded; 400/403 is
        VisionAuthError. The 3036/3040 codes are detected by scanning
        the response body.
        """
        if not self.account_id:
            self.auth_error = (
                "No Cloudflare Account ID set — set "
                "CLOUDFLARE_ACCOUNT_ID or save it to "
                "~/.config/hermes-organizer/cloudflare_account_id"
            )
            raise VisionAuthError(self.auth_error)
        url = (
            f"{_CLOUDFLARE_API_BASE}/{self.account_id}"
            f"/ai/run/{self.model}"
        )
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        if not self.api_token:
            self._apply_vault_bearer(req)
        self._wait_for_slot()
        self._last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - best effort only
                pass
            if exc.code == 429:
                if "3036" in body:
                    # Daily free allowance spent — resets 00:00 UTC.
                    # Retrying cannot help; the caller must not retry.
                    raise VisionDailyLimit(
                        "Cloudflare daily free allowance used up "
                        "(error 3036, HTTP 429) — resets 00:00 UTC; "
                        "upgrade to Workers Paid or wait"
                    ) from exc
                err = VisionRateLimited(
                    f"Cloudflare capacity limit (HTTP 429): {_truncate(body)}"
                )
                err.retry_after = self._retry_after(exc)  # type: ignore[attr-defined]
                raise err from exc
            if exc.code >= 500:
                err = VisionOverloaded(
                    "Cloudflare overloaded "
                    f"(HTTP {exc.code}): {_truncate(body)}"
                )
                err.retry_after = self._retry_after(exc)  # type: ignore[attr-defined]
                raise err from exc
            if exc.code in (400, 403):
                hint = ""
                if "5016" in body or "Model Agreement" in body:
                    # Llama 3.2 model terms not agreed. Accept them in
                    # the dashboard (Workers AI model page), or submit
                    # the prompt "agree" to the model once.
                    hint = (
                        " — Llama 3.2 model terms not agreed: accept them "
                        "in the Cloudflare dashboard (Workers AI model "
                        "page), or submit the prompt 'agree' to the model"
                    )
                self.auth_error = (
                    "Cloudflare API token invalid or not allowed "
                    f"(HTTP {exc.code}) — check CLOUDFLARE_API_TOKEN and "
                    f"CLOUDFLARE_ACCOUNT_ID{hint}"
                )
                raise VisionAuthError(self.auth_error) from exc
            raise VisionError(
                f"Cloudflare request failed (HTTP {exc.code}): {_truncate(body)}"
            ) from exc
        except TimeoutError as exc:
            # A read timeout surfaces raw (not wrapped in URLError);
            # convert it so callers see the documented VisionError type.
            raise VisionError(
                f"Cloudflare request timed out after 180s: {exc}"
            ) from exc
        except urllib.error.URLError as exc:
            raise VisionError(
                f"Cloudflare request failed (network): {exc.reason}"
            ) from exc

    # -- public API -------------------------------------------------------

    def _payload(self, image_bytes: bytes, mime: str) -> dict:
        """Provider-specific request body for one prepared image."""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        if self.provider == "cloudflare":
            # Native /ai/run endpoint: the model is in the URL, the
            # body carries only the messages.
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{b64}"
                                },
                            },
                        ],
                    }
                ],
            }
        return {
            "contents": [
                {
                    "parts": [
                        {"text": VISION_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": b64,
                            }
                        },
                    ]
                }
            ]
        }

    def extract(self, path: str) -> OcrResult:
        """Describe + transcribe one image via the configured provider.
        Returns the same OcrResult shape as LocalOcr so the classifier
        can use it directly. Raises VisionAuthError without making a
        request when no credentials are set.
        """
        if not self.available():
            if self.provider == "cloudflare":
                self.auth_error = (
                    "No Cloudflare credentials set — set "
                    "CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, "
                    "or connect Cloudflare (custom.cloudflare) and save "
                    "the Account ID to "
                    "~/.config/hermes-organizer/cloudflare_account_id"
                )
            else:
                self.auth_error = (
                    "No Gemini API key set — set GEMINI_API_KEY "
                    "(free key from Google AI Studio) or connect Gemini"
                )
            raise VisionAuthError(self.auth_error)
        image_bytes, mime = self._prepare_image(path)
        payload = self._payload(image_bytes, mime)
        last_exc: VisionError | None = None
        # Initial attempt + up to 3 retries on 429 and on 503/5xx.
        # VisionDailyLimit (Cloudflare 3036) is never retried: the
        # allowance only resets at 00:00 UTC.
        for attempt in range(4):
            try:
                data = self._post(payload)
                break
            except VisionDailyLimit:
                raise
            except (VisionRateLimited, VisionOverloaded) as exc:
                last_exc = exc
                if attempt == 3:
                    if self.provider == "cloudflare":
                        if isinstance(exc, VisionOverloaded):
                            raise VisionOverloaded(
                                "Cloudflare overloaded (HTTP 5xx) "
                                "after 3 retries"
                            ) from exc
                        raise VisionRateLimited(
                            "Cloudflare rate limit (HTTP 429) after 3 retries"
                        ) from exc
                    if isinstance(exc, VisionOverloaded):
                        raise VisionOverloaded(
                            "Gemini free tier overloaded (HTTP 503) "
                            "after 3 retries"
                        ) from exc
                    raise VisionRateLimited(
                        "Gemini free tier rate limit (HTTP 429) after 3 retries"
                    ) from exc
                delay = self.backoff_base * (2 ** attempt)
                retry_after = getattr(exc, "retry_after", None)
                if retry_after:
                    try:
                        delay = min(float(retry_after), 60.0)
                    except (TypeError, ValueError):
                        pass
                if delay > 0:
                    time.sleep(delay)
        else:  # pragma: no cover - loop always breaks or raises
            raise last_exc  # type: ignore[misc]
        text = self._response_text(data)
        return OcrResult(text=text, engine=self.engine_label, char_count=len(text))

    def _response_text(self, data: dict) -> str:
        """Extract the description text from a provider response.

        Gemini: candidates[0].content.parts[*].text. Cloudflare
        (native /ai/run): result.response, with the abandoned
        OpenAI-compatible choices[0].message.content as a fallback.
        Missing parts mean an empty transcription, never a crash.
        """
        if self.provider == "cloudflare":
            try:
                content = data["result"]["response"]
            except (KeyError, TypeError):
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    return ""
            return content if isinstance(content, str) else ""
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            return ""
        texts = [str(p.get("text", "")) for p in parts if isinstance(p, dict)]
        return "\n".join(t for t in texts if t)


def extract_text(path: str, ocr: LocalOcr | None = None) -> OcrResult:
    """Best-effort text extraction for one file. PDFs need poppler; otherwise skipped."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_path  # type: ignore

            pages = convert_from_path(path, first_page=1, last_page=3, dpi=200)
        except Exception as exc:  # noqa: BLE001 - poppler missing is expected
            return OcrResult(text="", engine=f"pdf-skipped: {exc}", char_count=0)
        ocr = ocr or LocalOcr()
        texts = []
        for page in pages:
            # One temp file per page (v0.2 fix: the old fixed path overwrote
            # itself on multi-page PDFs and raced concurrent scans).
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp.name
            tmp.close()
            try:
                page.save(tmp_path)
                texts.append(ocr.extract(tmp_path).text)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        joined = "\n".join(texts)
        return OcrResult(text=joined, engine="rapidocr-local+pdf2image", char_count=len(joined))
    if ext in SUPPORTED_IMAGE_EXTS:
        return (ocr or LocalOcr()).extract(path)
    return OcrResult(text="", engine="unsupported-type", char_count=0)
