"""Tests for plan scope and pre-flight book assessment."""
from __future__ import annotations

from bookforge.pipeline.feasibility import _default_profile, build_book_assessment
from bookforge.pipeline.page_limits import allowed_page_range
from bookforge.pipeline.plan_scope import build_plan_scope
from bookforge.pipeline.readiness import check_readiness, ReadinessRequest
from bookforge.schemas import SectionPlan


def test_page_band_100_120():
    assert allowed_page_range(100) == (100, 120)


def test_page_band_170_190():
    assert allowed_page_range(170) == (170, 190)


def test_build_plan_scope_from_toc():
    scope = build_plan_scope(
        topic="Camp Manual",
        toc=["Intro", "Ch 1", "Ch 2"],
    )
    assert scope.min_sections == 3
    assert scope.locked_toc is True


def test_build_plan_scope_counts_bullets():
    text = "Chapter 1: Safety\n- rule one\n- rule two\n* rule three"
    scope = build_plan_scope(topic="Guide", text=text)
    assert scope.source_entry_floor == 3


def test_build_plan_scope_prefers_explicit_recipe_count_over_bullets():
    lines = ["Create 75 freezer-friendly recipes:"]
    lines += [f"{i}. Recipe {i}" for i in range(1, 101)]
    text = "\n".join(lines)
    scope = build_plan_scope(topic="Batch Cookbook", text=text)
    assert scope.min_entries == 75
    assert scope.source_entry_floor == 100


def test_reconcile_allows_small_gap_below_floor(monkeypatch):
    from bookforge.pipeline import page_limits as pl

    sections = [
        SectionPlan(index=1, title="S1", full_card_quota=5, list_quota=0, page_budget=8),
    ]
    profile = _default_profile(True)

    monkeypatch.setattr(pl, "estimate_rendered_pages", lambda *a, **k: 122)
    monkeypatch.setattr(
        pl, "_upsize_sections_to_estimate", lambda sections, *a, **k: sections,
    )
    out = pl.reconcile_plan_to_page_band(sections, profile, target_pages=125)
    assert len(out) == 1


def test_assessment_blocks_thin_toc_at_170():
    toc = [f"Chapter {i}" for i in range(1, 17)]
    text = "\n".join(f"- item {i}" for i in range(120))
    assessment = build_book_assessment(
        topic="Big Guide",
        text=text,
        target_pages=170,
        toc=toc,
    )
    assert assessment.page_range_lo == 170
    assert assessment.page_range_hi == 190
    assert assessment.scope.sections == 16
    assert assessment.estimated_pages is not None


def test_assessment_ok_for_cookbook_count():
    assessment = build_book_assessment(
        topic="40 Quick Pan Suppers",
        description="40 easy dinners for one",
        target_pages=148,
    )
    assert assessment.can_create is True
    assert assessment.page_range_hi == 168
    assert assessment.status in ("ok", "compact")


def test_screenshot_case_expandable_at_150():
    """Regression: est below floor must not say Ready while blocked."""
    toc = [f"Chapter {i}" for i in range(1, 8)]
    text = "\n".join(f"- item {i}" for i in range(40))
    assessment = build_book_assessment(
        topic="Forgotten British Meals",
        text=text,
        target_pages=150,
        toc=toc,
    )
    assert assessment.can_create is True
    assert assessment.assessment_mode == "expand"
    assert "expand" in assessment.message.lower() or "light" in assessment.message.lower()
    assert "Ready" not in assessment.message or assessment.can_create


def test_message_matches_can_create():
    toc = [f"Chapter {i}" for i in range(1, 8)]
    text = "\n".join(f"- item {i}" for i in range(40))
    assessment = build_book_assessment(
        topic="Forgotten British Meals",
        text=text,
        target_pages=150,
        toc=toc,
    )
    if assessment.can_create:
        assert assessment.status in ("ok", "compact")
        assert "needs at least" not in assessment.message.lower()
    else:
        assert assessment.status == "infeasible"


def test_readiness_blocked_uses_assessment_message():
    toc = [f"Chapter {i}" for i in range(1, 8)]
    text = "\n".join(f"- item {i}" for i in range(2))
    req = ReadinessRequest(
        topic="365 Forgotten American Recipes",
        text=text * 50,
        target_pages=150,
        toc=toc,
    )
    result = check_readiness(include_request=req, active_jobs=0)
    if not result.can_generate:
        assert result.assessment is not None
        assert result.message == result.assessment.message
        assert "Ready — estimated" not in result.message

