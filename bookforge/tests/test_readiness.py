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


def test_images_unclassified_generation_error_degrades_not_blocks():
    """Regression test: a live incident hard-blocked book creation with
    "Today's image limit is used up" when the real cause was an unrelated bug
    (a stale fallback model id + a response-parsing crash) — NOT quota. An
    unrecognized ImageGenerationError (not clearly billing/auth) must degrade
    like ImageQuotaError does, not hard-block on a guess."""
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch(
            "bookforge.pipeline.readiness.img_client.ping",
            side_effect=img_client.ImageGenerationError(
                "all Imagen models failed; last error: model x returned an "
                "empty image response"
            ),
        ):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(active_jobs=0)
    assert result.can_generate is True
    assert result.checks["images"].ok is True


def test_images_billing_generation_error_still_blocks():
    """A genuinely actionable billing/permission failure must still hard
    block — unlike the unclassified case above, this won't self-resolve."""
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch(
            "bookforge.pipeline.readiness.img_client.ping",
            side_effect=img_client.ImageGenerationError(
                "403 permission_denied: upgrade to a paid plan"
            ),
        ):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                result = check_readiness(active_jobs=0)
    assert result.can_generate is False
    assert result.checks["images"].ok is False
    assert result.checks["images"].reason_code == "auth"


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
            with patch(
                "bookforge.pipeline.orchestrator.plan_book",
                return_value=(MagicMock(), MagicMock()),
            ):
                res = client.post(
                    "/books",
                    json={"topic": "Test Book", "text": "Enough source " * 30},
                )
    assert res.status_code == 202
    assert res.json()["status"] == "queued"


def test_post_books_never_calls_plan_book_synchronously():
    """POST /books must enqueue immediately without running real planning
    (ingest + build_bible) synchronously on the request thread — that used to
    block the endpoint for up to a minute on every submission. Real planning
    still runs as the job's own first stage (see orchestrator/pipeline
    tests); a mismatch surfaces as a failed job, not a blocked POST. Matches
    /books/upload, which never had a synchronous planning gate."""
    with patch("bookforge.api.main._run_pipeline"):
        with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
            with patch(
                "bookforge.pipeline.orchestrator.plan_book",
                side_effect=AssertionError(
                    "plan_book must not be called synchronously by POST /books"
                ),
            ) as mock_plan_book:
                res = client.post(
                    "/books",
                    json={"topic": "Reused Id Book", "text": "Enough source " * 30},
                )
    assert res.status_code == 202
    assert res.json()["status"] == "queued"
    mock_plan_book.assert_not_called()


def test_check_request_replaces_stale_options_with_dry_run_options():
    """Regression test: when the accurate dry-run TOC check blocks a request,
    its options must win over the earlier, coarser assessment's — both on
    `assessment.options` AND `feasibility.options` (the frontend prefers the
    latter when both are present), or the UI can show a message and a fix-it
    button that point in different directions."""
    from bookforge.pipeline.feasibility import FeasibilityOption

    req = ReadinessRequest(
        topic="100 Forgotten Recipes",
        text="x" * 500,
        toc=["Section 1: Starters"] + [f"Recipe {i}" for i in range(1, 51)],
        target_pages=200,
    )
    fake_options = [
        FeasibilityOption(id="lower_pages", label="Use ~99 pages", target_pages=99),
    ]
    with patch("bookforge.pipeline.readiness.llm.ping"):
        with patch("bookforge.pipeline.readiness.img_client.ping"):
            with patch(
                "bookforge.pipeline.readiness.pdf_geometry.preflight_render_check",
                return_value=[],
            ):
                with patch(
                    "bookforge.pipeline.bible.dry_run_toc_feasibility",
                    return_value=(False, "plan tops out at 99 pages", fake_options),
                ):
                    result = check_readiness(include_request=req, active_jobs=0)
    assert result.can_generate is False
    assert result.assessment is not None
    assert result.assessment.options == fake_options
    assert result.feasibility is not None
    assert result.feasibility.options == fake_options


def test_get_readiness_endpoint():
    with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
        res = client.get("/readiness")
    assert res.status_code == 200
    assert res.json()["can_generate"] is True
