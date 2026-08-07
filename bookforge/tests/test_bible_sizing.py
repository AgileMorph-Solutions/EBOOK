"""Verify practical-guide bible helpers."""
from bookforge.pipeline.bible import (
    _BaseSection,
    _apply_cookbook_profile_bias,
    _enforce_toc_sections,
    _expand_sections,
    _guide_density,
    _is_cookbookish,
    dry_run_toc_feasibility,
)
from bookforge.pipeline.content_guardrails import (
    parse_promised_count,
    planned_use_count,
    shrink_sections_to_count,
)
from bookforge.schemas import EditorialProfile, EntrySchema, SourceDoc


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


def test_enforce_toc_sections_pads_after_critique_drop():
    existing = [
        _BaseSection(title="Old A", summary="a", divider_subject="bowl"),
        _BaseSection(title="Old B", summary="b", divider_subject="pot"),
    ]
    toc = ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"]
    enforced = _enforce_toc_sections(existing, toc)
    assert len(enforced) == 4
    assert [s.title for s in enforced] == toc
    assert enforced[0].summary == "a"
    assert enforced[2].summary == ""
    assert enforced[3].entries == []


def test_cookbookish_detects_recipe_topic():
    src = SourceDoc(topic="365 Forgotten American Recipes", text="x" * 500)
    assert _is_cookbookish(src, description="for seniors", toc=["a"] * 5)


def test_cookbook_profile_bias_forces_every_entry_and_spread_template():
    profile = EditorialProfile(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="tips_cards",
        image_density="sparse",
        pages_per_entry=1.0,
    )
    biased = _apply_cookbook_profile_bias(
        profile, cookbookish=True, has_user_toc=True,
    )
    assert biased.image_density == "every_entry"
    assert biased.structure_template == "project_based"
    assert biased.pages_per_entry == 2.0


def test_smoke_density_five_pages():
    # every_entry pamphlet: image_quota matches cards (not capped at 1).
    density = _guide_density(5, _pest_profile())
    assert density.list_quota == 0
    assert density.cards_per_section <= 3
    assert density.image_quota == density.cards_per_section

    sparse = _pest_profile().model_copy(update={"image_density": "sparse"})
    sparse_d = _guide_density(5, sparse)
    assert sparse_d.image_quota <= 1


def test_smoke_expand_and_shrink_to_subtitle_promise():
    """Pamphlet: 3 TOC sections x tiers=1, shrink to subtitle promise of 21."""
    profile = _pest_profile().model_copy(update={"tiers": 1})
    density = _guide_density(5, profile)
    bases = [
        _BaseSection(title=t, summary="s", divider_subject="d")
        for t in ("Roaches", "Ants", "Mice")
    ]
    sections = _expand_sections(bases, density, profile)
    planned = planned_use_count(sections)
    promised = parse_promised_count(
        "Senior Pest Control",
        "21 Cheap, Safe DIY Fixes Before You Pay an Exterminator",
    )
    assert promised == 21
    if planned > promised:
        sections = shrink_sections_to_count(sections, promised)
    assert planned_use_count(sections) <= 21
    assert all(s.list_quota == 0 for s in sections)


def test_pamphlet_density_honors_topic_promise():
    profile = EditorialProfile(
        genre="cookbook",
        audience="home cooks",
        angle="quick",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        image_density="sparse",
        pages_per_entry=2.0,
        tiers=1,
    )
    density = _guide_density(5, profile, promised=4)
    assert density.base_sections * density.cards_per_section >= 4


def test_cookbook_density_image_quota_standard():
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


def test_dry_run_toc_feasibility_propagates_options_from_plan_infeasible(monkeypatch):
    """Regression test for the "raise pages" bug: when the real page-band
    reconciliation determines the plan is ceiling-capped, dry_run_toc_feasibility
    must surface the SAME structured remediation (a lower_pages option), not a
    stale or contradictory one — verified by mocking reconcile_plan_to_page_band
    itself so this test doesn't depend on the TOC-normalization/density
    arithmetic (covered separately in test_page_limits.py)."""
    from bookforge.pipeline import bible
    from bookforge.errors import PlanInfeasibleError

    def fake_reconcile(*args, **kwargs):
        raise PlanInfeasibleError(
            "plan tops out at 99 pages", ceiling_capped=True, suggested_pages=99,
        )

    monkeypatch.setattr(bible, "reconcile_plan_to_page_band", fake_reconcile)

    toc = ["Section 1: Starters"] + [f"Recipe {i}" for i in range(1, 51)]
    toc += ["Section 2: Mains"] + [f"Dish {i}" for i in range(1, 51)]
    ok, msg, options = dry_run_toc_feasibility(
        topic="100 Forgotten Recipes",
        text="placeholder source text",
        description=None,
        toc=toc,
        target_pages=200,
        density_mode=None,
        accepted_promised_count=None,
    )
    assert ok is False
    assert "99 pages" in msg
    assert len(options) == 1
    assert options[0].id == "lower_pages"
    assert options[0].target_pages == 99


def test_dry_run_toc_feasibility_no_toc_returns_no_opinion():
    ok, msg, options = dry_run_toc_feasibility(
        topic="Simple Guide",
        text="placeholder",
        description=None,
        toc=None,
        target_pages=100,
        density_mode=None,
        accepted_promised_count=None,
    )
    assert ok is True
    assert msg == ""
    assert options == []
