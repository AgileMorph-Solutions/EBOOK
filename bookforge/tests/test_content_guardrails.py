"""Tests for practical-guide content coherence guardrails."""
from __future__ import annotations

from bookforge.pipeline.content_guardrails import (
    extract_title_item_label,
    parse_leading_marketing_count,
    parse_promised_count,
    planned_use_count,
    scan_chapters_for_count_drift,
    shrink_sections_to_count,
)
from bookforge.schemas import (
    ArtStyle, BlockType, BookBible, Chapter, ContentBlock, SectionPlan, VoiceStyle,
)


def _minimal_bible(**kwargs) -> BookBible:
    defaults = dict(
        title="The Hydrogen Peroxide Savings Bible",
        subtitle="250 Cheap Ways Seniors Can Clean, Fix, Freshen, and Protect Their Home",
        audience="Seniors",
        tone="warm",
        art_style=ArtStyle(
            medium="photo", palette="teal", lighting="daylight", era="now",
            composition_rules="", negative_prompt="no text",
            style_suffix="clean photo",
        ),
        voice=VoiceStyle(
            narrator_persona="neighbor", tone_register="plain", pov="you",
            pacing="short", vocabulary="simple",
        ),
        promised_uses=250,
        sections=[
            SectionPlan(index=1, title="Kitchen", full_card_quota=8, list_quota=28),
        ],
    )
    defaults.update(kwargs)
    return BookBible(**defaults)


def test_parse_promised_count_from_subtitle():
    assert parse_promised_count(
        "HP Bible",
        "250 Cheap Ways Seniors Can Clean",
    ) == 250


def test_parse_promised_count_level_range():
    """'Level 1 to 100 … Secrets' must resolve to 100, not the start digit 1."""
    title = "Level 1 to 100 Social Intelligence Secrets That Get Darker as You Go"
    assert parse_promised_count(title, "") == 100
    assert parse_promised_count("1-50 Dark Psychology Tricks", "") == 50
    assert parse_promised_count("Levels 1–25 Relationship Habits", "") == 25
    assert parse_promised_count("100 Dark Psychology Tricks", "") == 100
    assert parse_promised_count("4 Quick Pan Suppers", "") == 4


def test_parse_promised_count_with_quoted_gap_words():
    title = '100 "Normal" Adult Habits That Reveal You Had to Grow Up Too Fast'
    assert parse_promised_count(title, "") == 100


def test_parse_promised_count_none():
    assert parse_promised_count("Pest Guide", "How to Fight Roaches Without Paying Hundreds") is None


def test_parse_promised_count_fixes():
    assert parse_promised_count(
        "Senior Pest Control",
        "21 Cheap, Safe DIY Fixes Before You Pay an Exterminator",
    ) == 21


def test_parse_promised_count_psychology_list_nouns():
    assert parse_promised_count("100 Signs Someone Secretly Dislikes You", "") == 100
    assert parse_promised_count(
        "100 Behaviors That Reveal Exactly How Someone Was Raised", ""
    ) == 100
    assert parse_promised_count(
        "100 Concepts That Make You See the World Differently Than 99% of People",
        "",
    ) == 100


def test_parse_leading_marketing_count():
    assert parse_leading_marketing_count("100 Signs Someone Secretly Dislikes You") == 100
    assert parse_leading_marketing_count("4 Quick Pan Suppers") == 4
    assert parse_leading_marketing_count("3 Hours of Calm") is None
    assert parse_leading_marketing_count("The 1950s Grandma Cookbook") is None
    assert parse_leading_marketing_count("2024 Garden Guide") is None


def test_extract_title_item_label():
    assert extract_title_item_label("100 Signs Someone Secretly Dislikes You") == "signs"
    assert extract_title_item_label("100 Psychological Reasons People Cut Everyone Off") == "reasons"
    assert extract_title_item_label("How To Think Clearly") == "items"


def test_parse_promised_count_negative_titles():
    assert parse_promised_count("Pest Guide", "") is None
    assert parse_promised_count("The 1950s Grandma Cookbook", "") is None
    assert parse_promised_count(
        "Pest Guide",
        "How to Fight Roaches Without Paying Hundreds",
    ) is None


def test_shrink_pamphlet_sections_to_promise():
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=7, list_quota=24)
        for i in range(1, 7)
    ]
    out = shrink_sections_to_count(sections, 21)
    assert planned_use_count(out) <= 21


def test_planned_use_count():
    sections = [
        SectionPlan(index=1, title="A", full_card_quota=8, list_quota=28),
        SectionPlan(index=2, title="B", full_card_quota=3, list_quota=12),
    ]
    assert planned_use_count(sections) == 8 + 28 + 3 + 12


def test_scan_flags_inflated_count():
    bible = _minimal_bible()
    ch = Chapter(
        index=1,
        title="Kitchen",
        blocks=[
            ContentBlock(
                type=BlockType.section_intro,
                text="Here are over 800 ways to save with peroxide around your home.",
            ),
        ],
    )
    issues = scan_chapters_for_count_drift([ch], bible)
    assert any("800" in i for i in issues)


def test_scan_ignores_matching_count():
    bible = _minimal_bible()
    ch = Chapter(
        index=1,
        title="Kitchen",
        blocks=[
            ContentBlock(
                type=BlockType.section_intro,
                text="This section adds to our 250 practical home tips.",
            ),
        ],
    )
    issues = scan_chapters_for_count_drift([ch], bible)
    assert not issues
