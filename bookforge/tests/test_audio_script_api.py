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


def test_post_accepts_free_text_category():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            "/audio-scripts",
            json={
                "title": "True Crime Free Category",
                "category": "true crime",
                "item_limit": 1,
                "skip_audio": True,
            },
        )
    assert res.status_code == 202
    script_id = res.json()["script_id"]
    gen.assert_called_once()
    assert gen.call_args.kwargs.get("category") == "true crime"

    from bookforge.api.main import _as_request_path, _read_as_state
    import json
    req = json.loads(_as_request_path(script_id).read_text())
    assert req["category"] == "true crime"
    state = _read_as_state(script_id)
    assert state is not None
    assert state.get("category") == "true crime"


def test_post_rejects_blank_category():
    res = client.post(
        "/audio-scripts",
        json={"title": "Need Category", "category": "   ", "skip_audio": True},
    )
    assert res.status_code == 422


def test_post_generic_title_defaults_to_1000_words():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            "/audio-scripts",
            json={
                "title": "How to think clearly",
                "category": "psychology",
                "skip_audio": True,
            },
        )
    assert res.status_code == 202
    script_id = res.json()["script_id"]
    gen.assert_called_once()
    assert gen.call_args.kwargs.get("item_count") == 4
    assert gen.call_args.kwargs.get("word_count") == 250

    from bookforge.api.main import _as_request_path, _read_as_state
    import json
    req = json.loads(_as_request_path(script_id).read_text())
    assert req["overall_word_limit"] is None

    status = client.get(f"/audio-scripts/{script_id}")
    body = status.json()
    assert body.get("overall_word_limit") == 1000
    assert body.get("item_count") == 4
    assert body.get("effective_word_count") == 250


def test_post_overall_word_limit_derives_per_item():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            "/audio-scripts",
            json={
                "title": "Overall Budget Script",
                "item_limit": 10,
                "overall_word_limit": 3000,
                "skip_audio": True,
            },
        )
    assert res.status_code == 202
    script_id = res.json()["script_id"]
    gen.assert_called_once()
    assert gen.call_args.kwargs.get("word_count") == 300  # 3000 // 10

    from bookforge.api.main import _as_request_path
    import json
    req = json.loads(_as_request_path(script_id).read_text())
    assert req["overall_word_limit"] == 3000
    assert req.get("word_count") is None


def test_post_overall_word_limit_clamps_explicit_word_count():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            "/audio-scripts",
            json={
                "title": "Clamp Word Count Script",
                "item_limit": 20,
                "word_count": 270,
                "overall_word_limit": 2000,
                "skip_audio": True,
            },
        )
    assert res.status_code == 202
    gen.assert_called_once()
    # 270 * 20 = 5400 > 2000 → 2000 // 20 = 100
    assert gen.call_args.kwargs.get("word_count") == 100


def test_post_duration_title_derives_length():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            "/audio-scripts",
            json={
                "title": "3 Hours of Emotional Control Secrets to Fall Asleep To",
                "skip_audio": True,
            },
        )
    assert res.status_code == 202
    gen.assert_called_once()
    # ~180 min * 130 wpm / 267 ≈ 88 items; per-item ≈ 265
    assert gen.call_args.kwargs.get("item_count") == 88
    assert gen.call_args.kwargs.get("word_count") == 265

    status = client.get(f"/audio-scripts/{res.json()['script_id']}")
    body = status.json()
    assert body.get("target_duration_minutes") == 180
    assert body.get("overall_word_limit") == 23400


