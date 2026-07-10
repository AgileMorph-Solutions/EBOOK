"""Tests for the deterministic layout gates and self-repair added 2026-07.

Covers: layout_defects formatting, LayoutError classification, vision-QA page
coverage, the repairs plumbing in the guide builder, and the measure-and-repair
loop in assemble._render_practical (with the renderer monkeypatched — no
browser needed).
"""
from __future__ import annotations

import pytest

from bookforge.errors import GeometryError, LayoutError
from bookforge.layout import render
from bookforge.layout.guide import GuideDirector
from bookforge.pipeline import assemble, layout_qa
from bookforge.schemas import (
    ArtStyle, BookBible, SectionPlan, StepItem, UseEntry, VoiceStyle,
)


def _bible(**kwargs) -> BookBible:
    defaults = dict(
        title="The Cast-Iron Kitchen",
        subtitle="Cook and care for your skillet",
        audience="Home cooks",
        tone="warm",
        book_kind="practical_guide",
        art_style=ArtStyle(
            medium="photo", palette="warm", lighting="daylight", era="now",
            composition_rules="", negative_prompt="no text",
            style_suffix="clean photo",
        ),
        voice=VoiceStyle(
            narrator_persona="neighbor", tone_register="plain", pov="you",
            pacing="short", vocabulary="simple",
        ),
        sections=[
            SectionPlan(index=1, title="Seasoning", full_card_quota=4, list_quota=8),
        ],
    )
    defaults.update(kwargs)
    return BookBible(**defaults)


def _entry(**kwargs) -> UseEntry:
    defaults = dict(
        number=1, title="The Oven Season",
        intro="A thin coat of oil, a hot oven, and an hour of patience.",
        what_you_need=["1 tsp grapeseed oil", "paper towels"],
        steps=[StepItem(text="Wash and dry the pan."),
               StepItem(text="Wipe on oil, then wipe it all off."),
               StepItem(text="Bake upside down at 475F for an hour.")],
    )
    defaults.update(kwargs)
    return UseEntry(**defaults)


# --------------------------------------------------------------------------- #
# layout_defects report formatting
# --------------------------------------------------------------------------- #

def test_layout_defects_empty_and_none():
    assert render.layout_defects(None) == []
    assert render.layout_defects({"stuck": [], "overflows": []}) == []


def test_layout_defects_formats_stuck_and_overflows():
    report = {
        "stuck": ["divider:Rebuilding the Plate"],
        "overflows": [{"page": 13, "entry": "1.4", "selector": "gbody-box",
                       "over_px": 93, "text": "Fridge Keeping"}],
    }
    defects = render.layout_defects(report)
    assert len(defects) == 2
    assert "divider:Rebuilding the Plate" in defects[0]
    assert "page 13" in defects[1] and "gbody-box" in defects[1]


# --------------------------------------------------------------------------- #
# LayoutError classification (API handles it exactly like GeometryError)
# --------------------------------------------------------------------------- #

def test_layout_error_is_geometry_error():
    assert issubclass(LayoutError, GeometryError)


def test_progress_normalizes_layout_revise_stage():
    from bookforge.api.main import _normalize_stage

    assert _normalize_stage("layout-revise (1)") == "layout"
    assert _normalize_stage("revise (2)") == "qa"
    assert _normalize_stage("images") == "images"


def test_api_classifies_layout_error_permanent():
    from bookforge.api.main import _failure_class, _friendly_error

    err = LayoutError("layout audit failed")
    assert _failure_class(err) == "permanent"
    msg = _friendly_error(err)
    assert "layout" in msg.lower()
    assert "layout audit failed" not in msg  # never leak internals


# --------------------------------------------------------------------------- #
# Vision QA page coverage
# --------------------------------------------------------------------------- #

def test_must_check_pages_finds_high_risk_templates():
    pages = ["page gcover", "page gtitle", "page howto", "page gsafety",
             "page toc", "page gdivider has-img", "page", "page gdivider"]
    assert layout_qa._must_check_pages(pages) == {1, 2, 4, 6, 8}


