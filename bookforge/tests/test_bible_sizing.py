"""Verify target_pages drives practical-guide section sizing."""
from bookforge.pipeline.bible import (
    _BaseSection,
    _expand_sections,
    _max_specials,
    _plan_dimensions,
    _section_quotas,
)


def test_plan_dimensions_scales_with_target():
    assert _plan_dimensions(1) == (1, 1)
    assert _plan_dimensions(10) == (1, 1)
    assert _plan_dimensions(100) == (4, 2)
    assert _plan_dimensions(200) == (7, 2)


def test_section_budget_sums_near_target():
    for pages in (10, 30, 60, 100, 200):
        base, tiers = _plan_dimensions(pages)
        n = base * tiers
        _, _, _, pb = _section_quotas(pages, n)
        assert n * pb <= pages + 4
        assert n * pb >= pages - 10


def test_small_targets_have_lean_quotas():
    _, _, _, pb_small = _section_quotas(10, 1)
    fc_small, lq_small, _, _ = _section_quotas(10, 1)
    _, _, _, pb_large = _section_quotas(100, 8)
    fc_large, lq_large, _, _ = _section_quotas(100, 8)
    assert pb_small < pb_large
    assert fc_small <= fc_large
    assert lq_small < lq_large


def test_specials_capped_for_small_books():
    assert _max_specials(10) == 0
    assert _max_specials(30) == 1
    assert _max_specials(100) == 3


def test_single_tier_uses_plain_section_title():
    sections = _expand_sections(
        [_BaseSection(title="Kitchen", summary="Tips.", divider_subject="bowl")],
        target_pages=10,
        tiers_count=1,
    )
    assert sections[0].title == "Kitchen"
