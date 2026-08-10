"""Tests for page-count limits and content scaling."""
from __future__ import annotations

import pytest

from bookforge.errors import PageLimitError, PlanInfeasibleError
from bookforge.pipeline.bible import (
    _cap_density,
    _cap_sections_to_page_ceiling,
    _guide_density,
    _normalize_user_toc,
    _parse_toc_lines,
    _toc_looks_like_prose,
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
    estimate_rendered_pages,
    hard_page_ceiling,
    is_acceptable_page_count,
    is_within_page_range,
    max_planned_entries,
    page_range_prompt_suffix,
    reconcile_plan_to_page_band,
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


def _essay_profile() -> EditorialProfile:
    return EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        pages_per_entry=2.0,
    )


def _full_cookbook_profile(pages_per_entry: float = 2.0) -> EditorialProfile:
    """A fully-templated recipe card profile — the real shape behind the
    Part 5 recalibration: photo + mandatory ingredient grid + numbered steps
    + meta strip, matching the real "Oma's Poor-Man Kitchen" book's saved
    bible.json exactly (image_density=every_entry, require_quantities=True,
    show_meta=True)."""
    return EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(
            entry_noun="recipe", require_quantities=True, show_meta=True,
        ),
        structure_template="essay_entries",
        pages_per_entry=pages_per_entry,
        image_density="every_entry",
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
    assert allowed_page_range(300) == (300, 300)


def test_allowed_page_range_target_200():
    assert allowed_page_range(200) == (200, 220)


def test_allowed_page_range_target_100():
    assert allowed_page_range(100) == (100, 120)


def test_allowed_page_range_target_170():
    assert allowed_page_range(170) == (170, 190)


def test_allowed_page_range_target_5():
    assert allowed_page_range(5) == (5, 6)


def test_meets_page_floor():
    from bookforge.pipeline.page_limits import meets_page_floor
    assert meets_page_floor(160, 160)
    assert not meets_page_floor(116, 160)


def test_is_acceptable_page_count():
    assert is_acceptable_page_count(160, 160)
    assert not is_acceptable_page_count(120, 160)
    assert is_acceptable_page_count(180, 160)
    assert not is_acceptable_page_count(181, 160)
    assert not is_acceptable_page_count(301, 300)
    assert is_acceptable_page_count(300, 300)
    assert is_acceptable_page_count(120, 100)
    assert not is_acceptable_page_count(99, 100)
    assert is_acceptable_page_count(119, 100)
    assert not is_acceptable_page_count(121, 100)


def test_hard_ceiling_never_exceeds_absolute_max():
    assert hard_page_ceiling(300) == ABSOLUTE_PAGE_CEILING
    assert hard_page_ceiling(290) == 300
    assert hard_page_ceiling(200) == 220
    assert hard_page_ceiling(100) == 120
    assert hard_page_ceiling(400) == ABSOLUTE_PAGE_CEILING


def test_check_page_count_in_range():
    check_page_count(100, 100)
    check_page_count(120, 100)
    check_page_count(300, 300)


def test_check_page_count_raises_over_ceiling():
    with pytest.raises(PageLimitError) as exc:
        check_page_count(301, 200)
    assert exc.value.pages == 301
    assert exc.value.hi == 220

    with pytest.raises(PageLimitError):
        check_page_count(301, 300)


def test_check_page_count_raises_under_floor():
    with pytest.raises(PageLimitError) as exc:
        check_page_count(99, 100)
    assert exc.value.pages == 99
    assert exc.value.lo == 100

    with pytest.raises(PageLimitError):
        check_page_count(151, 100)


def test_max_planned_entries_uses_target_not_hi():
    profile = _essay_profile()
    at_75 = max_planned_entries(75, profile, 11, pamphlet=False)
    content_at_target = 75 - 13
    assert at_75 < 365
    assert content_at_target > 0


