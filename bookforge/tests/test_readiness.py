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


def test_post_books_reuses_book_id_from_sync_plan():
    """On a successful synchronous plan, the job must be enqueued under the
    SAME book_id the plan was checkpointed against — not a fresh one — or the
    background job would re-derive (and re-pay for) the bible from scratch."""
    captured: dict = {}

    def fake_plan_book(book_id, *args, **kwargs):
        captured["book_id"] = book_id
        return MagicMock(), MagicMock()

    with patch("bookforge.api.main._run_pipeline"):
        with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
            with patch(
                "bookforge.pipeline.orchestrator.plan_book",
                side_effect=fake_plan_book,
            ):
                res = client.post(
                    "/books",
                    json={"topic": "Reused Id Book", "text": "Enough source " * 30},
                )
    assert res.status_code == 202
    assert res.json()["book_id"] == captured["book_id"]


def test_post_books_503_when_plan_infeasible():
    """A real (not coarse-guessed) plan mismatch must block synchronously,
    before any job is created — the exact bug this gate exists to fix."""
    from bookforge.errors import PlanInfeasibleError

    with patch("bookforge.api.main._run_pipeline") as mock_run:
        with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
            with patch(
                "bookforge.pipeline.orchestrator.plan_book",
                side_effect=PlanInfeasibleError(
                    "plan estimates 59 pages but need at least 100 "
                    "(target 100, max 120) — raise pages or add content"
                ),
            ):
                res = client.post(
                    "/books",
                    json={
                        "topic": "100 Cheap Household Fixes",
                        "text": "Enough source " * 30,
                        "target_pages": 100,
                    },
                )
    assert res.status_code == 503
    body = res.json()["detail"]
    assert body["reason_code"] == "request"
    assert "raise pages" in body["message"].lower()
    assert body["assessment"]["can_create"] is False
    mock_run.assert_not_called()


def test_post_books_503_options_match_ceiling_capped_message():
    """Regression test for the "raise pages" bug: when the real plan is
    ceiling-capped, the 503's assessment.options must offer the SAME
    achievable page count as the message — never the separately-computed
    coarse assessment's (possibly contradictory) suggestion, and never advice
    to raise pages when that's proven not to work."""
    from bookforge.errors import PlanInfeasibleError

    with patch("bookforge.api.main._run_pipeline") as mock_run:
        with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
            with patch(
                "bookforge.pipeline.orchestrator.plan_book",
                side_effect=PlanInfeasibleError(
                    "plan tops out at 99 pages — it's already at its promised "
                    "item ceiling (66 items) and target 177 needs at least 177. "
                    "Raising pages will not help; use ~99 pages instead, or add "
                    "more source material / promise more items to allow a "
                    "bigger plan.",
                    est=99, lo=177, hi=197, target_pages=177,
                    ceiling_capped=True, suggested_pages=99,
                ),
            ):
                res = client.post(
                    "/books",
                    json={
                        "topic": "60 Cheap Household Fixes",
                        "text": "Enough source " * 30,
                        "target_pages": 177,
                    },
                )
    assert res.status_code == 503
    body = res.json()["detail"]
    assert "raise pages" not in body["message"].lower()
    options = body["assessment"]["options"]
    assert len(options) == 1
    assert options[0]["id"] == "lower_pages"
    assert options[0]["target_pages"] == 99
    mock_run.assert_not_called()


def test_post_books_falls_back_to_async_on_transient_plan_error():
    """A provider hiccup during the synchronous plan check must not block the
    user on infrastructure trouble — it should fall through to the normal
    async job, which already has its own retry/park handling."""
    with patch("bookforge.api.main._run_pipeline"):
        with patch("bookforge.api.main.check_readiness", return_value=_passing_checks()):
            with patch(
                "bookforge.pipeline.orchestrator.plan_book",
                side_effect=TimeoutError("provider hiccup"),
            ):
                res = client.post(
                    "/books",
                    json={"topic": "Test Book", "text": "Enough source " * 30},
                )
    assert res.status_code == 202
    assert res.json()["status"] == "queued"


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
