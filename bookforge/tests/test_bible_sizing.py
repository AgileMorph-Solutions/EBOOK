"""Verify practical-guide bible helpers."""
from bookforge.pipeline.bible import (
    _BaseSection,
    _apply_cookbook_profile_bias,
    _enforce_toc_sections,
    _expand_sections,
    _guide_density,
    _is_cookbookish,
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