def test_365_recipes_scaled_to_target_pages():
    profile = _essay_profile()
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
    plan_cap = 5
    num_sections = density.base_sections * profile.tiers
    overhead = 2
    assert overhead + num_sections * density.page_budget <= plan_cap


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
    from bookforge.pipeline.page_limits import estimate_rendered_pages

    assert estimate_rendered_pages(capped, profile, pamphlet=True) <= 6
    assert planned_use_count(capped) < planned_use_count(sections)


def test_cap_sections_reduces_section_count():
    # Target chosen so the SHELL alone (divider + opener per section) busts the
    # ceiling, which is what forces sections to be dropped rather than merely
    # thinned. 20 sections at 75 pages no longer qualifies: once the estimator
    # was calibrated to what layout/guide.py really emits, 20 one-entry
    # chapters genuinely fit 75 pages (~66 rendered), so nothing needs dropping.
    profile = _essay_profile()
    target = 30
    sections = [
        SectionPlan(
            index=i, title=f"S{i}", full_card_quota=6, list_quota=0,
            page_budget=20, image_quota=6,
        )
        for i in range(1, 21)
    ]
    capped = _cap_sections_to_page_ceiling(
        sections, target, profile, pamphlet=False,
    )
    from bookforge.pipeline.page_limits import (
        estimate_rendered_pages,
        hard_page_ceiling,
    )

    assert len(capped) < len(sections)
    assert estimate_rendered_pages(
        capped, profile, pamphlet=False,
    ) <= hard_page_ceiling(target)
    assert all(s.image_quota <= s.full_card_quota for s in capped)


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


def test_scale_chapters_trims_overshoot():
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


def test_scale_chapters_never_trims_below_promised_floor():
    """A severe overshoot must not let the trimmer pop whole chapters past
    the book's promised entry count — it should stop at the floor and let
    the caller ship an over-length book with a warning instead."""
    chapters = [
        Chapter(
            index=i,
            title=f"Chapter {i}",
            blocks=[ContentBlock(
                type=BlockType.use_card,
                entries=[UseEntry(number=1, title=f"Recipe {i}")],
            )],
        )
        for i in range(1, 11)  # 10 chapters, 1 card each = 10 entries total
    ]
    bible = _minimal_bible(
        target_pages=75, promised_uses=8, original_promised_uses=8,
    )
    scaled, removed = scale_chapters_to_pages(
        chapters, bible, current_pages=500, target_pages=75,
    )
    remaining = sum(
        len(b.entries) for c in scaled for b in c.blocks
        if b.type in (BlockType.use_card, BlockType.use_list)
    )
    assert remaining >= 8


def test_scale_chapters_escalates_shrink_across_attempts():
    """A later page-scale attempt must never apply a LOOSER prose-shrink
    than an earlier one, even if the ratio-based estimate computed from the
    (already-shrunk) current page count would suggest one — otherwise the
    loop can stall above the ceiling once truncation becomes a no-op."""
    long_intro = "This is a very long recipe introduction. " * 10
    def make_chapters():
        return [
            Chapter(
                index=1, title="Chapter 1",
                blocks=[ContentBlock(
                    type=BlockType.use_card,
                    entries=[UseEntry(number=1, title="Recipe 1", intro=long_intro)],
                )],
            ),
        ]
    bible = _minimal_bible(
        target_pages=100, promised_uses=1, original_promised_uses=1,
    )
    # Attempt 0: 253 pages vs a 120 ceiling -> ratio-based shrink (~0.47).
    scaled0, _ = scale_chapters_to_pages(
        make_chapters(), bible, current_pages=253, target_pages=100,
        scale_attempt=0,
    )
    len0 = len(scaled0[0].blocks[0].entries[0].intro)
    # Attempt 1: the partial improvement from attempt 0 (253->205) makes the
    # RATIO estimate alone LOOSER (120/205 ~= 0.585) than attempt 0's ratio —
    # without escalation this would be a no-op on already-shrunk text and the
    # loop would stall above the ceiling forever. The escalation floor must
    # force it at least as tight as attempt 0 regardless.
    scaled1, _ = scale_chapters_to_pages(
        make_chapters(), bible, current_pages=205, target_pages=100,
        scale_attempt=1,
    )
    len1 = len(scaled1[0].blocks[0].entries[0].intro)
    assert len1 <= len0