def test_pick_pages_always_includes_must_pages():
    # dividers deep in a long book, far beyond the front-matter window
    picks = layout_qa._pick_pages(200, 8, sample=5, must={57, 121, 199})
    assert {57, 121, 199} <= set(picks)


# --------------------------------------------------------------------------- #
# Repairs plumbing in the guide builder
# --------------------------------------------------------------------------- #

def test_entry_card_single_page_tagged_with_key():
    d = GuideDirector(_bible())
    d.entry_card("Seasoning", _entry(), None, key="1.2")
    assert len(d.pages) == 1
    assert 'data-entry="1.2"' in d.pages[0]


def test_entry_card_force_spread_makes_two_pages():
    d = GuideDirector(_bible())
    d.entry_card("Seasoning", _entry(), None, key="1.2", force_spread=True)
    assert len(d.pages) == 2
    assert "(continued)" not in d.pages[1]
    assert "entry-continued" in d.pages[1]
    assert 'class="entry-cont"' not in d.pages[1]
    assert "entry-continued" not in d.pages[0]


def test_spread_page_b_no_entry_cont():
    d = GuideDirector(_bible())
    d.entry_card("Seasoning", _entry(), None, key="1.2", force_spread=True)
    assert 'class="entry-cont"' not in d.pages[1]
    assert "ec-tag" not in d.pages[1]


def test_spread_continued_page_is_top_aligned():
    d = GuideDirector(_bible())
    d.entry_card("Seasoning", _entry(), None, key="1.2", force_spread=True)
    assert "entry-fit entry-continued" in d.pages[1]


def test_entry_flow_continued_has_entry_continued_class():
    long_steps = [
        StepItem(text=" ".join(["patience"] * 55)) for _ in range(10)
    ]
    entry = _entry(steps=long_steps)
    d = GuideDirector(_bible())
    d.entry_card("Seasoning", entry, None, key="1.2", level=2)
    cont_pages = [p for p in d.pages if "entry-continued" in p]
    assert cont_pages
    assert all("(continued)" not in p for p in cont_pages)
    assert all('class="entry-cont"' not in p for p in cont_pages)


def test_howto_continued_uses_fill_stack_not_spread():
    d = GuideDirector(_bible())
    d.how_to_use("How to", "lead", ["a", "b", "c", "d"], level=2)
    assert len(d.pages) == 2
    assert "howto-continued" in d.pages[1]
    assert "(continued)" not in d.pages[1]
    assert "fill-stack" in d.pages[1]
    assert "fill-spread" not in d.pages[1]
    assert "fill-spread" in d.pages[0]


def test_chart_page_always_fill_start():
    from bookforge.schemas import TableModel

    table = TableModel(columns=["A", "B"],
                       rows=[[f"row {i}", "val"] for i in range(10)])
    d = GuideDirector(_bible())
    d.chart_page("Ref", "Chart", "lead", table, key="chart:1", level=0)
    assert "fill-start" in d.pages[0]
    assert "fill-center" not in d.pages[0]
    d2 = GuideDirector(_bible())
    d2.chart_page("Ref", "Big", "lead", table, key="chart:2", level=3)
    for pg in d2.pages:
        assert "fill-start" in pg
        assert "fill-center" not in pg


def test_entry_flow_level3_sheds_intro_from_presentation_page():
    d2 = GuideDirector(_bible())
    d2.entry_card("Seasoning", _entry(), None, key="1.2", level=2)
    assert "entry-intro" in d2.pages[0]  # level 2: intro stays on page A
    d3 = GuideDirector(_bible())
    d3.entry_card("Seasoning", _entry(), None, key="1.2", level=3)
    assert "entry-intro" not in d3.pages[0]  # level 3: intro flows
    assert any("entry-intro" in pg for pg in d3.pages[1:])
    assert all('data-entry="1.2"' in pg for pg in d3.pages)


