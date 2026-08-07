"""target_pages is a FLOOR — a book must never ship under it silently.

Covers the four mechanisms that make that true:
  1. the estimator now predicts what layout/guide.py really emits
  2. deterministic layout padding closes any remaining gap for free
  3. the pre-flight gate promises exactly what padding can deliver
  4. an "expand" pass may never shrink the book
"""
from __future__ import annotations

import pytest

from bookforge.pipeline import page_pad
from bookforge.pipeline.page_limits import estimate_rendered_pages
from bookforge.schemas import (
    ArtStyle,
    BlockType,
    BookBible,
    Chapter,
    ContentBlock,
    EditorialProfile,
    EntrySchema,
    SectionPlan,
    StepItem,
    UseEntry,
    VoiceStyle,
)


def _profile(**kw) -> EditorialProfile:
    base = dict(
        genre="cookbook",
        audience="seniors",
        angle="nostalgic",
        entry_schema=EntrySchema(entry_noun="recipe"),
        structure_template="essay_entries",
        pages_per_entry=2.0,
    )
    base.update(kw)
    return EditorialProfile(**base)


def _bible(**kw) -> BookBible:
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
    defaults.update(kw)
    return BookBible(**defaults)


def _short_entry(n: int) -> UseEntry:
    """An entry small enough that guide.py renders it on ONE page."""
    return UseEntry(
        number=n,
        title=f"Recipe {n}",
        intro="A short headnote.",
        what_you_need=["flour", "water"],
        steps=[StepItem(text="Mix.")],
    )


def _long_entry(n: int) -> UseEntry:
    """An entry the spread heuristic already sends to TWO pages."""
    return UseEntry(
        number=n,
        title=f"Recipe {n}",
        intro="x" * 400,
        what_you_need=[f"ingredient {i}" for i in range(14)],
        steps=[StepItem(text="y" * 120) for _ in range(10)],
    )


def _chapters(n_chapters: int, per_chapter: int, entry=_short_entry) -> list[Chapter]:
    out = []
    num = 0
    for ci in range(1, n_chapters + 1):
        blocks = []
        for _ in range(per_chapter):
            num += 1
            blocks.append(
                ContentBlock(type=BlockType.use_card, entries=[entry(num)]),
            )
        out.append(Chapter(index=ci, title=f"Chapter {ci}", blocks=blocks))
    return out


# --------------------------------------------------------------------------- #
# 1. estimator matches the emitter
# --------------------------------------------------------------------------- #


def test_page_budget_no_longer_inflates_the_estimate():
    """page_budget never adds pages in guide.py, so it must not add them here.

    The old budget_depth term invented ~0.25 pages per budget point per
    section — up to ~16 phantom pages on a normal book, which is what made the
    planner trim content that was never too long.
    """
    profile = _profile()
    lean = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=3, page_budget=2)
        for i in range(1, 9)
    ]
    fat = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=3, page_budget=20)
        for i in range(1, 9)
    ]
    assert estimate_rendered_pages(lean, profile) == estimate_rendered_pages(
        fat, profile,
    )


def test_estimate_is_in_the_right_ballpark_for_a_real_shape():
    """8 sections x 3 cards renders ~68 pages; the estimate must be close.

    Before calibration this shape estimated 113 — a 2x over-estimate that let
    readiness approve books the renderer could never fill. The upper bound
    was later widened (67->79ish) to account for essay_entries' own "1-2
    feature boxes between entries" rhythm, which renders as genuinely
    separate pages (layout/guide.py's feature_page()) that earlier
    estimates — including this test's own reference math below — didn't
    count at all (see PLAN.md Part 5, found via a real production overshoot).
    """
    profile = _profile()
    sections = [
        SectionPlan(index=i, title=f"S{i}", full_card_quota=3, page_budget=10)
        for i in range(1, 9)
    ]
    # 5 front + 8 dividers + 8 openers + 24 cards + 1 pro = 46 single-page
    # cards, up to ~70 if every card spreads, plus ~1-1.5 feature-box pages
    # per section (essay_entries' own rhythm) not counted above.
    est = estimate_rendered_pages(sections, profile)
    assert 45 <= est <= 90, est


# --------------------------------------------------------------------------- #
# 2. the pad ladder
# --------------------------------------------------------------------------- #


def test_pad_capacity_counts_one_page_per_entry():
    profile = _profile()
    chapters = _chapters(4, 5)  # 20 short entries
    assert page_pad.layout_pad_capacity(chapters, profile) >= 20