def test_scale_chapters_shrinks_prose_when_at_the_promised_floor():
    """When entry count is already AT the promised floor (nothing left to
    remove), a severe overshoot must still shrink prose per entry — 'less
    content is fine' as long as the promised count is kept intact."""
    long_intro = "This is a very long recipe introduction. " * 10
    chapters = [
        Chapter(
            index=1, title="Chapter 1",
            blocks=[ContentBlock(
                type=BlockType.use_card,
                entries=[UseEntry(number=1, title="Recipe 1", intro=long_intro)],
            )],
        ),
    ]
    bible = _minimal_bible(
        target_pages=75, promised_uses=1, original_promised_uses=1,
    )
    scaled, removed = scale_chapters_to_pages(
        chapters, bible, current_pages=500, target_pages=75,
    )
    assert removed == 0  # already at the floor — nothing removable
    shrunk_intro = scaled[0].blocks[0].entries[0].intro
    assert len(shrunk_intro) < len(long_intro)


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


def test_reconcile_promise_capped_to_page_ceiling():
    """Over-promise at full size raises PlanInfeasibleError (no silent shrink)."""
    from bookforge.errors import PlanInfeasibleError

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
    with pytest.raises(PlanInfeasibleError):
        reconcile_promise_book(bible)


def test_parse_toc_lines_groups_recipes_under_sections():
    toc = [
        "Section 1: Breakfast",
        "Pancakes",
        "Eggs",
        "Section 2: Lunch",
        "Soup",
    ]
    parsed = _parse_toc_lines(toc)
    assert len(parsed) == 2
    assert parsed[0].title == "Section 1: Breakfast"
    assert [e.title for e in parsed[0].entries] == ["Pancakes", "Eggs"]


def test_parse_toc_lines_recognizes_chapter_headers():
    toc = [
        "01", "Introduction: Why This Matters",
        "02", "How to Use This Book",
        "05", "Chapter 1: Three-Ingredient Meals",
        "06", "Chapter 2: Forgotten Potato Dishes",
    ]
    parsed = _parse_toc_lines(toc)
    titles = [s.title for s in parsed]
    assert "Chapter 1: Three-Ingredient Meals" in titles
    assert "Chapter 2: Forgotten Potato Dishes" in titles
    # Bare ordinal placeholders must not leak in as entries.
    for s in parsed:
        for e in s.entries:
            assert e.title not in ("01", "02", "05", "06")


def test_parse_toc_lines_ordinal_marks_any_line_as_header():
    """Front/back-matter titles rarely say 'Chapter N:' but are still each
    their own section when paired with a bare ordinal marker."""
    toc = [
        "01", "Introduction: Why This Matters",
        "02", "How to Use This Book",
        "03", "Chapter 1: Three-Ingredient Meals",
        "04", "A Week of Sample Menus",
    ]
    parsed = _parse_toc_lines(toc)
    titles = [s.title for s in parsed]
    assert titles == [
        "Introduction: Why This Matters",
        "How to Use This Book",
        "Chapter 1: Three-Ingredient Meals",
        "A Week of Sample Menus",
    ]
    assert all(len(s.entries) == 0 for s in parsed)


def test_parse_toc_lines_strips_dot_leader_page_numbers():
    """Regression test: a pasted TOC ("How to Use This Cookbook ......... 3")
    carries its own dot-leader + page number, which is source-document
    bookkeeping, not part of the title — it was leaking verbatim into
    generated chapter-opener headings and corrupting them (confirmed via a
    real shipped book's fit_report.json overflow entries)."""
    toc = [
        "01",
        "Introduction: When German Grandmothers Made Something from Almost "
        "Nothing .......... 2",
        "02",
        "How to Use This Cookbook ................................"
        "................................. 3",
        "03",
        "Chapter 1: Three-Ingredient Meals ..................................."
        "................ 6",
        "Normal line with no dots",
    ]
    parsed = _parse_toc_lines(toc)
    titles = [s.title for s in parsed]
    assert (
        "Introduction: When German Grandmothers Made Something from Almost "
        "Nothing" in titles
    )
    assert "How to Use This Cookbook" in titles
    assert "Chapter 1: Three-Ingredient Meals" in titles
    assert not any(".." in t for t in titles)


