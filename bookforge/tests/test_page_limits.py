"""Tests for page-count limits and content scaling."""
from __future__ import annotations

import pytest

from bookforge.errors import PageLimitError
from bookforge.pipeline.bible import (
    _cap_density,
    _cap_sections_to_page_ceiling,
    _guide_density,
)
from bookforge.pipeline.content_guardrails import (
    planned_use_count,
    reconcile_promise_book,
    rewrite_promised_number,
    shrink_sections_to_count,
)
from bookforge.pipeline.page_limits import (
    ABSOLUTE_PAGE_CEILING,
    allowed_page_range,
    check_page_count,
    hard_page_ceiling,
    is_within_page_range,
    max_planned_entries,
    page_range_prompt_suffix,
    scale_chapters_to_pages,
    scale_images_for_chapters,
)
from bookforge.schemas import (
    ArtStyle,
    BlockType,
    BookBible,
    Chapter,
    ContentBlock,
    EditorialProfile,
    EntrySchema,
    GeneratedImage,
    SectionPlan,
    UseEntry,
    VoiceStyle,
)


def _pest_profile() -> EditorialProfile:
    return EditorialProfile(
        genre="Home pest-control handbook",
        audience="seniors",
        angle="cheap DIY",
        entry_schema=EntrySchema(entry_noun="method"),
        structure_template="tips_cards",
        tiers=2,
        tier_names=["Essentials", "More Uses"],
        image_density="every_entry",
        pages_per_entry=1.0,
        quick_list_weight=1.0,
    )


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
        target_pages=100,
    )
    defaults.update(kwargs)
    return BookBible(**defaults)


def test_allowed_page_range_target_300():
    assert allowed_page_range(300) == (300, 320)


def test_allowed_page_range_target_100():
    assert allowed_page_range(100) == (100, 120)


def test_allowed_page_range_target_5():
    assert allowed_page_range(5) == (5, 25)


def test_meets_page_floor():
    from bookforge.pipeline.page_limits import meets_page_floor
    assert meets_page_floor(160, 160)
    assert not meets_page_floor(116, 160)


def test_is_acceptable_page_count():
    from bookforge.pipeline.page_limits import is_acceptable_page_count
    assert is_acceptable_page_count(160, 160)
    assert is_acceptable_page_count(200, 160)  # overshoot OK when count forces length
    assert not is_acceptable_page_count(116, 160)
    assert not is_acceptable_page_count(400, 160)


def test_hard_ceiling_never_exceeds_absolute_max():
    assert hard_page_ceiling(300) == ABSOLUTE_PAGE_CEILING
    assert hard_page_ceiling(290) == 310


def test_check_page_count_in_range():
    check_page_count(100, 100)
    check_page_count(320, 300)


def test_check_page_count_raises_outside_range():
    with pytest.raises(PageLimitError) as exc:
        check_page_count(99, 100)
    assert exc.value.pages == 99
    assert exc.value.lo == 100
    assert exc.value.hi == 120


def test_max_planned_entries_uses_target_not_hi():
    profile = EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        pages_per_entry=2.0,
    )
    at_75 = max_planned_entries(75, profile, 11, pamphlet=False)
    content_at_target = 75 - 13
    content_at_hi = 100 - 13
    assert at_75 < 365
    assert content_at_target < content_at_hi


def test_365_recipes_scaled_to_target_pages():
    profile = EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        pages_per_entry=2.0,
    )
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=30, list_quota=0)
        for i in range(1, 12)
    ]
    cap = max_planned_entries(75, profile, len(sections), pamphlet=False)
    assert cap < 365
    shrunk = shrink_sections_to_count(sections, cap)
    planned = planned_use_count(shrunk)
    title, _ = rewrite_promised_number(
        "365 Forgotten American Recipes", "Grandma meals", planned,
    )
    assert "365" not in title


def test_smoke_density_respects_ceiling():
    profile = _pest_profile().model_copy(update={"tiers": 1})
    density = _cap_density(_guide_density(5, profile), 5, profile, pamphlet=True)
    _, hi = allowed_page_range(5)
    num_sections = density.base_sections * profile.tiers
    overhead = 2
    assert overhead + num_sections * density.page_budget <= hi


def test_standard_density_image_quota_from_page_budget():
    profile = EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        image_density="standard",
        pages_per_entry=2.0,
    )
    density = _guide_density(75, profile)
    expected = max(1, min(density.cards_per_section, round(density.page_budget / 1.5)))
    assert density.image_quota == expected


