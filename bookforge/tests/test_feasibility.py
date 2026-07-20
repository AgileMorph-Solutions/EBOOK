"""Feasibility: compact density vs infeasible count — never auto-shrink."""
from bookforge.pipeline.feasibility import (
    COMPACT_PAGES_PER_ENTRY,
    assess_request,
    max_entries_for_pages,
    min_pages_for_entries,
    resolve_density,
)
from bookforge.pipeline.bible import _cap_sections_to_page_ceiling
from bookforge.pipeline.content_guardrails import planned_use_count
from bookforge.schemas import EditorialProfile, EntrySchema, SectionPlan


def _essay_profile(ppe: float = 2.0) -> EditorialProfile:
    return EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="frugal",
        entry_schema=EntrySchema(entry_noun="recipe", headnote_lo=12, headnote_hi=22),
        structure_template="essay_entries",
        pages_per_entry=ppe,
        image_density="every_entry",
    )


def test_100_at_150_is_compact_not_infeasible():
    result = resolve_density(promised=100, target_pages=150, toc_len=12)
    assert result.status == "compact"
    assert result.promised == 100
    assert result.pages_per_entry == COMPACT_PAGES_PER_ENTRY
    assert result.options == []


def test_100_at_50_is_infeasible_with_options():
    result = resolve_density(promised=100, target_pages=50, toc_len=12)
    assert result.status == "infeasible"
    assert result.promised == 100
    assert len(result.options) == 2
    ids = {o.id for o in result.options}
    assert ids == {"raise_pages", "lower_count"}
    lower = next(o for o in result.options if o.id == "lower_count")
    assert lower.promised_count is not None
    assert lower.promised_count < 100


def test_accepted_lower_count_makes_feasible():
    blocked = assess_request(
        topic="100 Filling Dinners for One",
        target_pages=50,
    )
    assert blocked.status == "infeasible"
    accepted = assess_request(
        topic="100 Filling Dinners for One",
        target_pages=50,
        accepted_promised_count=blocked.max_entries_at_target,
    )
    assert accepted.status in ("ok", "compact")
    assert accepted.promised == blocked.max_entries_at_target


def test_parse_dinners_as_promised_count():
    from bookforge.pipeline.content_guardrails import parse_promised_count
    assert parse_promised_count("100 Filling Dinners for One", "") == 100


def test_28_at_80_standard_ok():
    result = resolve_density(promised=28, target_pages=80, toc_len=10)
    assert result.status in ("ok", "compact")
    assert result.promised == 28


def test_cap_sections_never_merges_into_one_mega_section():
    profile = _essay_profile(ppe=0.8)
    sections = [
        SectionPlan(
            index=i, title=f"Chapter {i}", full_card_quota=6, list_quota=0,
            page_budget=20, image_quota=6,
        )
        for i in range(1, 15)
    ]
    capped = _cap_sections_to_page_ceiling(
        sections, 150, profile, pamphlet=False, min_entries=84,
    )
    assert len(capped) > 1
    assert planned_use_count(capped) >= 84
    assert all(s.full_card_quota <= 8 for s in capped)


def test_cap_without_min_entries_may_still_shrink_legacy():
    profile = _essay_profile(ppe=2.0)
    sections = [
        SectionPlan(
            index=i, title=f"S{i}", full_card_quota=20, list_quota=0,
            page_budget=28,
        )
        for i in range(1, 6)
    ]
    capped = _cap_sections_to_page_ceiling(
        sections, 5, profile, pamphlet=True, min_entries=0,
    )
    assert planned_use_count(capped) < planned_use_count(sections)


def test_min_pages_and_max_entries_inverse():
    n = 12
    pages = min_pages_for_entries(56, 0.8, n)
    max_e = max_entries_for_pages(pages, 0.8, n)
    assert max_e >= 50
