"""Tests for the genre-adaptive editorial-profile pipeline.

Covers: legacy back-compat, template density, safety-brief routing, the
profile-driven layout (labels/meta/essay/feature pages), the deterministic
guide QA audit, page-number/TOC integrity, and the zero-hardcoded-keywords
policy.
"""
from __future__ import annotations

import re

from bookforge import safety
from bookforge.layout import theme
from bookforge.layout.guide import GuideDirector, build_practical_html
from bookforge.pipeline import guide_images, layout_budget, templates
from bookforge.pipeline import bible as bible_stage
from bookforge.pipeline import content_guide
from bookforge.pipeline.content_guardrails import (
    parse_promised_count, reconcile_promise,
)
from bookforge.pipeline.qa import audit_guide_chapters
from bookforge.schemas import (
    ArtStyle, BlockType, BookBible, Chapter, ContentBlock, EditorialProfile,
    EntrySchema, SafetyCard, SectionPlan, StepItem, UseEntry, VoiceStyle,
    legacy_profile, profile_of,
)

# Vocabulary that used to be hardcoded into prompts/layout and leaked into
# every book. It may survive ONLY in the sanctioned legacy modules. Regexes:
# word boundaries keep the legacy preset KEY "senior_cookbook" (one word, must
# survive for old manifests) from false-positiving.
_BANNED_LEGACY_VOCAB = (
    r"\bseniors?\b", r"money-saving", r"hydrogen.peroxide", r"cheap rice",
    r"sparkling clean", r"\bgrout\b",
)


def _vocab_hits(text: str) -> list[str]:
    return [pat for pat in _BANNED_LEGACY_VOCAB
            if re.search(pat, text, re.IGNORECASE)]


def _art() -> ArtStyle:
    return ArtStyle(medium="photo", palette="cream and tomato red",
                    lighting="soft daylight", era="mid-century",
                    composition_rules="", negative_prompt="no text",
                    style_suffix="warm editorial photo")


def _voice() -> VoiceStyle:
    return VoiceStyle(narrator_persona="a food historian", tone_register="warm",
                      pov="second person", pacing="steady", vocabulary="plain")


def _cookbook_profile() -> EditorialProfile:
    return EditorialProfile(
        genre="heritage cookbook",
        audience="home cooks who love American history",
        angle="forgotten American food history, cooked again",
        persona="a food historian who cooks",
        safety_domain="food",
        entry_schema=EntrySchema(
            entry_noun="recipe", need_label="Ingredients", steps_label="Method",
            require_quantities=True, show_savings=False, show_meta=True,
            min_steps=4, max_steps=8, headnote_lo=35, headnote_hi=60,
        ),
        structure_template="essay_entries",
        pages_per_entry=2.0, tiers=1, quick_list_weight=0.15,
        image_density="every_entry",
        cover_kicker="American Heritage Cooking",
        cover_foot="Real dishes · Real history",
        back_matter_heading="Pantry Notes & Substitutions",
        grounded_content="the food history, dates, names, and places",
        authored_content="the complete recipes themselves",
        safety_rules="SAFETY RULES (obey exactly): cook poultry fully.",
        safety_cards=[
            SafetyCard(pair="Raw Chicken + the Salad Board",
                       why="Raw poultry juices spread bacteria."),
            SafetyCard(pair="Hot Oil + Water",
                       why="Water makes hot oil spatter violently."),
        ],
        danger_patterns=[["chicken", "rare"], ["hot oil", "water", "pour"],
                         ["thaw", "counter"]],
    )


