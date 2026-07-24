"""Audio script API endpoint tests — no live LLM / ElevenLabs calls."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("BOOKFORGE_LOCAL_STORE", "/tmp/bookforge-test-artifacts")

from bookforge.api.main import app  # noqa: E402
from bookforge.pipeline.readiness import ReadinessResult  # noqa: E402
from bookforge.schemas import AudioScriptItem, AudioScriptResult  # noqa: E402

client = TestClient(app)

_FAKE_RESULT = AudioScriptResult(
    title="100 Concepts That Make You See the World Differently Than 99% of People",
    category="psychology",
    items=[
        AudioScriptItem(
            number=1,
            title="Map-Territory Distinction",
            body="First paragraph.\n\nSecond paragraph.",
            word_count=4,
        ),
        AudioScriptItem(
            number=2,
            title="Identity Protection",
            body="Another paragraph.",
            word_count=2,
        ),
    ],
    transcript="Map-Territory Distinction\n\nFirst paragraph.\n\nSecond paragraph.\n\nIdentity Protection\n\nAnother paragraph.",
    total_words=6,
)

_FAKE_MP3 = b"ID3" + b"\x00" * 200


@pytest.fixture(autouse=True)
def _readiness_ok():
    passing = ReadinessResult(
        can_generate=True,
        title="Ready to generate",
        message="All services are ready.",
        reason_code="ok",
        checks={},
    )
    with patch("bookforge.api.main.check_readiness", return_value=passing):
        yield


def test_post_audio_script_returns_link():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT):
        with patch("bookforge.api.main.generate_narration_audio", return_value=_FAKE_MP3):
            res = client.post(
                "/audio-scripts",
                json={
                    "title": "100 Concepts That Make You See the World Differently Than 99% of People",
                    "item_limit": 2,
                },
            )
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued"
    assert "script_id" in body
    assert "/audio-scripts/" in body["link"]


def test_get_done_includes_transcript_and_audio_link():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT):
        with patch("bookforge.api.main.generate_narration_audio", return_value=_FAKE_MP3):
            create = client.post(
                "/audio-scripts",
                json={"title": "100 Concepts Test Title", "item_limit": 2},
            )
            script_id = create.json()["script_id"]

    res = client.get(f"/audio-scripts/{script_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "done"
    assert "transcript" in body
    assert "Map-Territory Distinction" in body["transcript"]
    assert body["audio_link"].endswith(f"/audio-scripts/{script_id}/audio")
    stage_ids = [s["id"] for s in body["stages"]]
    assert "generating_audio" in stage_ids

    audio = client.get(f"/audio-scripts/{script_id}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/mpeg")
    assert len(audio.content) >= 100


def test_skip_audio_no_mp3_route():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        with patch("bookforge.api.main.generate_narration_audio") as tts:
            create = client.post(
                "/audio-scripts",
                json={"title": "Skip Audio Test", "item_limit": 1, "skip_audio": True},
            )
            script_id = create.json()["script_id"]
            gen.assert_called_once()
            tts.assert_not_called()

    res = client.get(f"/audio-scripts/{script_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "done"
    assert "transcript" in res.json()
    audio = client.get(f"/audio-scripts/{script_id}/audio")
    assert audio.status_code == 404


def test_get_not_found():
    res = client.get("/audio-scripts/does-not-exist-xyz")
    assert res.status_code == 404


def test_list_audio_scripts():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT):
        with patch("bookforge.api.main.generate_narration_audio", return_value=_FAKE_MP3):
            client.post("/audio-scripts", json={"title": "List Test Script", "item_limit": 1})
    res = client.get("/audio-scripts")
    assert res.status_code == 200
    scripts = res.json()["scripts"]
    match = next(s for s in scripts if s.get("title") == "List Test Script")
    assert match["has_audio"] is True


def test_post_accepts_word_count_and_persists_request():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        with patch("bookforge.api.main.generate_narration_audio", return_value=_FAKE_MP3):
            res = client.post(
                "/audio-scripts",
                json={
                    "title": "100 Concepts Word Count Test",
                    "item_limit": 2,
                    "word_count": 200,
                    "skip_audio": True,
                },
            )
    assert res.status_code == 202
    script_id = res.json()["script_id"]
    gen.assert_called_once()
    assert gen.call_args.kwargs.get("word_count") == 200

    from bookforge.api.main import _as_request_path
    import json
    req = json.loads(_as_request_path(script_id).read_text())
    assert req["word_count"] == 200
    assert req["item_limit"] == 2


def test_post_rejects_word_count_out_of_range():
    res = client.post(
        "/audio-scripts",
        json={"title": "Bad Word Count", "word_count": 50},
    )
    assert res.status_code == 422


def test_post_requires_title():
    res = client.post("/audio-scripts", json={"title": "   "})
    assert res.status_code == 422


def test_old_video_routes_gone():
    res = client.post("/video-scripts", json={"title": "x", "item_limit": 1})
    assert res.status_code == 404