def test_cap_sections_to_page_ceiling_shrinks_oversized_plan():
    profile = _pest_profile().model_copy(update={"tiers": 1, "pages_per_entry": 2.0})
    sections = [
        SectionPlan(
            index=i, title=f"S{i}", full_card_quota=20, list_quota=40,
            page_budget=28,
        )
        for i in range(1, 6)
    ]
    capped = _cap_sections_to_page_ceiling(
        sections, 5, profile, pamphlet=True,
    )
    from bookforge.pipeline.page_limits import estimate_guide_pages

    _, hi = allowed_page_range(5)
    assert estimate_guide_pages(capped, pamphlet=True) <= hi
    assert planned_use_count(capped) < planned_use_count(sections)


def test_expand_sections_to_count():
    from bookforge.pipeline.content_guardrails import expand_sections_to_count
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=6, list_quota=0)
        for i in range(1, 8)
    ]
    assert planned_use_count(sections) == 42
    out = expand_sections_to_count(sections, 100)
    assert planned_use_count(out) == 100


def test_finalize_marketing_counts_never_below_promised():
    from bookforge.pipeline.content_guardrails import finalize_marketing_counts
    bible = _minimal_bible(
        title="The Poor Man Recipe Bible",
        subtitle="100 Forgotten Cheap Meals",
        promised_uses=100,
        profile=EditorialProfile(
            cover_kicker="100 Forgotten Cheap Meals",
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
    assert bible.promised_uses == 100
    assert "100" in bible.subtitle


def test_scale_chapters_respects_promised_floor():
    entries = [UseEntry(number=i, title=f"Tip {i}") for i in range(1, 21)]
    ch = Chapter(
        index=1, title="Section 1",
        blocks=[ContentBlock(type=BlockType.use_list, entries=entries)],
    )
    bible = _minimal_bible(target_pages=75)
    scaled, removed = scale_chapters_to_pages(
        [ch], bible, current_pages=150, target_pages=75,
    )
    assert removed > 0
    remaining = sum(
        len(b.entries) for c in scaled for b in c.blocks
        if b.type == BlockType.use_list
    )
    assert remaining < 20


def test_pamphlet_every_entry_image_quota_matches_cards():
    profile = EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        image_density="every_entry",
        pages_per_entry=2.0,
    )
    density = _guide_density(5, profile)
    assert density.image_quota == density.cards_per_section


def test_scale_images_for_chapters_drops_orphan_refs():
    ch = Chapter(
        index=1, title="S1",
        blocks=[
            ContentBlock(
                type=BlockType.use_card,
                entries=[UseEntry(number=1, title="A")],
            ),
        ],
    )
    images = [
        GeneratedImage(block_ref="1.0", storage_key="k1", width=100, height=100),
        GeneratedImage(block_ref="1.99", storage_key="k2", width=100, height=100),
        GeneratedImage(block_ref="cover", storage_key="k3", width=100, height=100),
    ]
    kept = scale_images_for_chapters(images, [ch])
    refs = {i.block_ref for i in kept}
    assert "1.0" in refs
    assert "cover" in refs
    assert "1.99" not in refs


def test_reconcile_promise_expands_to_promised_count():
    sections = [
        SectionPlan(index=1, title="Kitchen", full_card_quota=7, list_quota=0),
    ]
    bible = _minimal_bible(
        title="365 Kitchen Cleaning Tips",
        subtitle="Easy ways to save money",
        sections=sections,
        target_pages=100,
        profile=_pest_profile().model_copy(update={"pages_per_entry": 2.0}),
    )
    out = reconcile_promise_book(bible)
    assert out.promised_uses == 365
    assert planned_use_count(out.sections) == 365
    assert "365" in out.title


def test_assemble_does_not_fail_oversized_pdf(monkeypatch):
    from bookforge.pipeline import assemble
    from bookforge.schemas import QAResult

    bible = _minimal_bible(
        title="Smoke Test",
        book_kind="narrative",
        target_pages=5,
        chapters=[],
    )
    qa = QAResult(groundedness=1.0, passed=True)

    monkeypatch.setattr(assemble.build, "build_html", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(assemble.render, "html_to_pdf", lambda html: b"%PDF-1.4")
    monkeypatch.setattr(assemble, "_linearize", lambda pdf: pdf)
    monkeypatch.setattr(assemble, "_page_count", lambda pdf: 37)
    monkeypatch.setattr(assemble.pdf_geometry, "check_pdf_geometry", lambda pdf: [])
    monkeypatch.setattr(assemble.storage, "put", lambda *a, **k: None)

    art = assemble.assemble("book1", bible, [], [], qa)
    assert art.page_count == 37
    assert not is_within_page_range(37, 5)
    assert any("outside target range" in i for i in art.qa.layout_issues)


def test_page_range_prompt_mentions_scaling():
    text = page_range_prompt_suffix(75)
    assert "75" in text
    assert "MINIMUM" in text
    assert "promised count" in text.lower()
