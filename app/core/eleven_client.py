from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
from loguru import logger

from app.i18n import tr
from app.utils.paths import APP_NAME, APP_VERSION

BASE_URL = "https://api.elevenlabs.io"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3


class ElevenError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class Unauthorized(ElevenError):
    pass


class PermissionDenied(ElevenError):
    pass


class QuotaExceeded(ElevenError):
    pass


class RateLimited(ElevenError):
    def __init__(self, message: str, retry_after: Optional[float] = None, code: Optional[str] = None) -> None:
        super().__init__(message, 429, code)
        self.retry_after = retry_after


class ApiValidationError(ElevenError):
    pass


class NotFound(ElevenError):
    pass


class TransientError(ElevenError):
    pass


class NetworkError(ElevenError):
    pass


HUMAN_MESSAGES: dict[str, str] = {
    "invalid_api_key": tr("The API key is wrong or revoked."),
    "quota_exceeded": tr("The character quota on this key is used up."),
    "missing_permissions": tr("The key is missing a required permission (check key permissions in ElevenLabs)."),
    "voice_not_found": tr("Voice not found or not available on this account."),
    "model_not_found": tr("Model not found."),
    "max_character_limit_exceeded": tr("Text is over the per-request limit, lower the part size in Settings."),
    "text_too_long": tr("Text is too long for one request, lower the part size."),
    "detected_unusual_activity": tr(
        "ElevenLabs restricted free usage for this account. Contact ElevenLabs support or use a paid plan."
    ),
    "too_many_concurrent_requests": tr("Too many concurrent requests."),
    "system_busy": tr("ElevenLabs is overloaded, try again later."),
    "voice_not_fine_tuned": tr("The voice isn't trained yet."),
    "invalid_output_format": tr("This output format isn't available on your plan."),
    "output_format_not_allowed": tr("This output format isn't available on your plan."),
    "subscription_required": tr("Not available on your current plan."),
    "can_not_use_instant_voice_cloning": tr("Voice cloning isn't available on your current plan."),
}

FIELD_NAMES: dict[str, str] = {
    "text": tr("text"),
    "model_id": tr("model"),
    "voice_id": tr("voice"),
    "voice_settings": tr("voice settings"),
    "stability": tr("stability"),
    "similarity_boost": tr("similarity"),
    "style": tr("style"),
    "speed": tr("speed"),
    "language_code": tr("language code"),
    "seed": "seed",
    "output_format": tr("output format"),
}


@dataclass
class TTSResult:
    audio: bytes
    character_cost: Optional[int]
    request_id: Optional[str]


def _backoff(attempt: int, base: float = 1.0, cap: float = 20.0) -> float:
    return random.uniform(0, min(cap, base * (2 ** attempt))) + 0.5


def _parse_retry_after(resp: requests.Response) -> Optional[float]:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _detail(resp: requests.Response) -> tuple[Optional[str], str]:
    try:
        body = resp.json()
    except ValueError:
        return None, (resp.text or resp.reason or "")[:300]
    detail: Any = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return detail.get("status") or detail.get("code"), str(detail.get("message") or detail)
    if isinstance(detail, list):
        parts = []
        for item in detail[:3]:
            if isinstance(item, dict):
                loc = [str(x) for x in item.get("loc", []) if x not in ("body", "query")]
                field = ", ".join(FIELD_NAMES.get(x, x) for x in loc) or tr("request")
                parts.append(f"{field}: {item.get('msg', '')}")
        return "validation_error", tr("Invalid parameters: ") + "; ".join(parts)
    return None, str(detail)[:300]