def _recipe(number: int, title: str, opening: str = "In the winter of") -> UseEntry:
    return UseEntry(
        number=number, title=title,
        intro=f"{opening} 1953, this dish fed a family for pennies and "
              "became a quiet legend of the church-supper circuit.",
        what_you_need=[
            "2 cups yellow cornmeal", "1 cup all-purpose flour",
            "1 teaspoon baking soda", "1 large egg", "1 cup whole milk",
        ],
        steps=[StepItem(text="Heat the oven to 400°F and grease a skillet."),
               StepItem(text="Stir the cornmeal, flour, and baking soda together."),
               StepItem(text="Whisk in the egg and milk until smooth."),
               StepItem(text="Bake 20 to 25 minutes, until deep gold at the edges.")],
        serves="Serves 4", time="10 min prep · 25 min bake",
        variation="Stir in a handful of corn kernels for a summer version.",
        source_line="Adapted from a 1953 Swanson-era supper menu.",
    )


def _cookbook_bible() -> BookBible:
    return BookBible(
        title="The Lost American Memories Cookbook",
        subtitle="140 Heritage Recipes from the Foods That Shaped America",
        audience="home cooks", tone="warm, historical",
        art_style=_art(), voice=_voice(),
        book_kind="practical_guide",
        theme_name="heritage_cookbook",
        safety_key="food",
        profile=_cookbook_profile(),
        promised_uses=140,
        sections=[SectionPlan(
            index=1, title="Dinner in a Tray", summary="How Swanson changed supper.",
            part_index=1, part_title="The Cold Revolution",
            full_card_quota=2, list_quota=2, image_quota=2, page_budget=8,
            divider_subject="a vintage segmented dinner tray with a full meal",
            planned_entries=["Classic Tray Turkey Supper — the 1953 original"],
        )],
    )


def _cookbook_chapter() -> Chapter:
    return Chapter(index=1, title="Dinner in a Tray", blocks=[
        ContentBlock(type=BlockType.section_intro,
                     text="Supper changed forever in 1953."),
        ContentBlock(type=BlockType.history, heading="Dinner in a Tray",
                     text=("In 1953 the Swanson company found itself with a "
                           "mountain of Thanksgiving turkey and a bold idea. "
                           "The segmented aluminum tray went straight into the "
                           "oven, and America ate in front of the television. "
                           "What follows are the dishes of that moment.")),
        ContentBlock(type=BlockType.pull_quote,
                     text="Supper moved to the living room and never came back."),
        ContentBlock(type=BlockType.use_card, heading="Classic Tray Turkey Supper",
                     entries=[_recipe(1, "Classic Tray Turkey Supper")]),
        ContentBlock(type=BlockType.sidebar, heading="Then & Now",
                     text=("A frozen turkey dinner cost 98 cents in 1954 — about "
                           "eleven dollars today, roughly what a family meal kit "
                           "runs now.")),
        ContentBlock(type=BlockType.use_card, heading="Cornbread Dressing",
                     entries=[_recipe(2, "Cornbread Dressing",
                                      opening="Long before boxed mixes")]),
        ContentBlock(type=BlockType.use_list, heading="Variations & Quick Ideas",
                     text="Quick recipe ideas, one line each.",
                     entries=[
                         UseEntry(number=3, title="Leftover Turkey Hash",
                                  intro="Crisp diced turkey and potatoes in a hot skillet."),
                         UseEntry(number=4, title="Tray-Style Meatloaf",
                                  intro="Bake meatloaf portions in a divided pan."),
                     ]),
    ])


def _legacy_bible() -> BookBible:
    return BookBible(
        title="The Home Savings Guide", subtitle="250 Ways to Keep House",
        audience="Seniors (65+)", tone="warm",
        art_style=_art(), voice=_voice(),
        book_kind="practical_guide", theme_name="home_clean",
        safety_key="general", profile=None, promised_uses=250,
        sections=[SectionPlan(index=1, title="Kitchen — Essentials",
                              full_card_quota=1, list_quota=0, page_budget=4)],
    )