def test_entry_flow_continues_step_numbering_across_pages():
    """Continuation pages must use CSS counter-reset, not HTML start= (ignored)."""
    long_steps = [
        StepItem(text=" ".join(["patience"] * 55)) for _ in range(10)
    ]
    entry = _entry(steps=long_steps)
    d = GuideDirector(_bible())
    d.entry_card("Seasoning", entry, None, key="1.2", level=2)
    assert len(d.pages) >= 2
    step_pages = [p for p in d.pages if "steps-list" in p]
    assert len(step_pages) >= 2
    assert any('counter-reset: s' in p and 'counter-reset: s 0' not in p
               or __import__("re").search(r"counter-reset: s [1-9]", p)
               for p in step_pages[1:])
    assert ' start="' not in "".join(step_pages[1:])


def test_essay_continuation_no_repeated_title():
    text = " ".join(["Cast iron rewards patience and punishes neglect."] * 90)
    d = GuideDirector(_bible())
    d.essay_page("Seasoning", "Why It Works", text, key="3")
    assert len(d.pages) >= 2
    assert "gh2" in d.pages[0]
    assert "gh3" not in d.pages[1]
    assert "essaypage continued" in d.pages[1]
    assert "fill-start" in d.pages[1]
    assert "(continued)" not in d.pages[1]


def test_essay_pull_quote_on_own_page_not_inline():
    text = " ".join(["Cast iron rewards patience and punishes neglect."] * 90)
    quote = "You brought the dish full and came home with it empty."
    d = GuideDirector(_bible())
    d.essay_page("Seasoning", "Why It Works", text, pull_quote=quote, key="3")
    essay_pages = [p for p in d.pages if 'data-entry="essay:3"' in p]
    assert len(essay_pages) >= 2
    assert all("<blockquote>" not in p or "essay-pull" in p for p in essay_pages[:-1])
    assert "essay-pull" in essay_pages[-1]
    assert quote in essay_pages[-1]
    assert essay_pages[-1].count("<blockquote>") == 1


def test_essay_page_tagged_and_squeeze_paginates_more():
    text = " ".join(["Cast iron rewards patience and punishes neglect."] * 90)
    d1 = GuideDirector(_bible())
    d1.essay_page("Seasoning", "Why It Works", text, key="3")
    assert 'data-entry="essay:3"' in d1.pages[0]
    d2 = GuideDirector(_bible())
    d2.essay_page("Seasoning", "Why It Works", text, key="3", squeeze=True)
    assert len(d2.pages) > len(d1.pages)


# --------------------------------------------------------------------------- #
# assemble._render_practical: measure -> repair -> re-render -> gate
# --------------------------------------------------------------------------- #

class _FakeRender:
    """Substitute for layout.render inside assemble: each html_to_pdf call pops
    the next scripted audit report."""

    def __init__(self, reports):
        self.reports = list(reports)
        self.last_fit_report = None

    def html_to_pdf(self, html):
        self.last_fit_report = self.reports.pop(0)
        return b"%PDF-fake"

    def layout_defects(self, report):
        return render.layout_defects(report)


def _run_render_practical(monkeypatch, reports):
    fake = _FakeRender(reports)
    seen_repairs = []

    def fake_build(bible, chapters, images, **kw):
        seen_repairs.append(kw.get("repairs"))
        return "<html/>"

    monkeypatch.setattr(assemble, "render", fake)
    monkeypatch.setattr(assemble.guide, "build_practical_html", fake_build)
    result = assemble._render_practical(
        _bible(), [], [], cover_key=None, how_to=None, safety_data=None, pro=None)
    return result, seen_repairs


def test_render_practical_clean_first_pass(monkeypatch):
    (html, pdf), repairs = _run_render_practical(
        monkeypatch, [{"stuck": [], "overflows": [], "pages": []}])
    assert pdf.startswith(b"%PDF") and repairs == [None]


def _over(key, content_h, box_h, page=9):
    return {"page": page, "entry": key, "selector": "gfit",
            "over_px": content_h - box_h, "content_h": content_h,
            "box_h": box_h, "text": "x"}


