"""Tests for practical-guide promised tip count reconciliation."""
from bookforge.pipeline.bible import _adjust_list_quotas, _reconcile_promise, _rewrite_promised_number
from bookforge.pipeline.content_guardrails import planned_use_count
from bookforge.schemas import ArtStyle, BookBible, SectionPlan, VoiceStyle


def _minimal_bible(**kwargs) -> BookBible:
    defaults = dict(
        title="Test Book",
        subtitle="",
        audience="seniors",
        tone="warm",
        art_style=ArtStyle(
            medium="photo", palette="warm", lighting="soft", era="today",
            composition_rules="", negative_prompt="no text", style_suffix="",
        ),
        voice=VoiceStyle(
            narrator_persona="guide", tone_register="plain", pov="you",
            pacing="slow", vocabulary="simple",
        ),
        book_kind="practical_guide",
        target_pages=12,
    )
    defaults.update(kwargs)
    return BookBible(**defaults)


def test_adjust_list_quotas_adds_exactly():
    sections = [
        SectionPlan(index=1, title="A", full_card_quota=5, list_quota=10),
        SectionPlan(index=2, title="B", full_card_quota=5, list_quota=10),
    ]
    out = _adjust_list_quotas(sections, 4)
    assert planned_use_count(out) == planned_use_count(sections) + 4


def test_reconcile_promise_matches_promised_count():
    sections = [
        SectionPlan(index=1, title="Kitchen", full_card_quota=7, list_quota=20),
    ]
    bible = _minimal_bible(
        title="50 Kitchen Cleaning Tips",
        subtitle="Easy ways to save money",
        sections=sections,
    )
    out = _reconcile_promise(bible)
    assert out.promised_uses == 50
    assert planned_use_count(out.sections) == 50


def test_reconcile_promise_rewrites_absurd_count():
    sections = [
        SectionPlan(index=1, title="Kitchen", full_card_quota=3, list_quota=5),
    ]
    bible = _minimal_bible(
        title="900 Amazing Kitchen Hacks",
        subtitle="",
        sections=sections,
        target_pages=12,
    )
    out = _reconcile_promise(bible)
    planned = planned_use_count(out.sections)
    assert out.promised_uses == planned
    assert "900" not in out.title


def test_rewrite_promised_number():
    title, sub = _rewrite_promised_number(
        "Top 50 Ways to Clean", "Fifty clever tips", 46,
    )
    assert "46" in title or "46" in sub
    assert "50" not in title