def test_post_estimate_duration_conflict():
    res = client.post(
        "/audio-scripts/estimate",
        json={"title": "2 minutes of reading", "overall_word_limit": 200},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["conflict"] is True
    assert body["words_needed"] == 260
    assert body["overall_word_limit"] == 200


def test_post_estimate_no_conflict_when_cap_enough():
    res = client.post(
        "/audio-scripts/estimate",
        json={"title": "2 minutes of reading", "overall_word_limit": 300},
    )
    assert res.status_code == 200
    assert res.json()["conflict"] is False


def test_post_rejects_duration_conflict_without_accept():
    res = client.post(
        "/audio-scripts",
        json={
            "title": "2 minutes of reading",
            "overall_word_limit": 200,
            "skip_audio": True,
        },
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["conflict"] is True


def test_post_accepts_duration_conflict_with_flag():
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            "/audio-scripts",
            json={
                "title": "2 minutes of reading",
                "overall_word_limit": 200,
                "accept_short_duration": True,
                "skip_audio": True,
            },
        )
    assert res.status_code == 202
    gen.assert_called_once()


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


def _create_done_script(*, title: str = "Regen Base Script", skip_audio: bool = True) -> str:
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT):
        with patch("bookforge.api.main.generate_narration_audio", return_value=_FAKE_MP3):
            create = client.post(
                "/audio-scripts",
                json={"title": title, "item_limit": 2, "skip_audio": skip_audio},
            )
    assert create.status_code == 202
    script_id = create.json()["script_id"]
    status = client.get(f"/audio-scripts/{script_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "done"
    return script_id


def test_regenerate_with_feedback_requeues_and_passes_feedback():
    script_id = _create_done_script(title="Regen Happy Path")
    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        with patch("bookforge.api.main.generate_narration_audio", return_value=_FAKE_MP3):
            res = client.post(
                f"/audio-scripts/{script_id}/regenerate",
                json={"feedback": "Make openings more scene-based."},
            )
    assert res.status_code == 202
    body = res.json()
    assert body["script_id"] == script_id
    assert body["status"] == "queued"
    assert body["mode"] == "full_rerun"
    gen.assert_called_once()
    assert gen.call_args.kwargs.get("feedback") == "Make openings more scene-based."

    done = client.get(f"/audio-scripts/{script_id}")
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json().get("feedback") == "Make openings more scene-based."


def test_regenerate_failed_script():
    script_id = _create_done_script(title="Regen Failed Path")
    from bookforge.api.main import _set_as_state
    _set_as_state(script_id, status="failed", error="boom", stage="failed")

    with patch("bookforge.api.main.generate_audio_script", return_value=_FAKE_RESULT) as gen:
        res = client.post(
            f"/audio-scripts/{script_id}/regenerate",
            json={"feedback": "Retry with calmer tone."},
        )
    assert res.status_code == 202
    gen.assert_called_once()
    assert gen.call_args.kwargs.get("feedback") == "Retry with calmer tone."
    assert client.get(f"/audio-scripts/{script_id}").json()["status"] == "done"


def test_regenerate_rejects_empty_feedback():
    script_id = _create_done_script(title="Regen Empty Feedback")
    res = client.post(
        f"/audio-scripts/{script_id}/regenerate",
        json={"feedback": "   "},
    )
    assert res.status_code == 422


def test_regenerate_409_while_running():
    script_id = _create_done_script(title="Regen Running Conflict")
    from bookforge.api.main import _set_as_state
    _set_as_state(script_id, status="running", stage="writing_items")
    res = client.post(
        f"/audio-scripts/{script_id}/regenerate",
        json={"feedback": "Should not run."},
    )
    assert res.status_code == 409


def test_regenerate_404_unknown():
    res = client.post(
        "/audio-scripts/does-not-exist-xyz/regenerate",
        json={"feedback": "Nope."},
    )
    assert res.status_code == 404


def test_regenerate_409_missing_request():
    script_id = _create_done_script(title="Regen Missing Request")
    from bookforge.api.main import _as_request_path
    path = _as_request_path(script_id)
    path.unlink(missing_ok=True)
    res = client.post(
        f"/audio-scripts/{script_id}/regenerate",
        json={"feedback": "Cannot without request."},
    )
    assert res.status_code == 409