def test_toc_looks_like_prose_detects_pasted_description():
    prose_toc = [
        "Create a warm, nostalgic German cookbook featuring 60 affordable recipes "
        "inspired by the resourceful home cooking of German grandmothers.",
        "The cookbook should contain exactly 60 complete recipes, divided into six "
        "recipe chapters with 10 recipes in each chapter.",
        "Use metric measurements, including grams, kilograms, millilitres, litres.",
    ]
    assert _toc_looks_like_prose(prose_toc) is True

    real_toc = [
        "01", "Introduction",
        "02", "Chapter 1: Three-Ingredient Meals",
        "03", "Chapter 2: Forgotten Potato Dishes",
        "04", "Recipe Index",
    ]
    assert _toc_looks_like_prose(real_toc) is False


def test_normalize_user_toc_discards_prose_falls_back_to_ai_planning():
    prose_toc = [
        "Create a warm, nostalgic German cookbook featuring 60 affordable recipes "
        "inspired by the resourceful home cooking of German grandmothers alive today.",
        "The cookbook should contain exactly 60 complete recipes across six chapters "
        "with ten recipes in each chapter, covering breakfast through dessert.",
        "Use metric measurements throughout, including grams, millilitres, and Celsius.",
    ]
    result = _normalize_user_toc(prose_toc, 100, _essay_profile(), pamphlet=False)
    assert result is None


def test_normalize_user_toc_collapses_recipe_list():
    toc = ["Section 1: Starters"] + [f"Recipe {i}" for i in range(1, 51)]
    toc += ["Section 2: Mains"] + [f"Dish {i}" for i in range(1, 51)]
    normalized = _normalize_user_toc(
        toc, 300, _essay_profile(), pamphlet=False,
    )
    assert normalized is not None
    assert len(normalized) <= 60
    assert sum(len(s.entries) for s in normalized) >= 50


def test_upsize_sections_distributes_list_quota_across_sections():
    from bookforge.pipeline.page_limits import _upsize_sections_to_estimate

    sections = [
        SectionPlan(index=1, title="A", full_card_quota=8, list_quota=0),
        SectionPlan(index=2, title="B", full_card_quota=8, list_quota=0),
        SectionPlan(index=3, title="C", full_card_quota=8, list_quota=0),
    ]
    out = _upsize_sections_to_estimate(
        sections, _essay_profile(), target_estimate=10_000,
    )
    # full_card_quota is already maxed on every section, so the list_quota
    # fallback must fire — and it must not dump everything on section 1.
    assert out[0].list_quota > 0
    assert out[1].list_quota > 0 or out[2].list_quota > 0


def test_upsize_sections_respects_promised_ceiling():
    from bookforge.pipeline.content_guardrails import planned_use_count
    from bookforge.pipeline.page_limits import _upsize_sections_to_estimate

    # Plenty of headroom (10 sections x up to 20 items each = 200 max) so an
    # unguarded loop would sail past the promise chasing the page target.
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=0, list_quota=0)
        for i in range(1, 11)
    ]
    out = _upsize_sections_to_estimate(
        sections, _essay_profile(), target_estimate=10_000,
        promised_ceiling=60,
    )
    # Must stop inflating quantity near the 60-item promise (10% tolerance),
    # not chase the page target indefinitely with more filler entries.
    assert planned_use_count(out) <= 66


def test_flat_profile_unaffected_by_full_card_recalibration():
    """A profile that isn't a fully-templated card (no require_quantities,
    no show_meta) must keep the original flat pages_per_entry * factor cost —
    the recalibration is scoped to the shape it was measured against, not a
    blanket change to every cookbook-ish profile."""
    from bookforge.pipeline.page_limits import _effective_pages_per_entry

    profile = _essay_profile()  # image_density defaults every_entry, but no
    # require_quantities/show_meta set
    assert _effective_pages_per_entry(profile) == pytest.approx(2.0 * 0.9)