def _legacy_chapter() -> Chapter:
    return Chapter(index=1, title="Kitchen — Essentials", blocks=[
        ContentBlock(type=BlockType.section_intro, text="Welcome to the kitchen."),
        ContentBlock(type=BlockType.use_card, heading="Wipe the counters",
                     entries=[UseEntry(
                         number=1, title="Wipe the counters",
                         intro="A clean counter keeps the whole kitchen easy.",
                         what_you_need=["Cloth", "Spray bottle"],
                         steps=[StepItem(text="Spray the counter."),
                                StepItem(text="Wipe it dry."),
                                StepItem(text="Rinse the cloth.")],
                         savings="Replaces wipes, about $6 a month")]),
    ])


# --------------------------------------------------------------------------- #
# 1. Legacy back-compat
# --------------------------------------------------------------------------- #
def test_profile_of_legacy_defaults():
    b = _legacy_bible()
    p = profile_of(b)
    assert p.cover_kicker == "A Senior's Money-Saving Guide"
    assert p.entry_schema.need_label == "What You Need"
    assert p.entry_schema.show_savings is True
    assert p.structure_template == "tips_cards"


def test_old_manifest_bible_dict_deserializes():
    # An old manifest has no profile/part/planned_entries keys at all.
    old = _legacy_bible().model_dump(mode="json")
    for k in ("profile",):
        old.pop(k, None)
    for s in old["sections"]:
        for k in ("part_index", "part_title", "planned_entries"):
            s.pop(k, None)
    revived = BookBible(**old)
    assert revived.profile is None
    assert profile_of(revived).entry_schema.entry_noun == "tip"


def test_legacy_render_keeps_labels_and_kicker():
    html = build_practical_html(_legacy_bible(), [_legacy_chapter()], [])
    assert "What You Need" in html
    assert "You save" in html
    assert "A Senior&#x27;s Money-Saving Guide" in html


def test_legacy_safety_canon_unchanged():
    assert "hydrogen peroxide" in safety.rules_for("general").lower()
    assert safety.never_mix_for("hp")[0][0] == "Peroxide + Vinegar"
    b = _legacy_bible()
    assert safety.cards_for_bible(b) == safety.never_mix_for("general")
    assert safety.danger_patterns_for_bible(b)


# --------------------------------------------------------------------------- #
# 2. Density
# --------------------------------------------------------------------------- #
def test_cookbook_density_is_entry_scale_not_tip_scale():
    density = bible_stage._guide_density(350, _cookbook_profile())
    total = density.base_sections * (density.cards_per_section
                                     + density.list_quota)
    assert 120 <= total <= 220, total
    assert density.cards_per_section <= 7  # structured-output safety margin
    assert density.image_quota == density.cards_per_section  # every_entry


def test_legacy_density_unchanged():
    p = legacy_profile()
    density = bible_stage._guide_density(350, p)
    # legacy math: ~12 base topics × 2 tiers × (cards+list) ≈ 1,100+ tips
    total = density.base_sections * p.tiers * (
        density.cards_per_section + density.list_quota)
    assert total > 800


# --------------------------------------------------------------------------- #
# 3. Count coherence
# --------------------------------------------------------------------------- #
def test_promised_count_parses_hyphenated_and_recipes():
    assert parse_promised_count("T", "200 Money-Saving Kitchen Tips") == 200
    assert parse_promised_count("T", "150 Heritage Recipes") == 150


def test_reconcile_promise_rewrites_bad_subtitle():
    t, s, changed = reconcile_promise(
        "The Cookbook", "200 Money-Saving Kitchen Tips and Recipes", 1176)
    assert changed and "1176" in s and "200" not in s
    t, s, changed = reconcile_promise("The Cookbook", "140 Heritage Recipes", 138)
    assert not changed  # within 10%


# --------------------------------------------------------------------------- #
# 4. Profile-driven layout
# --------------------------------------------------------------------------- #
def test_cookbook_render_uses_profile_labels_and_structure():
    html = build_practical_html(_cookbook_bible(), [_cookbook_chapter()], [])
    assert "Ingredients" in html and "Method" in html
    assert "Serves 4" in html                      # meta strip
    assert "essaypage" in html                     # opener essay page
    assert "feature-box" in html                   # sidebar as designed feature
    assert "The Cold Revolution" in html           # part label on divider + TOC
    assert "American Heritage Cooking" in html     # profile cover kicker
    assert "Raw Chicken" in html                   # generated safety card
    assert "What You Need" not in html
    assert "You save" not in html
    assert "Money-Saving" not in html
    assert '<html lang="en"' in html
    assert "--safe-inner" in html                  # mirrored KDP margins
    assert "hyphens: auto" in html
    assert "overflow-wrap: anywhere" not in html


