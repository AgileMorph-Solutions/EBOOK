"""Readiness gate tests."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import anthropic
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("BOOKFORGE_LOCAL_STORE", "/tmp/bookforge-test-artifacts")

from bookforge.api.main import app  # noqa: E402
from bookforge.clients import images as img_client
from bookforge.pipeline.readiness import (
    ReadinessRequest,
    ReadinessResult,
    check_readiness,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_readiness_cache():
    from bookforge.pipeline import readiness as readiness_mod
    from bookforge.config import get_settings

    readiness_mod._service_cache["deadline"] = 0.0
    readiness_mod._service_cache["checks"] = None
    # Tests patch llm.ping — ensure the auth short-circuit does not fire first.
    with patch.object(get_settings(), "anthropic_api_key", "test-key"):
        yield
    readiness_mod._service_cache["deadline"] = 0.0
    readiness_mod._service_cache["checks"] = None


def _passing_checks() -> ReadinessResult:
    return ReadinessResult(
        can_generate=True,
        title="Ready to generate",
        message="All services are ready.",
        reason_code="ok",
        checks={},
    )


def test_writing_unfunded_blocks():
    exc = anthropic.BadRequestError(
        message="credit balance is too low",
        response=MagicMock(status_code=400),
        body=None,
    )
    with patch("bookforge.pipeline.readiness.llm.ping", side_effect=exc):
        with patch("bookforge.pipeline.readiness.img_client.ping"):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(active_jobs=0)
    assert result.can_generate is False
    assert result.reason_code == "quota_llm"
    assert "credits" in result.message.lower()


def test_images_quota_allows_degraded_generation():
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch(
            "bookforge.pipeline.readiness.img_client.ping",
            side_effect=img_client.ImageQuotaError("429 quota"),
        ):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(active_jobs=0)
    assert result.can_generate is True
    assert result.checks["images"].ok is True
    assert "quota" in result.checks["images"].detail.lower()


def test_layout_defect_blocks():
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch("bookforge.pipeline.readiness.img_client.ping"):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=["page scale wrong"],
            ):
                result = check_readiness(active_jobs=0)
    assert result.can_generate is False
    assert result.reason_code == "layout"


def test_capacity_warn_only():
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch("bookforge.pipeline.readiness.img_client.ping"):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(active_jobs=2)
    assert result.can_generate is True
    assert result.checks["capacity"].ok is False
    assert result.checks["capacity"].reason_code == "capacity"


def test_request_allows_marketing_count_smoke_run():
    """10-page smoke tests must not be blocked by title numbers like 365 or 100."""
    req = ReadinessRequest(
        topic="100 Forgotten American Recipes",
        description="365 recipes for seniors",
        text="x" * 500,
        target_pages=10,
        section_limit=1,
    )
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch("bookforge.pipeline.readiness.img_client.ping"):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(include_request=req, active_jobs=0)
    assert result.can_generate is True
    assert result.checks["request"].ok is True


def test_request_blocks_short_source():
    req = ReadinessRequest(
        topic="Test Book",
        text="too short",
        target_pages=10,
    )
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch("bookforge.pipeline.readiness.img_client.ping"):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(include_request=req, active_jobs=0)
    assert result.can_generate is False
    assert result.reason_code == "request"
    assert "too short" in result.message.lower()


def test_post_books_503_when_not_ready():
    fail = ReadinessResult(
        can_generate=False,
        title="Can't start",
        message="Writing credits are low.",
        reason_code="quota_llm",
        checks={},
    )
    with patch("bookforge.api.main._run_pipeline"):
        with patch("bookforge.api.main.check_readiness", return_value=fail):
            res = client.post(
                "/books",
                json={"topic": "Test Book", "text": "Enough source " * 30},
            )
    assert res.status_code == 503
    body = res.json()
    assert body["detail"]["message"] == "Writing credits are low."


def test_post_books_202_when_ready():
    with patch("bookforge.api.main._run_pipeline"):
        with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
            res = client.post(
                "/books",
                json={"topic": "Test Book", "text": "Enough source " * 30},
            )
    assert res.status_code == 202
    assert res.json()["status"] == "queued"


def test_get_readiness_endpoint():
    with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
        res = client.get("/readiness")
    assert res.status_code == 200
    assert res.json()["can_generate"] is True
