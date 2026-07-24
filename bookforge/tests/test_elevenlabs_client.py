"""ElevenLabs client unit tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bookforge.clients.elevenlabs import ElevenLabsError, text_to_speech


def test_text_to_speech_success(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from bookforge.config import get_settings
    get_settings.cache_clear()

    fake = MagicMock()
    fake.status_code = 200
    fake.content = b"ID3" + b"\x00" * 200

    with patch("bookforge.clients.elevenlabs.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = fake
        data = text_to_speech("Hello world", voice_id="21m00Tcm4TlvDq8ikWAM")

    assert data.startswith(b"ID3")
    call_kw = client_cls.return_value.__enter__.return_value.post.call_args
    url = call_kw.args[0]
    assert url.endswith("/text-to-speech/21m00Tcm4TlvDq8ikWAM")
    assert "speech.mp3" not in url
    assert call_kw.kwargs["headers"]["xi-api-key"] == "test-key"
    payload = call_kw.kwargs["json"]
    assert payload["model_id"] == "eleven_multilingual_v2"
    assert payload["voice_settings"] == {
        "stability": 0.40,
        "similarity_boost": 0.78,
        "style": 0.20,
        "use_speaker_boost": True,
        "speed": 0.95,
    }

    get_settings.cache_clear()


def test_text_to_speech_voice_settings_override(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from bookforge.config import get_settings
    get_settings.cache_clear()

    fake = MagicMock()
    fake.status_code = 200
    fake.content = b"ID3" + b"\x00" * 200

    with patch("bookforge.clients.elevenlabs.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = fake
        text_to_speech(
            "Hello",
            model_id="eleven_multilingual_v2",
            voice_settings={"stability": 0.2, "speed": 1.0},
        )

    payload = client_cls.return_value.__enter__.return_value.post.call_args.kwargs["json"]
    assert payload["voice_settings"]["stability"] == 0.2
    assert payload["voice_settings"]["speed"] == 1.0
    assert payload["voice_settings"]["similarity_boost"] == 0.78  # default kept

    get_settings.cache_clear()


def test_text_to_speech_missing_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    from bookforge.config import get_settings
    get_settings.cache_clear()
    with pytest.raises(ElevenLabsError, match="ELEVENLABS_API_KEY"):
        text_to_speech("hi")
    get_settings.cache_clear()


def test_text_to_speech_api_error(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from bookforge.config import get_settings
    get_settings.cache_clear()

    fake = MagicMock()
    fake.status_code = 400
    fake.text = '{"detail":{"message":"bad voice"}}'
    fake.json.return_value = {"detail": {"message": "bad voice"}}

    with patch("bookforge.clients.elevenlabs.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = fake
        with pytest.raises(ElevenLabsError, match="bad voice"):
            text_to_speech("Hello")

    get_settings.cache_clear()