def test_toc_rows_are_atomic_and_pages_counted():
    groups = [("Part I", [(f"Chapter {i}", i + 6) for i in range(30)]),
              ("Reference", [("Chart", 40), ("Checklist", 41)])]
    n = GuideDirector.toc_page_count(groups)
    assert n == 1
    big = [("Chapters", [(f"Chapter {i} With a Fairly Long Evocative Title", i)
                         for i in range(120)])]
    assert GuideDirector.toc_page_count(big) >= 3
    d = GuideDirector(_cookbook_bible())
    pages = d.toc_pages(groups, n)
    assert len(pages) == n
    # every row keeps its page number inside the same unbreakable row div
    rows = re.findall(r'<div class="row">.*?</div>', pages[0])
    assert all('class="pg"' in r for r in rows) and len(rows) == 32


def test_folio_present_on_body_pages():
    html = build_practical_html(_cookbook_bible(), [_cookbook_chapter()], [])
    assert html.count('class="gfolio"') >= 4


# --------------------------------------------------------------------------- #
# 5. Deterministic QA audit
# --------------------------------------------------------------------------- #
def test_audit_passes_clean_cookbook_chapter():
    issues, findings = audit_guide_chapters([_cookbook_chapter()], _cookbook_bible())
    assert issues == [] and findings == []


def test_audit_flags_quantityless_recipe():
    ch = _cookbook_chapter()
    ch.blocks[3].entries[0].what_you_need = ["Cornmeal", "Flour", "Egg and milk"]
    issues, _ = audit_guide_chapters([ch], _cookbook_bible())
    assert any("without a quantity" in i for i in issues)


def test_audit_allows_equipment_and_seasoning_lines():
    # a lone equipment/oil/seasoning line among real quantities is NOT a defect
    ch = _cookbook_chapter()
    ch.blocks[3].entries[0].what_you_need = [
        "2 cups yellow cornmeal", "1 cup all-purpose flour",
        "Neutral oil, for frying", "Salt, to taste",
        "Heavy-duty freezer bags", "A candy thermometer",
    ]
    issues, _ = audit_guide_chapters([ch], _cookbook_bible())
    assert not any("without a quantity" in i for i in issues), issues


def test_audit_danger_scan_ignores_safety_warnings():
    # a step that WARNS against the danger is safe advice, not a violation
    ch = _cookbook_chapter()
    ch.blocks[3].entries[0].steps = [
        StepItem(text="Thaw the turkey in the refrigerator, never on the counter."),
        StepItem(text="Roast until a thermometer reads 165°F at the thickest point."),
    ]
    _, findings = audit_guide_chapters([ch], _cookbook_bible())
    assert not findings, [f.rule for f in findings]
    # but an actual unsafe instruction still trips
    ch.blocks[3].entries[0].steps = [
        StepItem(text="Thaw the turkey on the counter overnight before roasting.")]
    _, findings = audit_guide_chapters([ch], _cookbook_bible())
    assert any(f.severity == "block" for f in findings)


def test_audit_flags_glyph_and_truncation():
    ch = _cookbook_chapter()
    ch.blocks[3].entries[0].note = "saves about $1O a month"
    ch.blocks[5].entries[0].variation = "a cut-off variation that ends…"
    issues, _ = audit_guide_chapters([ch], _cookbook_bible())
    assert any("letter O" in i for i in issues)
    assert any("truncated" in i for i in issues)


