"""Tests for practical-guide promised tip count reconciliation."""
from bookforge.pipeline.content_guardrails import (
    adjust_list_quotas,
    parse_promised_count,
    planned_use_count,
    reconcile_promise_book,
    rewrite_promised_number,
    shrink_sections_to_count,
)
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
    out = adjust_list_quotas(sections, 4)
    assert planned_use_count(out) == planned_use_count(sections) + 4


def test_reconcile_promise_matches_promised_count_within_page_cap():
    sections = [
        SectionPlan(index=1, title="Kitchen", full_card_quota=7, list_quota=10),
    ]
    bible = _minimal_bible(
        title="20 Kitchen Cleaning Tips",
        subtitle="Easy ways to save money",
        sections=sections,
        target_pages=100,
    )
    out = reconcile_promise_book(bible)
    assert out.promised_uses == 20
    assert planned_use_count(out.sections) == 20


def test_reconcile_promise_book_expands_when_promised_high():
    sections = [
        SectionPlan(index=1, title="Kitchen", full_card_quota=3, list_quota=5),
    ]
    bible = _minimal_bible(
        title="900 Amazing Kitchen Hacks",
        subtitle="",
        sections=sections,
        target_pages=12,
    )
    out = reconcile_promise_book(bible)
    assert out.promised_uses == 900
    assert planned_use_count(out.sections) == 900


def test_rewrite_promised_number():
    title, sub = rewrite_promised_number(
        "Top 50 Ways to Clean", "Fifty clever tips", 46,
    )
    assert "46" in title or "46" in sub
    assert "50" not in title


def test_parse_promised_count_quick_pan_suppers():
    assert parse_promised_count(
        "Spacing Test Cookbook: 4 Quick Pan Suppers", "",
    ) == 4
    assert parse_promised_count("4 Quick Pan Suppers", "") == 4


def test_rewrite_meals_count_in_subtitle():
    title, sub = rewrite_promised_number(
        "The Poor Man Recipe Bible",
        "6 Forgotten Cheap Meals Our Grandparents Made When Money Was Tight",
        4,
    )
    assert "4" in sub
    assert "6" not in sub
    assert parse_promised_count(title, sub) == 4


def test_finalize_marketing_counts_from_chapters():
    from bookforge.pipeline.content_guardrails import finalize_marketing_counts
    from bookforge.schemas import (
        BlockType, Chapter, ContentBlock, EditorialProfile, UseEntry,
    )

    bible = _minimal_bible(
        title="The Poor Man Recipe Bible",
        subtitle="6 Forgotten Cheap Meals Our Grandparents Made",
        promised_uses=6,
        profile=EditorialProfile(
            cover_kicker="100 Forgotten Cheap Meals from When Money Was Tight",
        ),
        sections=[],
    )
    ch = Chapter(
        index=1, title="One-Pots",
        blocks=[
            ContentBlock(
                type=BlockType.use_card,
                entries=[UseEntry(number=i, title=f"Meal {i}")],
            )
            for i in range(1, 5)
        ],
    )
    finalize_marketing_counts(bible, [ch])
    assert bible.promised_uses == 6
    assert "6" in bible.subtitle
    assert "4" not in bible.subtitle.split("6")[0]


def test_shrink_sections_to_count():
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=7, list_quota=24)
        for i in range(1, 7)
    ]
    assert planned_use_count(sections) == 6 * 31
    out = shrink_sections_to_count(sections, 21)
    assert planned_use_count(out) <= 21
    assert all(s.list_quota == 0 for s in out)
