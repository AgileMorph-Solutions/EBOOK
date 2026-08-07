"""Deterministic page padding — reach the page floor without writing content.

`target_pages` is a FLOOR, not a ceiling: a book asked for 100 pages may ship at
102, but must never ship at 47. Content expansion (orchestrator's page-expand
branch) is tried first because real prose is always better than spread-out
prose; when it runs out — or when the model refuses to grow the text — this
module closes the remaining gap at ZERO API cost.

It works by driving the SAME `repairs: dict[str, int]` channel the layout
self-repair ladder already uses (layout/guide.py reads it via `lvl(key, 0)`),
so no new rendering code exists. Each key/level pair below is a documented,
measured page delta in the emitter:

    essay:{ch}      1 -> words/page 330*0.7   2 -> 330*0.55   (guide.py:286-290)
    {ch}.{bi}       1 -> _entry_spread, 1 page becomes 2      (guide.py:439)
                    2 -> _entry_flow, packed over N pages     (guide.py:437)
    chart:{ref}     N -> npages = min(N, 6), level IS the count (guide.py:651)
    check:{ref}     2 -> split in half, needs > 4 pairs       (guide.py:710)
    list:{ref}      2 -> 11/page   3 -> 9/page (from 14)      (guide.py:902)

Padding is INCREMENTAL and stops at the first render that clears the floor, so
a padded book lands just above target rather than at the ceiling.
"""
from __future__ import annotations

import math

import structlog

from bookforge.layout.guide import entry_needs_spread
from bookforge.schemas import BlockType, Chapter

log = structlog.get_logger(__name__)

# Mirrors guide.GuideDirector._ESSAY_WORDS_FIRST and the squeeze factors.
_ESSAY_WORDS_FIRST = 330
_ESSAY_SQUEEZE = {1: 0.7, 2: 0.55}
# Mirrors the list re-chunk table in guide.py:902.
_LIST_PER_PAGE = {0: 14, 1: 14, 2: 11, 3: 9}
_CHART_MAX_LEVEL = 6


def _essay_pages(words: int, level: int) -> int:
    per = _ESSAY_WORDS_FIRST
    if level >= 2:
        per = int(per * _ESSAY_SQUEEZE[2])
    elif level == 1:
        per = int(per * _ESSAY_SQUEEZE[1])
    return max(1, math.ceil(words / per)) if words else 0


def _list_pages(n: int, level: int) -> int:
    per = _LIST_PER_PAGE.get(level, 9)
    return max(1, math.ceil(n / per))


class _Lever:
    """One padding move: set `key` to `level` and gain `gain` pages."""

    __slots__ = ("key", "level", "gain", "rank")

    def __init__(self, key: str, level: int, gain: int, rank: int) -> None:
        self.key = key
        self.level = level
        self.gain = gain
        self.rank = rank


def _levers(chapters: list[Chapter], profile, prev: dict[str, int]) -> list[_Lever]:
    """Every unused padding move available on this book, cheapest first.

    `rank` orders by visual cost: squeezing an essay is invisible, spreading an
    entry across two pages is normal cookbook styling, splitting a chart or
    re-chunking a list is a layout change readers notice, and flowing an entry
    is the most disruptive. `prev` is the repair state already in force, so a
    level the layout audit genuinely needed is never counted as free capacity.
    """
    out: list[_Lever] = []
    for ch in chapters:
        # --- essays: tighter words-per-page ---
        for b in ch.blocks:
            if b.type is BlockType.history and b.text:
                key = f"essay:{ch.index}"
                cur = prev.get(key, 0)
                words = len(b.text.split())
                base = _essay_pages(words, cur)
                for lvl in (1, 2):
                    if lvl <= cur:
                        continue
                    gain = _essay_pages(words, lvl) - base
                    if gain > 0:
                        out.append(_Lever(key, lvl, gain, rank=0))
                        break
                break

        for bi, b in enumerate(ch.blocks):
            ref = f"{ch.index}.{bi}"
            if b.type is BlockType.use_card and b.entries:
                cur = prev.get(ref, 0)
                # Level 1 only buys a page for entries the emitter would
                # otherwise render on ONE page; already-spread entries gain 0.
                if cur < 1 and not entry_needs_spread(b.entries[0], profile):
                    out.append(_Lever(ref, 1, 1, rank=1))
                elif cur < 2:
                    # Flow packs the body over as many pages as it needs; one
                    # extra page is the conservative floor for that move.
                    out.append(_Lever(ref, 2, 1, rank=3))
            elif b.type is BlockType.chart and b.table:
                key = f"chart:{ref}"
                cur = max(1, prev.get(key, 0))
                if cur < _CHART_MAX_LEVEL:
                    out.append(
                        _Lever(key, cur + 1, 1, rank=2),
                    )
            elif b.type is BlockType.checklist and b.checklist:
                key = f"check:{ref}"
                # guide.py:710 splits on len(pairs) > 4, where pairs comes from
                # checklist.items — a short checklist gains nothing from level 2.
                items = len(b.checklist.items)
                if prev.get(key, 0) < 2 and items > 4:
                    out.append(_Lever(key, 2, 1, rank=2))
            elif b.type is BlockType.use_list and b.entries and len(b.entries) >= 4:
                key = f"list:{ref}"
                cur = prev.get(key, 0)
                base = _list_pages(len(b.entries), cur)
                for lvl in (2, 3):
                    if lvl <= cur:
                        continue
                    gain = _list_pages(len(b.entries), lvl) - base
                    if gain > 0:
                        out.append(_Lever(key, lvl, gain, rank=2))
                        break
    out.sort(key=lambda lv: lv.rank)
    return out


def layout_pad_capacity(
    chapters: list[Chapter],
    profile,
    prev: dict[str, int] | None = None,
) -> int:
    """Extra pages the pad ladder can add to this book without new content.

    Used BOTH by the pre-flight feasibility gate and by the pad loop itself, so
    what readiness promises is exactly what generation can deliver.
    """
    return sum(lv.gain for lv in _levers(chapters, profile, dict(prev or {})))


def pad_capacity_for_plan(num_entries: int) -> int:
    """Pad capacity estimated from PLAN counts, before any chapter text exists.

    Used by the pre-flight feasibility gate, which runs before generation and so
    has no Chapter objects to inspect. Deliberately conservative: it counts only
    the one page per entry that `_levers` always yields — an entry that would
    render on a single page gains that page by becoming a spread (rank 1), and
    an entry already long enough to spread gains at least that much again by
    flowing (rank 3). Essay squeezes, chart splits and list re-chunks are real
    additional capacity but are NOT counted here, so the gate under-promises
    rather than repeating the over-promise this whole change exists to fix.
    """
    return max(0, num_entries)


def plan_pad(
    chapters: list[Chapter],
    profile,
    *,
    deficit: int,
    prev: dict[str, int] | None = None,
) -> dict[str, int]:
    """Cheapest set of repair levels that adds at least `deficit` pages.

    Returns ONLY the new/raised keys; merge into the live repair dict with
    max() so a level the layout audit required is never downgraded.
    """
    if deficit <= 0:
        return {}
    prev = dict(prev or {})
    plan: dict[str, int] = {}
    gained = 0
    for lv in _levers(chapters, profile, prev):
        if gained >= deficit:
            break
        plan[lv.key] = max(plan.get(lv.key, 0), lv.level)
        gained += lv.gain
    if plan:
        log.info(
            "page_pad_planned",
            deficit=deficit,
            projected_gain=gained,
            levers=len(plan),
        )
    return plan