def test_render_practical_one_shot_measured_repair(monkeypatch):
    # ratio 1.5 -> flow (level 2) planned DIRECTLY from the measurement
    overflowing = {"stuck": [], "pages": [], "overflows": [_over("1.4", 1500, 1000)]}
    clean = {"stuck": [], "overflows": [], "pages": []}
    (html, pdf), repairs = _run_render_practical(monkeypatch, [overflowing, clean])
    assert repairs == [None, {"1.4": 2}]  # one repair pass, no escalation loop


def test_render_practical_corrective_round_makes_progress(monkeypatch):
    over = {"stuck": [], "pages": [], "overflows": [_over("1.4", 1100, 1000)]}
    clean = {"stuck": [], "overflows": [], "pages": []}
    # ratio 1.1 -> spread; still overflowing after rebuild -> bumped past it
    (html, pdf), repairs = _run_render_practical(monkeypatch, [over, over, clean])
    assert repairs == [None, {"1.4": 1}, {"1.4": 2}]


def test_plan_repairs_computes_exact_chart_split_and_rescue():
    plan = assemble._plan_repairs(
        [_over("chart:2.11", 3000, 1000), _over("feature:1.2", 1500, 1000)], {})
    assert plan["chart:2.11"] == 5  # ceil(3.0/0.9)+1 pages, one shot
    assert plan["feature:1.2"] == 4  # no structural reflow -> rescue floor


def test_render_practical_raises_when_unrepairable(monkeypatch):
    # overflow with no entry key (e.g. a chart page): nothing to repair -> hard fail
    bad = {"stuck": ["divider:Unfixable"], "overflows": [], "pages": []}
    with pytest.raises(LayoutError):
        _run_render_practical(monkeypatch, [bad])


def test_render_practical_no_repair_loop_forever(monkeypatch):
    # same entry keeps overflowing through every rung incl. rescue -> bounded fail
    always = {"stuck": [], "pages": [], "overflows": [_over("1.4", 3000, 1000)]}
    with pytest.raises(LayoutError) as exc_info:
        _run_render_practical(monkeypatch, [always] * 7)
    # structured defects ride on the error for the orchestrator's revise loop
    assert exc_info.value.defects and exc_info.value.defects[0]["entry"] == "1.4"


def test_chart_page_splits_rows_across_pages():
    from bookforge.schemas import TableModel

    table = TableModel(columns=["Place", "Best season"],
                       rows=[[f"Place {i}", "Spring"] for i in range(30)],
                       note="Seasons vary.")
    d = GuideDirector(_bible())
    d.chart_page("Reference", "When to Go", "lead text", table,
                 key="chart:2.11", level=3)
    assert len(d.pages) == 3
    assert all('data-entry="chart:2.11"' in pg for pg in d.pages)
    assert "(1 of 3)" in d.pages[0] and "(3 of 3)" in d.pages[2]
    # header repeats on every page; lead only on first, note only on last
    assert all("<thead>" in pg for pg in d.pages)
    assert "lead text" in d.pages[0] and "lead text" not in d.pages[1]
    assert "Seasons vary." in d.pages[2] and "Seasons vary." not in d.pages[0]
    # rows balanced 10/10/10
    assert all(pg.count("<tr>") == 11 for pg in d.pages)  # 10 body + 1 head


def test_fitmin_rescue_floor():
    assert GuideDirector._fitmin(4) == ' data-fitmin="9"'
    assert GuideDirector._fitmin(2) == ' data-fitmin="11"'
    d = GuideDirector(_bible())
    d.how_to_use("How to", "lead", ["a", "b", "c", "d"], level=4)
    assert 'data-fitmin="9"' in d.pages[0]


def test_layout_faults_maps_defects_to_chapters():
    from bookforge.pipeline.orchestrator import _layout_faults

    defects = [
        {"page": 9, "entry": "1.4", "text": "Butter-Basted Pan Steak"},
        {"page": 30, "entry": "essay:2", "text": "Why It Works"},
        {"page": 3, "entry": "howto", "text": "Welcome"},  # no chapter -> skipped
    ]
    faults = _layout_faults(defects, [])
    assert set(faults) == {1, 2}
    assert "Butter-Basted" in faults[1][0]
    assert "essay" in faults[2][0]