def test_audit_flags_duplicates_and_danger():
    ch = _cookbook_chapter()
    ch.blocks[5].entries[0].title = "Classic Tray Turkey Supper"  # dup title
    ch.blocks[5].entries[0].steps = [
        StepItem(text="Serve the chicken rare for extra flavor.")]
    issues, findings = audit_guide_chapters([ch], _cookbook_bible())
    assert any("duplicate entry title" in i for i in issues)
    assert any(f.severity == "block" for f in findings)


def test_audit_flags_missing_meta_and_step_bounds():
    ch = _cookbook_chapter()
    ch.blocks[3].entries[0].serves = ""
    ch.blocks[3].entries[0].steps = ch.blocks[3].entries[0].steps[:2]
    issues, _ = audit_guide_chapters([ch], _cookbook_bible())
    assert any("serves/time" in i for i in issues)
    assert any("steps (schema wants" in i for i in issues)


# --------------------------------------------------------------------------- #
# 6. Zero hardcoded keywords
# --------------------------------------------------------------------------- #
def test_keyword_tables_are_gone():
    assert not hasattr(theme, "_KEYWORDS")
    assert not hasattr(guide_images, "_FOOD_HINTS")
    assert not hasattr(content_guide, "_PRODUCT_LINES")
    assert not hasattr(bible_stage, "infer_guide_theme")


def test_theme_pick_never_keyword_matches():
    # a topic loaded with old trigger words changes nothing without a name
    assert (theme.pick("hydrogen peroxide rice british pest cleaning")
            == theme.PRESETS[theme.NEUTRAL_GUIDE_PRESET])
    assert theme.pick("anything", name="heritage_cookbook") \
        == theme.PRESETS["heritage_cookbook"]


def test_prompts_carry_no_legacy_vocabulary():
    profile = _cookbook_profile()
    rendered = "\n".join([
        bible_stage._GUIDE_SYSTEM_TPL.format(
            genre=profile.genre, audience=profile.audience,
            angle=profile.angle, persona=profile.persona),
        bible_stage._PROFILE_PROMPT,
        content_guide._card_spec(profile),
        content_guide._storyboard_spec(profile),
        content_guide._source_policy(profile),
        content_guide._SECTION_PROMPT,
        content_guide._CHART_PROMPT,
        content_guide._CHECK_PROMPT,
        content_guide._PRO_PROMPT,
    ])
    assert not _vocab_hits(rendered), _vocab_hits(rendered)


def test_source_has_no_legacy_vocab_outside_sanctioned_modules():
    from pathlib import Path

    root = Path(bible_stage.__file__).resolve().parents[1]
    sanctioned = {root / "safety.py", root / "schemas.py"}
    hits = []
    for sub in ("pipeline", "layout"):
        for f in (root / sub).glob("*.py"):
            if f in sanctioned:
                continue
            for pat in _vocab_hits(f.read_text()):
                hits.append(f"{f.name}: {pat}")
    assert not hits, hits


def test_templates_menu_has_no_domain_words():
    assert not _vocab_hits(templates.menu_text())
    assert not _vocab_hits(theme.menu_text())


# --------------------------------------------------------------------------- #
# 7. Layout budget caps
# --------------------------------------------------------------------------- #
def test_recipe_caps_keep_quantified_ingredients_intact():
    b = _cookbook_bible()
    ch = _cookbook_chapter()
    long_item = "1 (10.75 ounce) can condensed cream of mushroom soup"
    ch.blocks[3].entries[0].what_you_need[0] = long_item
    layout_budget.trim_chapter(ch, b)
    assert ch.blocks[3].entries[0].what_you_need[0] == long_item


def test_legacy_caps_unchanged():
    ch = _legacy_chapter()
    ch.blocks[1].entries[0].what_you_need[0] = "x" * 60
    layout_budget.trim_chapter(ch)  # no bible → legacy caps
    assert ch.blocks[1].entries[0].what_you_need[0].endswith("…")
    assert len(ch.blocks[1].entries[0].what_you_need[0]) <= 40
