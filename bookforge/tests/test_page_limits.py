"""Tests for page-count limits and content scaling."""
from __future__ import annotations

import pytest

from bookforge.errors import PageLimitError
from bookforge.pipeline.bible import (
    _cap_density,
    _cap_sections_to_page_ceiling,
    _guide_density,
    _normalize_user_toc,
    _parse_toc_lines,
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
    is_acceptable_page_count,
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


def _essay_profile() -> EditorialProfile:
    return EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        pages_per_entry=2.0,
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
    profile = _essay_profile()
    sections = [
        SectionPlan(
            index=i, title=f"S{i}", full_card_quota=6, list_quota=0,
            page_budget=20, image_quota=6,
        )
        for i in range(1, 21)
    ]
    capped = _cap_sections_to_page_ceiling(
        sections, 75, profile, pamphlet=False,
    )
    from bookforge.pipeline.page_limits import estimate_rendered_pages

    assert len(capped) < len(sections)
    assert estimate_rendered_pages(capped, profile, pamphlet=False) <= 90
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


def test_normalize_user_toc_collapses_recipe_list():
    toc = ["Section 1: Starters"] + [f"Recipe {i}" for i in range(1, 51)]
    toc += ["Section 2: Mains"] + [f"Dish {i}" for i in range(1, 51)]
    normalized = _normalize_user_toc(
        toc, 300, _essay_profile(), pamphlet=False,
    )
    assert normalized is not None
    assert len(normalized) <= 60
    assert sum(len(s.entries) for s in normalized) >= 50


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


def test_orchestrator_raises_on_overshoot_by_default(monkeypatch):
    orchestrator, qa = _fake_overshoot_scale(monkeypatch)
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
    orchestrator, qa = _fake_overshoot_scale(monkeypatch)
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