def test_full_cookbook_card_has_structural_floor_even_in_compact_mode():
    """Regression test for the core Part 5 finding: compact mode's much lower
    pages_per_entry (0.8) still measured 1.80 REAL pages/entry on a real
    production run, because the mandatory photo/ingredient-grid/callout
    elements — not prose length — drive the page cost. A flat multiplier
    (0.8 * 0.9 = 0.72) badly undershoots; the structural-floor model must
    not."""
    from bookforge.pipeline.page_limits import _effective_pages_per_entry

    compact_profile = _full_cookbook_profile(pages_per_entry=0.8)
    flat_would_be = 0.8 * 0.9
    assert _effective_pages_per_entry(compact_profile) > flat_would_be * 2


def test_feature_box_pages_present_for_essay_entries_absent_for_tips_cards():
    """essay_entries' own rhythm calls for "1-2 feature boxes between
    entries" (templates.py) — these render as genuinely separate pages that
    were previously entirely unpriced. tips_cards has no feature_menu and
    must stay at zero."""
    from bookforge.pipeline.page_limits import _feature_box_pages_per_section

    essay_profile = _full_cookbook_profile()
    assert _feature_box_pages_per_section(essay_profile) > 0

    tips_profile = EditorialProfile(
        genre="reference", audience="general", angle="practical",
        entry_schema=EntrySchema(entry_noun="tip"),
        structure_template="tips_cards", pages_per_entry=1.0,
    )
    assert _feature_box_pages_per_section(tips_profile) == 0.0


def test_estimate_lands_close_to_real_oma_book_measurement():
    """Regression test using the real "Oma's Poor-Man Kitchen" book's shape
    (60 recipes, 15 sections, compact density) — a real production run of
    this exact shape rendered 193 pages while the pre-Part-5 estimate said
    only 78 (a 2.47x miss) and let the job run to completion, burning $31.38,
    before failing. The recalibrated estimate must land close to reality,
    not just closer-than-before."""
    profile = _full_cookbook_profile(pages_per_entry=0.8)  # compact, matches
    # the real saved bible.json exactly
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=4, list_quota=0)
        for i in range(1, 16)  # 15 sections x 4 = 60 entries
    ]
    est = estimate_rendered_pages(sections, profile, pamphlet=False)
    assert 140 <= est <= 220, est


def test_trim_sections_to_estimate_never_drops_below_min_entries():
    """Regression test: the section-popping fallback in
    _trim_sections_to_estimate had no min_entries guard (unlike the
    card-quota-reduction loop right above it in the same function), so it
    could silently pop whole sections and blow through the promised-item
    floor in one shot — accepting a plan that quietly broke a 60-item
    marketing promise instead of correctly leaving
    reconcile_plan_to_page_band's caller to raise PlanInfeasibleError."""
    from bookforge.pipeline.content_guardrails import planned_use_count
    from bookforge.pipeline.page_limits import _trim_sections_to_estimate

    profile = _full_cookbook_profile(pages_per_entry=0.8)
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=4, list_quota=0)
        for i in range(1, 16)  # 60 entries, same shape as the real book
    ]
    # Target far too small for 60 entries at this profile's real page cost —
    # section-popping WILL be needed to even attempt reaching it.
    out = _trim_sections_to_estimate(
        sections, profile, target_estimate=20, min_entries=60,
    )
    assert planned_use_count(out) >= 60


def test_reconcile_overshoot_gives_achievable_raise_pages_suggestion():
    """The overshoot branch (unlike the undershoot ceiling-capped one) should
    suggest a real, checked target_pages to raise to, since raising the
    target genuinely raises the ceiling here."""
    profile = _full_cookbook_profile(pages_per_entry=0.8)
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=4, list_quota=0)
        for i in range(1, 16)
    ]
    with pytest.raises(PlanInfeasibleError) as excinfo:
        reconcile_plan_to_page_band(
            sections, profile, target_pages=81,
            min_entries=60, promised_ceiling=60,
        )
    exc = excinfo.value
    assert exc.suggested_pages is not None
    assert exc.suggested_pages > 81
    assert "raise target" in str(exc).lower()