def test_pad_plan_covers_the_deficit_and_stops_there():
    """Padding is incremental — it buys the deficit, not every page it could."""
    profile = _profile()
    chapters = _chapters(4, 10)  # 40 entries of capacity
    plan = page_pad.plan_pad(chapters, profile, deficit=7)
    projected = sum(
        lv.gain
        for lv in page_pad._levers(chapters, profile, {})
        if lv.key in plan
    )
    assert projected >= 7
    assert len(plan) < 40, "should not pad every entry to cover a gap of 7"


def test_pad_does_not_double_count_already_spread_entries():
    """A long entry is already 2 pages; level 1 would gain it nothing."""
    profile = _profile()
    short = page_pad.layout_pad_capacity(_chapters(2, 5, _short_entry), profile)
    long_ = page_pad.layout_pad_capacity(_chapters(2, 5, _long_entry), profile)
    # Both still report capacity (long entries escalate to flow instead), but
    # the short book must never report LESS than the long one.
    assert short >= long_


def test_pad_respects_repairs_already_in_force():
    """Levels the layout audit demanded are not re-sold as free capacity."""
    profile = _profile()
    chapters = _chapters(2, 5)
    full = page_pad.layout_pad_capacity(chapters, profile)
    prev = {f"{ci}.{bi}": 2 for ci in (1, 2) for bi in range(5)}
    assert page_pad.layout_pad_capacity(chapters, profile, prev) < full


def test_pad_plan_is_empty_when_nothing_is_needed():
    profile = _profile()
    assert page_pad.plan_pad(_chapters(2, 3), profile, deficit=0) == {}


def test_pad_handles_structured_blocks():
    """Regression: checklist is a ChecklistModel, not a list.

    Found by replaying a real book's render_manifest — `len(b.checklist)`
    raised TypeError on every book carrying a checklist block.
    """
    from bookforge.schemas import ChecklistItem, ChecklistModel, TableModel

    profile = _profile()
    chapters = [Chapter(index=1, title="C1", blocks=[
        ContentBlock(type=BlockType.use_card, entries=[_short_entry(1)]),
        ContentBlock(
            type=BlockType.checklist,
            checklist=ChecklistModel(
                title="Prep",
                items=[ChecklistItem(text=f"step {i}") for i in range(6)],
            ),
        ),
        ContentBlock(
            type=BlockType.chart,
            table=TableModel(columns=["a", "b"], rows=[["1", "2"]]),
        ),
        ContentBlock(
            type=BlockType.use_list,
            entries=[_short_entry(n) for n in range(2, 12)],
        ),
    ])]
    capacity = page_pad.layout_pad_capacity(chapters, profile)
    assert capacity > 0
    plan = page_pad.plan_pad(chapters, profile, deficit=3)
    assert plan


def test_plan_capacity_is_conservative_vs_real_capacity():
    """The gate must never promise more than the ladder can deliver."""
    profile = _profile()
    chapters = _chapters(5, 6)  # 30 entries
    assert page_pad.pad_capacity_for_plan(30) <= page_pad.layout_pad_capacity(
        chapters, profile,
    )


# --------------------------------------------------------------------------- #
# 3. pre-flight gate
# --------------------------------------------------------------------------- #


def test_gate_blocks_a_target_the_outline_cannot_reach():
    """The Oma failure mode: a thin outline against a big target.

    Must be refused BEFORE generation with a usable suggestion, rather than
    approved and then hard-failed an hour later on PageLimitError.
    """
    from bookforge.pipeline.feasibility import build_book_assessment

    # A LOCKED 2-chapter TOC pins the section count, so the plan tops out at
    # 2 x MAX_CARDS_PER_SECTION entries. Even spreading every one of those over
    # two pages cannot approach 300.
    assessment = build_book_assessment(
        topic="A Tiny Guide",
        text="\n".join(f"- item {i}" for i in range(4)),
        toc=["Chapter One", "Chapter Two"],
        target_pages=300,
    )
    assert assessment.can_create is False
    assert assessment.status == "infeasible"
    suggestion = next(
        (o for o in assessment.options if o.id == "lower_pages"), None,
    )
    assert suggestion is not None
    # The old code suggested 2*lo - max_est here, which was ABOVE the target it
    # had just rejected. A suggestion must be reachable.
    assert suggestion.target_pages < 300


def test_gate_allows_what_padding_can_actually_reach():
    from bookforge.pipeline.feasibility import build_book_assessment

    assessment = build_book_assessment(
        topic="60 Forgotten German Recipes",
        description="60 cheap family dinners",
        target_pages=100,
    )
    assert assessment.can_create is True


# --------------------------------------------------------------------------- #
# 4. expansion may never shrink the book
# --------------------------------------------------------------------------- #


