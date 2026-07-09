"""Verify practical-guide bible helpers."""
from bookforge.pipeline.bible import (
    _BaseSection,
    _apply_cookbook_profile_bias,
    _enforce_toc_sections,
    _is_cookbookish,
)
from bookforge.schemas import EditorialProfile, EntrySchema, SourceDoc


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