def test_oma_book_scenario_rejected_before_any_spend():
    """The exact real production scenario that cost $31.38 before failing:
    60-recipe cookbook, target_pages=81 (band 81-98). With the Part 5
    recalibration, this must now be rejected by reconcile_plan_to_page_band
    (the same function the free dry-run TOC check and the job's own
    build_bible planning stage both call) — for $0 or for the cost of one
    planning call, instead of running the full pipeline (content + images)
    to find out 45+ minutes and $31 later."""
    profile = _full_cookbook_profile(pages_per_entry=0.8)
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=4, list_quota=0)
        for i in range(1, 16)
    ]
    with pytest.raises(PlanInfeasibleError):
        reconcile_plan_to_page_band(
            sections, profile, target_pages=81,
            min_entries=60, promised_ceiling=60,
        )


def test_reconcile_undershoot_ceiling_capped_never_advises_raising_pages():
    """Regression test: a plan pinned to its promised-item ceiling cannot be
    fixed by raising target_pages (it only raises the floor, not the
    achievable estimate) — the error must say so and suggest a pages figure
    that's actually achievable, never "raise pages"."""
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=0, list_quota=0)
        for i in range(1, 11)
    ]
    with pytest.raises(PlanInfeasibleError) as excinfo:
        reconcile_plan_to_page_band(
            sections, _essay_profile(), target_pages=1000,
            promised_ceiling=60,
        )
    exc = excinfo.value
    assert exc.ceiling_capped is True
    assert exc.suggested_pages is not None
    assert exc.suggested_pages < 1000
    assert "raise pages" not in str(exc).lower()


def test_reconcile_undershoot_without_ceiling_keeps_raise_pages_advice():
    """When the shortfall isn't caused by a promised-count ceiling, sections
    genuinely can still grow with a higher target — the existing advice is
    valid there and should be unchanged."""
    sections = [
        SectionPlan(index=1, title="A", full_card_quota=0, list_quota=0),
        SectionPlan(index=2, title="B", full_card_quota=0, list_quota=0),
    ]
    with pytest.raises(PlanInfeasibleError) as excinfo:
        reconcile_plan_to_page_band(
            sections, _essay_profile(), target_pages=1000,
        )
    exc = excinfo.value
    assert exc.ceiling_capped is False
    assert "raise pages" in str(exc).lower()


def test_reconcile_undershoot_sections_locked_never_advises_raising_pages():
    """Regression test: a locked table of contents fixes the section count
    regardless of target_pages, so raising the target can't help here either
    — same contract as the promised-item ceiling case, but triggered by
    `sections_locked` instead of `ceiling_capped`."""
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=0, list_quota=0)
        for i in range(1, 3)
    ]
    with pytest.raises(PlanInfeasibleError) as excinfo:
        reconcile_plan_to_page_band(
            sections, _essay_profile(), target_pages=1000,
            sections_locked=True,
        )
    exc = excinfo.value
    assert exc.ceiling_capped is False
    assert exc.suggested_pages is not None
    assert exc.suggested_pages < 1000
    assert "raise pages" not in str(exc).lower()
    assert "table of contents" in str(exc).lower()


def test_assemble_ships_oversized_pdf_with_advisory(monkeypatch):
    # Page limits are OWNED by the orchestrator's scale loops (which can trim
    # and re-render); assemble only records an advisory so the loop can act.
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
    monkeypatch.setattr(assemble.render, "render_pdf",
                        lambda html: (b"%PDF-1.4", {"stuck": [], "overflows": [],
                                                    "rescued": [], "pages": []}))
    monkeypatch.setattr(assemble, "_linearize", lambda pdf: pdf)
    monkeypatch.setattr(assemble, "_page_count", lambda pdf: 37)
    monkeypatch.setattr(assemble.pdf_geometry, "check_pdf_geometry", lambda pdf: [])
    monkeypatch.setattr(assemble.storage, "put", lambda *a, **k: None)

    art = assemble.assemble("book1", bible, [], [], qa)
    assert art.page_count == 37
    assert any("outside target range" in i for i in art.qa.layout_issues)