def test_total_words_counts_entry_bodies():
    from bookforge.pipeline.orchestrator import _total_words

    few = _chapters(1, 1, _short_entry)
    many = _chapters(1, 1, _long_entry)
    assert _total_words(many) > _total_words(few) > 0


def test_shrinking_expansion_is_discarded(monkeypatch):
    """A fallback model that writes tersely must not lose us pages.

    Observed in prod on b09a09: opus timed out -> sonnet -> haiku, and the
    'expand' pass took the book from 49 pages to 47.
    """
    from bookforge.pipeline import orchestrator
    from bookforge.schemas import BookArtifact, QAResult

    qa = QAResult(groundedness=1.0, passed=True)
    rich = _chapters(2, 4, _long_entry)
    terse = _chapters(2, 4, _short_entry)
    assert orchestrator._total_words(terse) < orchestrator._total_words(rich)

    seen: dict = {}

    def _fake_assemble(book_id, bible, chapters, *a, **k):
        seen["chapters"] = chapters
        return BookArtifact(
            book_id="b1", interior_pdf_key="k", page_count=40,
            file_size_mb=1.0, qa=qa,
        )

    monkeypatch.setattr(orchestrator, "_assemble_with_layout_revise", _fake_assemble)
    monkeypatch.setattr(orchestrator, "_final_vision_qa",
                        lambda book_id, artifact, *a, **k: artifact)
    monkeypatch.setattr(orchestrator.settings, "max_page_scale_revisions", 1)
    monkeypatch.setattr(orchestrator.settings, "ship_on_page_undershoot", True)
    monkeypatch.setattr(orchestrator.content, "regenerate_guide_sections",
                        lambda *a, **k: terse)
    monkeypatch.setattr(orchestrator.content_guide, "sync_card_image_briefs",
                        lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "finalize_marketing_counts",
                        lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.checkpoints, "save_bible", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.checkpoints, "save_chapters", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_refresh_images",
                        lambda book_id, b, c, images, cover: (images, cover))

    orchestrator._assemble_with_page_scale(
        "b1", _bible(target_pages=100, sections=[]), rich, [], qa,
        cover_key=None, pro=None, source=None, stage=lambda s: None,
    )
    # The terser regeneration must have been thrown away.
    assert orchestrator._total_words(seen["chapters"]) == \
        orchestrator._total_words(rich)


# --------------------------------------------------------------------------- #
# 5. last-resort ship policy
# --------------------------------------------------------------------------- #


def _fake_short_scale(monkeypatch):
    from bookforge.pipeline import orchestrator
    from bookforge.schemas import BookArtifact, QAResult

    qa = QAResult(groundedness=1.0, passed=True)
    monkeypatch.setattr(
        orchestrator, "_assemble_with_layout_revise",
        lambda *a, **k: BookArtifact(
            book_id="b1", interior_pdf_key="k", page_count=80,
            file_size_mb=1.0, qa=qa,
        ),
    )
    monkeypatch.setattr(orchestrator.settings, "max_page_scale_revisions", 0)
    monkeypatch.setattr(orchestrator, "_final_vision_qa",
                        lambda book_id, artifact, *a, **k: artifact)
    return orchestrator, qa


def test_undershoot_ships_degraded_by_default(monkeypatch):
    orchestrator, qa = _fake_short_scale(monkeypatch)
    art = orchestrator._assemble_with_page_scale(
        "b1", _bible(target_pages=100, sections=[]), [], [], qa,
        cover_key=None, pro=None, source=None, stage=lambda s: None,
    )
    assert art.degraded
    assert art.page_count == 80
    assert any("below the 100-page target" in w for w in art.warnings)


def test_narrative_undershoot_ships_without_burning_renders(monkeypatch):
    from bookforge.pipeline import orchestrator
    from bookforge.schemas import BookArtifact, QAResult

    qa = QAResult(groundedness=1.0, passed=True)
    calls = {"n": 0}

    def _fake_assemble(*a, **k):
        calls["n"] += 1
        return BookArtifact(
            book_id="b1", interior_pdf_key="k", page_count=60,
            file_size_mb=1.0, qa=qa,
        )

    monkeypatch.setattr(orchestrator.assemble, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator.settings, "max_page_scale_revisions", 3)
    art = orchestrator._assemble_narrative_with_page_scale(
        "b1", _bible(book_kind="narrative", target_pages=100, chapters=[]),
        [], [], qa,
    )
    assert art.degraded
    # The narrative path can only trim, so re-rendering an undershooting book
    # is byte-identical work. It must bail after the first render.
    assert calls["n"] == 1