class ElevenClient:
    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> None:
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "xi-api-key": api_key.strip(),
            "Accept": "application/json",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        })

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, path: str, *, retries: Optional[int] = None, **kwargs: Any) -> requests.Response:
        retries = self.retries if retries is None else retries
        url = path if path.startswith("http") else BASE_URL + path
        kwargs.setdefault("timeout", self.timeout)
        last: ElevenError = NetworkError(tr("No connection to ElevenLabs"))
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.Timeout:
                last = TransientError(tr("Request timed out ({sec:.0f} s)", sec=self.timeout))
            except requests.ConnectionError as e:
                last = NetworkError(tr("No connection to ElevenLabs: {kind}", kind=e.__class__.__name__))
            except requests.RequestException as e:
                last = TransientError(tr("Request failed: {error}", error=e))
            else:
                if resp.status_code < 400:
                    return resp
                error = self._map_error(resp)
                # short overload 429s are worth waiting out, real rate limits go up to the task
                if isinstance(error, RateLimited) and error.code in ("too_many_concurrent_requests", "system_busy"):
                    wait = error.retry_after if error.retry_after is not None else _backoff(attempt)
                    if attempt < retries and wait <= 15:
                        logger.info("429 {}: retrying in {:.1f} s", error.code, wait)
                        time.sleep(wait)
                        continue
                    raise error
                if not isinstance(error, TransientError):
                    raise error
                last = error
            if attempt < retries:
                delay = _backoff(attempt)
                logger.warning("{} {} - attempt {}/{}: {}. Retrying in {:.1f} s",
                               method, path, attempt + 1, retries + 1, last.message, delay)
                time.sleep(delay)
        raise last

    @staticmethod
    def _map_error(resp: requests.Response) -> ElevenError:
        code, raw = _detail(resp)
        status = resp.status_code
        human = HUMAN_MESSAGES.get(code or "", raw or f"HTTP {status}")
        if status == 429:
            return RateLimited(human or tr("Too many requests (429)"), _parse_retry_after(resp), code)
        # elevenlabs reports an empty quota as 401 quota_exceeded, not 429
        if status == 401 or status == 403:
            if code == "quota_exceeded":
                return QuotaExceeded(human, status, code)
            if code == "missing_permissions":
                return PermissionDenied(human, status, code)
            if code == "detected_unusual_activity":
                return ApiValidationError(human, status, code)
            return Unauthorized(human or tr("The API key is invalid"), status, code)
        if status == 404:
            return NotFound(human or tr("Not found"), status, code)
        if status in (400, 422):
            if code == "quota_exceeded":
                return QuotaExceeded(human, status, code)
            return ApiValidationError(human, status, code)
        if status >= 500:
            return TransientError(tr("ElevenLabs server returned {status}", status=status), status, code)
        return ApiValidationError(f"HTTP {status}: {human}", status, code)

    def get_subscription(self) -> dict:
        return self._request("GET", "/v1/user/subscription").json()

    def list_models(self) -> list[dict]:
        data = self._request("GET", "/v1/models").json()
        return [m for m in data if m.get("can_do_text_to_speech", True)]

    def list_voices(self) -> list[dict]:
        voices: list[dict] = []
        token: Optional[str] = None
        for _ in range(50):
            params: dict[str, Any] = {"page_size": 100}
            if token:
                params["next_page_token"] = token
            data = self._request("GET", "/v2/voices", params=params).json()
            voices.extend(data.get("voices", []))
            token = data.get("next_page_token")
            if not data.get("has_more") or not token:
                break
        return voices

    def get_voice(self, voice_id: str) -> dict:
        return self._request("GET", f"/v1/voices/{voice_id}", retries=1).json()

    def tts(
        self,
        text: str,
        voice_id: str,
        model_id: str,
        voice_settings: dict,
        output_format: str = "mp3_44100_128",
        language_code: str = "",
        seed: Optional[int] = None,
        previous_text: Optional[str] = None,
        next_text: Optional[str] = None,
    ) -> TTSResult:
        body: dict[str, Any] = {"text": text, "model_id": model_id, "voice_settings": voice_settings}
        if language_code:
            body["language_code"] = language_code
        if seed is not None:
            body["seed"] = seed
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        resp = self._request(
            "POST",
            f"/v1/text-to-speech/{voice_id}",
            params={"output_format": output_format},
            json=body,
            headers={"Accept": "audio/mpeg"},
            timeout=max(self.timeout, 30 + len(text) / 100),
        )
        cost = resp.headers.get("character-cost") or resp.headers.get("x-character-count")
        audio = resp.content
        if not audio:
            raise TransientError(tr("The server returned an empty audio file"))
        return TTSResult(
            audio=audio,
            character_cost=int(cost) if cost and cost.isdigit() else None,
            request_id=resp.headers.get("request-id"),
        )

    def add_voice(self, name: str, files: list[Path], description: str = "",
                  remove_background_noise: bool = False) -> str:
        handles = []
        try:
            multipart = []
            for p in files:
                fh = open(p, "rb")
                handles.append(fh)
                multipart.append(("files", (p.name, fh, "audio/mpeg")))
            data = {
                "name": name,
                "description": description,
                "remove_background_noise": "true" if remove_background_noise else "false",
            }
            resp = self._request("POST", "/v1/voices/add", data=data, files=multipart,
                                 retries=0, timeout=120)
            return str(resp.json().get("voice_id", ""))
        finally:
            for fh in handles:
                fh.close()

    def download(self, url: str) -> bytes:
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content


def check_connectivity(timeout: float = 5.0) -> bool:
    try:
        requests.head(BASE_URL, timeout=timeout)
        return True
    except requests.RequestException:
        return False