def _fake_overshoot_scale(monkeypatch):
    from bookforge.pipeline import orchestrator
    from bookforge.schemas import BookArtifact, QAResult

    qa = QAResult(groundedness=1.0, passed=True)

    def _fake_assemble(*args, **kwargs):
        return BookArtifact(
            book_id="b1", interior_pdf_key="k", page_count=80,
            file_size_mb=1.0, qa=qa,
        )

    monkeypatch.setattr(orchestrator, "_assemble_with_layout_revise", _fake_assemble)
    monkeypatch.setattr(orchestrator.settings, "max_page_scale_revisions", 0)
    # Final vision QA has its own coverage; keep this test about page limits.
    monkeypatch.setattr(orchestrator, "_final_vision_qa",
                        lambda book_id, artifact, *a, **k: artifact)
    return orchestrator, qa


def test_orchestrator_raises_on_overshoot_with_hatch_disabled(monkeypatch):
    # SHIP_ON_PAGE_OVERSHOOT now defaults to TRUE, mirroring
    # SHIP_ON_PAGE_UNDERSHOOT: by the time content/images/layout are all
    # generated, a too-long book beats throwing away the work. Turning the
    # hatch off restores the strict behavior, which is what this test covers.
    # The default (ship) path is covered by test_orchestrator_ships_overshoot_with_escape_hatch.
    orchestrator, qa = _fake_overshoot_scale(monkeypatch)
    monkeypatch.setattr(orchestrator.settings, "ship_on_page_overshoot", False)
    with pytest.raises(PageLimitError):
        orchestrator._assemble_with_page_scale(
            "b1", _minimal_bible(target_pages=50, sections=[]), [], [], qa,
            cover_key=None, pro=None, source=None, stage=lambda s: None,
        )


def test_orchestrator_ships_overshoot_with_escape_hatch(monkeypatch):
    orchestrator, qa = _fake_overshoot_scale(monkeypatch)
    monkeypatch.setattr(orchestrator.settings, "ship_on_page_overshoot", True)
    art = orchestrator._assemble_with_page_scale(
        "b1", _minimal_bible(target_pages=50, sections=[]), [], [], qa,
        cover_key=None, pro=None, source=None, stage=lambda s: None,
    )
    assert art.degraded
    assert any("exceeds ceiling" in w for w in art.warnings)


def test_orchestrator_raises_on_undershoot(monkeypatch):
    # SHIP_ON_PAGE_UNDERSHOOT now defaults to TRUE: feasibility is gated at
    # /readiness, so a request that cannot reach its target is refused before
    # generation, and a book that gets this far ships short rather than
    # throwing away an hour of work. Turning the hatch off restores the strict
    # behavior, which is what this test covers. The default (ship) path is
    # covered by test_page_floor.py.
    orchestrator, qa = _fake_overshoot_scale(monkeypatch)
    monkeypatch.setattr(orchestrator.settings, "ship_on_page_undershoot", False)
    with pytest.raises(PageLimitError) as exc:
        orchestrator._assemble_with_page_scale(
            "b1", _minimal_bible(target_pages=100, sections=[]), [], [], qa,
            cover_key=None, pro=None, source=None, stage=lambda s: None,
        )
    assert exc.value.pages == 80
    assert exc.value.lo == 100


def test_page_range_prompt_mentions_band():
    text = page_range_prompt_suffix(75)
    assert "75" in text
    assert "90" in text
    assert "at least" in text.lower()
    assert "must not exceed" in text.lower()


def test_is_within_page_range():
    assert not is_within_page_range(99, 100)
    assert is_within_page_range(100, 100)
    assert is_within_page_range(120, 100)
    assert not is_within_page_range(121, 100)
