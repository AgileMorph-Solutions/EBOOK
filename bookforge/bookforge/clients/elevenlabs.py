"""ElevenLabs text-to-speech client."""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from bookforge.config import get_settings

log = structlog.get_logger(__name__)

_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Default voice settings for all narration audio (overridable per request).
DEFAULT_VOICE_SETTINGS: dict[str, Any] = {
    "stability": 0.40,
    "similarity_boost": 0.78,
    "style": 0.20,
    "use_speaker_boost": True,
    "speed": 0.95,
}


class ElevenLabsError(RuntimeError):
    """ElevenLabs API failure with a safe message for callers."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def merge_voice_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start from defaults; apply non-None override fields."""
    merged = dict(DEFAULT_VOICE_SETTINGS)
    if overrides:
        for key, val in overrides.items():
            if val is not None and key in DEFAULT_VOICE_SETTINGS:
                merged[key] = val
    return merged


def text_to_speech(
    text: str,
    *,
    voice_id: str | None = None,
    model_id: str | None = None,
    voice_settings: dict[str, Any] | None = None,
) -> bytes:
    """Convert text to MP3 bytes via ElevenLabs TTS."""
    s = get_settings()
    key = s.elevenlabs_api_key.strip()
    if not key:
        raise ElevenLabsError("ELEVENLABS_API_KEY is not set")
    cleaned = (text or "").strip()
    if not cleaned:
        raise ElevenLabsError("Cannot synthesize empty text")

    vid = (voice_id or s.elevenlabs_voice_id).strip()
    mid = (model_id or s.elevenlabs_model_id).strip() or "eleven_multilingual_v2"
    url = _TTS_URL.format(voice_id=vid)
    settings_payload = merge_voice_settings(voice_settings)

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                url,
                headers={
                    "xi-api-key": key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": cleaned,
                    "model_id": mid,
                    "voice_settings": settings_payload,
                },
            )
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"ElevenLabs request failed: {exc}") from exc

    if resp.status_code != 200:
        detail = resp.text[:300]
        try:
            body = resp.json()
            msg = (
                body.get("detail", {}).get("message")
                if isinstance(body.get("detail"), dict)
                else body.get("detail")
            ) or detail
        except Exception:
            msg = detail
        log.warning("elevenlabs_tts_failed", status=resp.status_code, detail=str(msg)[:200])
        raise ElevenLabsError(str(msg), status_code=resp.status_code)

    audio = resp.content
    if not audio or len(audio) < 100:
        raise ElevenLabsError("ElevenLabs returned empty audio")
    return audio
