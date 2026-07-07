"""Content-quality + per-topic-design overhaul: anti-vagueness audit, fuzzy
dedup, count coherence (digits + spelled-out), and DesignKit contrast/validation."""
from bookforge.layout import theme
from bookforge.pipeline import content_guardrails as cg
from bookforge.pipeline import qa


# --- per-topic design: contrast validation + dynamic font import ---
def test_contrast_ratio_black_white():
    assert round(theme.contrast_ratio("#000000", "#FFFFFF"), 1) == 21.0
    assert theme.contrast_ratio("nothex", "#fff") == 0.0


def test_validate_kit_rejects_low_contrast_and_fills_gaps():
    muddy = {k: "#808080" for k in theme._REQUIRED_TOKENS}
    muddy.update(display="'X',serif", body="'Y',serif", cond="'Z',sans")
    assert theme.validate_kit(muddy) is None
    good = dict(muddy, paper="#FFFFFF", ink="#111111", panel="#EEEEEE",
                panel_dark="#10303A", accent="#0A5A2A", muted="#555555")
    out = theme.validate_kit(good)
    assert out is not None and out["paper"] == "#FFFFFF"


def test_font_import_only_used_families():
    imp = theme.font_import_for({"display": "'Fraunces', serif",
                                 "body": "'Inter', sans-serif",
                                 "cond": "'Archivo', sans-serif"})
    assert "Fraunces" in imp and "Inter" in imp and "Archivo" in imp
    assert "Playfair" not in imp


def test_font_import_falls_back_when_unknown():
    assert theme.font_import_for({"display": "'Nope', serif"}) == theme.FONT_IMPORT


# --- count coherence: digits, spelled-out, expanded nouns ---
def test_parse_promised_count_digits_and_words():
    assert cg.parse_promised_count("Lose Belly Fat: 14 Habits", "") == 14
    assert cg.parse_promised_count("Fourteen Habits That Work", "") == 14
    assert cg.parse_promised_count("Sixty Rules for a Calmer Life", "") == 60
    assert cg.parse_promised_count("200 Best-Loved Recipes", "") == 200
    assert cg.parse_promised_count("A Book With No Number", "") is None


def test_word_to_int():
    assert cg._word_to_int("sixty") == 60
    assert cg._word_to_int("twenty-one") == 21
    assert cg._word_to_int("banana") is None


def test_propagate_promised_count_rewrites_all_fields():
    from bookforge.schemas import (BookBible, HowToPage, SectionPlan,
                                   EditorialProfile)
    prof = EditorialProfile(cover_kicker="14 Science-Backed Habits")
    bible = BookBible.model_construct(
        title="Lose Belly Fat: 50 Habits", subtitle="Fourteen Habits That Work",
        profile=prof, promised_uses=50,
        how_to=HowToPage(lead="You do not need all fourteen habits at once.",
                         points=["Read the fourteen habits in order."]),
        sections=[SectionPlan(index=2, title="X",
                              part_title="THE FOURTEEN HABITS")],
        list_lead="")
    cg.propagate_promised_count(bible)
    assert "50" in bible.title and "14" not in bible.title
    assert "50" in bible.subtitle and "ourteen" not in bible.subtitle.lower()
    assert "50" in bible.profile.cover_kicker
    assert "50" in bible.how_to.lead and "fourteen" not in bible.how_to.lead
    assert "50" in bible.how_to.points[0]
    assert "50" in bible.sections[0].part_title


def test_find_count_contradictions():
    from bookforge.schemas import (BookBible, Chapter, ContentBlock, BlockType,
                                   UseEntry)
    bible = BookBible.model_construct(
        title="14 Habits", subtitle="", sections=[], promised_uses=14, profile=None)
    ch = Chapter(index=2, title="X", blocks=[ContentBlock(
        type=BlockType.section_intro,
        text="This book gives you sixty habits to try.")])
    hits = cg.find_count_contradictions([ch], bible)
    assert any("60" in h and "14" in h for h in hits), hits


# --- anti-vagueness + fuzzy dedup ---
def test_abstract_need_detection():
    assert qa._is_abstract_need("Patience")
    assert qa._is_abstract_need("A plan")
    assert qa._is_abstract_need("good lighting")
    assert not qa._is_abstract_need("A refillable bottle")
    assert not qa._is_abstract_need("2 cups flour")


def test_near_duplicate_titles():
    a = qa._title_tokens("Swap Drinks for Water")
    b = qa._title_tokens("Swap Your Drinks for Water")
    assert qa._near_duplicate(a, b)
    assert not qa._near_duplicate(qa._title_tokens("Eat More Protein"),
                                  qa._title_tokens("Sleep Seven Hours"))


def test_audit_flags_abstract_materials_and_empty_when_no_materials():
    from bookforge.schemas import (BookBible, Chapter, ContentBlock, BlockType,
                                   UseEntry, StepItem, EditorialProfile,
                                   EntrySchema)
    prof = EditorialProfile(entry_schema=EntrySchema(requires_materials=True))
    bible = BookBible.model_construct(sections=[], profile=prof, title="T",
                                      subtitle="", promised_uses=None)
    entry = UseEntry(number=1, title="Drink Water", intro="Hydrate.",
                     what_you_need=["Patience", "A trend view"],
                     steps=[StepItem(text="Sip water through the day.")])
    ch = Chapter(index=1, title="H", blocks=[ContentBlock(
        type=BlockType.use_card, heading="Drink Water", entries=[entry])])
    issues, _ = qa.audit_guide_chapters([ch], bible)
    assert any("abstractions" in i for i in issues), issues
