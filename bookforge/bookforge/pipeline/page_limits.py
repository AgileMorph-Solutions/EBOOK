"""Page-count limits and content scaling for book generation.

Finished PDFs must fall in [target_pages, min(ratio×target, target+absolute, 300)].
Post-layout scaling expands or trims content; jobs fail if still out of band.
"""
from __future__ import annotations

import copy
import math

import structlog

from bookforge.config import get_settings
from bookforge.errors import PageLimitError, PlanInfeasibleError
from bookforge.schemas import BlockType, BookBible, Chapter, GeneratedImage, SectionPlan

log = structlog.get_logger(__name__)

MAX_BOOK_PAGES = 300
MAX_PAGES_OVERSHOOT_RATIO = 1.2
MAX_PAGE_OVERSHOOT_ABSOLUTE = 20
ABSOLUTE_PAGE_CEILING = MAX_BOOK_PAGES

# A chapter costs a divider + an opener essay. Below this many entries it is
# not worth that overhead — used to cap section counts at a structurally sane
# number rather than the (much larger) number that raw page arithmetic allows.
MIN_VIABLE_ENTRIES_PER_SECTION = 3


def _promise_ceiling(promised_ceiling: int | None, *, exact: bool = False) -> int | None:
    """Entry-count ceiling a promised item count imposes — shared by
    `_upsize_sections_to_estimate` (which enforces it) and
    `reconcile_plan_to_page_band` (which needs to know whether an undershoot
    it can't fix is because this ceiling was hit).

    `exact=True` (an unambiguous "create exactly N" / complete numbered-list
    promise, see content_guardrails.parse_exact_promised_count) applies the
    literal count with zero tolerance — the plan must never end up with more
    or fewer than N entries. `exact=False` (the default, a soft marketing
    count like "50 Recipes") keeps the usual 10% padding allowance."""
    if not promised_ceiling or promised_ceiling <= 0:
        return None
    if exact:
        return promised_ceiling
    return math.ceil(promised_ceiling * 1.10)


def hard_page_ceiling(target_pages: int) -> int:
    """Hard maximum pages for the finished PDF — tight band above target."""
    target = max(1, target_pages)
    s = get_settings()
    ratio = getattr(s, "max_page_overshoot_ratio", MAX_PAGES_OVERSHOOT_RATIO)
    absolute = getattr(s, "max_page_overshoot_absolute", MAX_PAGE_OVERSHOOT_ABSOLUTE)
    return min(
        math.ceil(target * ratio),
        target + absolute,
        ABSOLUTE_PAGE_CEILING,
    )


def allowed_page_range(target_pages: int) -> tuple[int, int]:
    """Return (lo, hi) inclusive bounds for an acceptable finished PDF."""
    lo = max(1, target_pages)
    hi = hard_page_ceiling(target_pages)
    return lo, hi


def meets_page_floor(pages: int, target_pages: int) -> bool:
    """True when the PDF meets the minimum page goal."""
    return pages >= max(1, target_pages)


def is_within_page_range(pages: int, target_pages: int) -> bool:
    lo, hi = allowed_page_range(target_pages)
    return lo <= pages <= hi


def is_acceptable_page_count(pages: int, target_pages: int) -> bool:
    """True when PDF page count is within the allowed band above target."""
    lo, hi = allowed_page_range(target_pages)
    return lo <= pages <= hi


def check_page_count(pages: int, target_pages: int) -> None:
    """Raise PageLimitError if `pages` is outside the allowed band."""
    lo, hi = allowed_page_range(target_pages)
    if lo <= pages <= hi:
        return
    if pages < lo:
        raise PageLimitError(
            f"page count {pages} below minimum {lo} (target {target_pages})",
            pages=pages,
            target=target_pages,
            lo=lo,
            hi=hi,
        )
    raise PageLimitError(
        f"page count {pages} exceeds hard maximum {hi} (target {target_pages})",
        pages=pages,
        target=target_pages,
        lo=lo,
        hi=hi,
    )


# --- Estimator calibration -------------------------------------------------
# GROUND TRUTH is layout/guide.py: it appends exactly one string per physical
# page to GuideDirector.pages, and `.page { break-after: page }` makes that 1:1
# with the finished PDF. The fit JS only shrinks type to fit a page — it never
# reflows onto a new one — so nothing about typography changes the count.
# Every constant below is therefore a COUNT OF EMITTERS in guide.py, not a
# tuning knob. If you change guide.py's page emission, change these too.


def _guide_fixed_overhead(pamphlet: bool) -> int:
    """Front matter guide.py always emits: cover + title + how-to + safety + TOC.

    build_practical_html: cover (:794), title_page (:797), how_to_use (:800),
    safety_page (:809), and >=1 reserved TOC slot (:833-835).
    """
    return 2 if pamphlet else 5


def _guide_specials_overhead(pamphlet: bool) -> int:
    """Back matter: pro_page (guide.py:931) — one page, and only when present."""
    return 0 if pamphlet else 1


def _section_shell_pages(profile) -> float:
    """Per-section pages that are NOT entry cards.

    divider() is unconditional (guide.py:857) = 1 page. essay_page() emits
    ceil(words/330) (:284-290) ~= 1 for a normal opener. The pull-quote page
    (:327-336) is suppressed whenever the chapter has sidebars (:869), which
    content_guide almost always produces — so it is worth ~0.2, not a full page.
    """
    from bookforge.pipeline import templates

    spec = templates.spec_for(profile.structure_template)
    if spec.has_opener_essay:
        return 2.2  # divider + opener essay + occasional pull-quote page
    return 1.2  # divider + occasional overflow


def _effective_section_shell(profile) -> float:
    """Shell pages per section — compact density uses shorter openers."""
    from bookforge.pipeline.feasibility import COMPACT_PAGES_PER_ENTRY, _compact_shell_pages

    ppe = getattr(profile, "pages_per_entry", 2.0) or 2.0
    if ppe <= COMPACT_PAGES_PER_ENTRY + 0.01:
        return _compact_shell_pages()
    return _section_shell_pages(profile)


def estimate_guide_pages(
    sections: list,
    *,
    pamphlet: bool = False,
) -> int:
    """Legacy budget-sum estimate (writing hints only — not used for gating)."""
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    return overhead + sum(getattr(s, "page_budget", 0) for s in sections)


def _card_page_factor(profile) -> float:
    if getattr(profile, "image_density", "") == "every_entry":
        return 0.9
    return 0.75


# --- Part 5 recalibration ---------------------------------------------------
# A real production book (60-recipe cookbook, essay_entries, image_density=
# every_entry, require_quantities+show_meta) measured 1.80-2.15 real rendered
# pages/entry against this file's flat `pages_per_entry * _card_page_factor`
# estimate of 0.72-1.17 — a 1.65-2.25x miss — on TWO separate real runs, and
# separately measured actual section-shell pages at 2.2-2.7x the estimated
# shell, because feature-box/sidebar pages (which templates like essay_entries
# explicitly call for in their own rhythm) had NO term in the estimator at
# all. Both gaps are structural, not prose-length-driven (verified directly
# against the real written content, which was well within its word budget in
# both runs) — a fully templated recipe card (mandatory photo + ingredient
# grid + numbered steps + savings/meta callouts) triggers layout/guide.py's
# entry_needs_spread() escalation almost unconditionally, and compounds with
# unpriced feature-box pages, into the ~2-2.5x book-level overshoot that let
# an unfittable book run to completion before this fix (see PLAN.md Part 5).
#
# PROVISIONAL: these constants are fit to two real data points — enough to
# stop shipping/spending on a book this structurally oversized, not enough to
# fully trust long-term. Revisit as orchestrator.py's estimate-vs-actual
# ratio logging accumulates more real runs.
_FULL_CARD_STRUCTURAL_FLOOR = 1.4
_FULL_CARD_VARIABLE_COEFF = 0.75
_FEATURE_BOX_PAGES_STANDARD = 1.5
_FEATURE_BOX_PAGES_COMPACT = 1.0


def _is_fully_templated_card(profile) -> bool:
    """True for profiles whose entry card carries the full mandatory template
    (photo + ingredient/needs grid + numbered steps + savings/meta callouts)
    that almost always triggers the layout engine's spread/flow escalation —
    the structural driver `_card_page_factor` alone can't model."""
    if getattr(profile, "image_density", "") != "every_entry":
        return False
    schema = getattr(profile, "entry_schema", None)
    if schema is None:
        return False
    return bool(getattr(schema, "require_quantities", False)
                or getattr(schema, "show_meta", False))


def _effective_pages_per_entry(profile) -> float:
    """Real per-entry page cost — see the recalibration note above. Falls
    back to the original flat `pages_per_entry * _card_page_factor` for
    profiles that aren't fully-templated cards (those weren't measured to
    have this gap)."""
    ppe = max(0.5, getattr(profile, "pages_per_entry", 1.0) or 1.0)
    flat = ppe * _card_page_factor(profile)
    if not _is_fully_templated_card(profile):
        return flat
    return max(flat, _FULL_CARD_STRUCTURAL_FLOOR + _FULL_CARD_VARIABLE_COEFF * ppe)


def _feature_box_pages_per_section(profile) -> float:
    """Sidebar/feature-box pages per section — previously entirely unpriced.
    Templates whose own rhythm calls for feature boxes (non-empty
    `feature_menu`, e.g. essay_entries: "1-2 feature boxes between entries")
    render them as fully separate pages (layout/guide.py's feature_page()),
    additive to the divider+essay shell. `tips_cards` is the only template
    with an empty feature_menu (minimal-prose uniform cards), so it correctly
    gets zero here."""
    from bookforge.pipeline import templates
    from bookforge.pipeline.feasibility import COMPACT_PAGES_PER_ENTRY

    spec = templates.spec_for(getattr(profile, "structure_template", ""))
    if not spec.feature_menu:
        return 0.0
    ppe = getattr(profile, "pages_per_entry", 2.0) or 2.0
    if ppe <= COMPACT_PAGES_PER_ENTRY + 0.01:
        return _FEATURE_BOX_PAGES_COMPACT
    return _FEATURE_BOX_PAGES_STANDARD


def estimate_rendered_pages(
    sections: list,
    profile,
    *,
    specials: list | None = None,
    pamphlet: bool = False,
) -> int:
    """Template-calibrated pre-render page estimate (single source of truth)."""
    if not sections:
        return _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    shell = _effective_section_shell(profile) + _feature_box_pages_per_section(profile)
    per_entry = _effective_pages_per_entry(profile)
    cards = sum(
        getattr(s, "full_card_quota", 0)
        + getattr(s, "list_quota", 0) * 0.15
        for s in sections
    )
    # chart_page (guide.py:631) and checklist_page (:673) each emit ONE page.
    chart_pages = len(specials or [])
    # NOTE: page_budget deliberately contributes nothing. guide.py never uses it
    # to add pages — its only read is `tight_budget = page_budget <= 2` (:844),
    # which REMOVES the pull-quote page. The old budget_depth term invented up
    # to ~16 phantom pages per book and was the largest single source of the
    # ~2x over-estimate that made the planner trim content it never needed to.
    body = len(sections) * shell + cards * per_entry + chart_pages
    return max(1, int(math.ceil(overhead + body)))


def estimate_preflight_pages(
    *,
    num_sections: int,
    num_entries: int,
    profile,
    pamphlet: bool = False,
) -> int:
    """Estimate pages before bible build from scope counts."""
    from bookforge.pipeline.feasibility import MAX_CARDS_PER_SECTION

    n = max(1, num_sections)
    if num_entries > 0:
        entries = num_entries
    else:
        entries = n * 3
    per = min(MAX_CARDS_PER_SECTION, max(1, math.ceil(entries / n)))
    sections = [
        SectionPlan(index=i + 1, title=f"S{i + 1}", full_card_quota=per, list_quota=0)
        for i in range(n)
    ]
    return estimate_rendered_pages(sections, profile, pamphlet=pamphlet)


def max_planned_entries(
    target_pages: int,
    profile,
    num_sections: int,
    *,
    pamphlet: bool = False,
) -> int:
    """Upper bound on full-card + list entries that fit the target page count."""
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    content_pages = max(1, target_pages - overhead)
    if num_sections <= 0:
        return max(2, content_pages // 2)

    pages_per_entry = max(0.5, getattr(profile, "pages_per_entry", 1.0) or 1.0)
    shell = _effective_section_shell(profile)
    pages_per_section = content_pages / num_sections
    cards_per_section = max(
        1,
        int((pages_per_section - shell) / pages_per_entry),
    )
    return max(1, cards_per_section * num_sections)


def max_entries_for_band(
    hi: int,
    num_sections: int,
    profile,
    *,
    pamphlet: bool = False,
) -> int:
    """Max entries whose preflight estimate fits under the page-band ceiling."""
    from bookforge.pipeline.feasibility import MAX_CARDS_PER_SECTION

    n = max(1, num_sections)
    cap = n * MAX_CARDS_PER_SECTION
    lo, hi_e = 1, cap
    best = 0
    while lo <= hi_e:
        mid = (lo + hi_e) // 2
        est = estimate_preflight_pages(
            num_sections=n,
            num_entries=mid,
            profile=profile,
            pamphlet=pamphlet,
        )
        if est <= hi:
            best = mid
            lo = mid + 1
        else:
            hi_e = mid - 1
    return max(1, best)


def max_sections_for_target(
    target_pages: int,
    profile,
    *,
    pamphlet: bool = False,
) -> int:
    """Maximum chapter count that can fit under the planning target.

    Budgets each section its shell PLUS a viable minimum of entries. Shell
    alone would be arithmetically correct but structurally useless: at the
    honest ~2.2pp shell a 300-page target "fits" 133 chapters, which is 133
    dividers and openers carrying almost no content. A chapter earns its
    divider only if it holds real entries, so that is what we budget for.
    """
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    ppe = max(0.5, getattr(profile, "pages_per_entry", 1.0) or 1.0)
    per_section = (
        _section_shell_pages(profile)
        + MIN_VIABLE_ENTRIES_PER_SECTION * ppe * _card_page_factor(profile)
    )
    return max(1, int((max(1, target_pages) - overhead) / per_section))


def page_range_prompt_suffix(target_pages: int) -> str:
    """Prompt clause stating the required finished page band."""
    lo, hi = allowed_page_range(target_pages)
    return (
        f"FINAL LENGTH LIMIT: the finished book must be at least {lo} pages and "
        f"must NOT exceed {hi} pages (target {target_pages}, hard maximum {hi}). "
        f"Plan chapters and entries to land in that band. "
        f"If the title names a count larger than fits in {lo} pages, plan the "
        f"entries that fit and state the fitted count consistently — never plan "
        f"content that requires more than {hi} pages."
    )


def _count_trimmable_units(chapters: list[Chapter]) -> int:
    total = 0
    for ch in chapters:
        for block in ch.blocks:
            if block.type == BlockType.use_list:
                total += len(block.entries)
            elif block.type == BlockType.use_card:
                total += 1
            elif block.type in (BlockType.body, BlockType.history, BlockType.method):
                total += 1
    return total


def _trim_one_from_end(chapters: list[Chapter]) -> bool:
    """Remove one trimmable unit from the last chapter. Returns False if none left."""
    if not chapters:
        return False

    ch = chapters[-1]
    for bi in range(len(ch.blocks) - 1, -1, -1):
        block = ch.blocks[bi]
        if block.type == BlockType.use_list and block.entries:
            block.entries.pop()
            if not block.entries:
                ch.blocks.pop(bi)
            return True
        if block.type == BlockType.use_card:
            ch.blocks.pop(bi)
            return True
        if block.type in (BlockType.body, BlockType.history, BlockType.method):
            ch.blocks.pop(bi)
            return True

    if len(chapters) > 1:
        chapters.pop()
        return True
    return False


def _chapter_block_refs(chapters: list[Chapter]) -> set[str]:
    refs: set[str] = set()
    for ch in chapters:
        refs.add(f"{ch.index}.divider")
        refs.add(f"{ch.index}.0")  # legacy divider ref
        for bi, block in enumerate(ch.blocks):
            if block.image_brief:
                refs.add(f"{ch.index}.{bi}")
    return refs


def scale_chapters_to_pages(
    chapters: list[Chapter],
    bible: BookBible,
    *,
    current_pages: int,
    target_pages: int,
    scale_attempt: int = 0,
) -> tuple[list[Chapter], int]:
    """Trim or expand generated content so the next render meets page targets.

    `scale_attempt` (0-based, one per page-scale round the caller runs) drives
    a monotonically-tightening prose-shrink floor. Character caps only ever
    shrink text, never regrow it — recomputing a shrink ratio purely from the
    CURRENT page count each round can come out LOOSER than a prior round's
    (if that round's actual reduction fell short of its own estimate),
    silently stalling the loop at a page count still over the ceiling.
    Escalating by attempt number instead guarantees each round is at least as
    aggressive as the last, regardless of how earlier rounds performed."""
    from bookforge.pipeline import layout_budget
    from bookforge.pipeline.content_guardrails import count_generated_entries

    lo, hi = allowed_page_range(target_pages)
    if lo <= current_pages <= hi:
        return chapters, 0

    out: list[Chapter] = copy.deepcopy(chapters)
    removed = 0
    cap_scale = 1.0

    if current_pages > hi:
        # Priority: shrink prose depth per entry FIRST (cap_scale below),
        # never entry COUNT — the book's promised count is honored, just
        # written more densely. Only remove whole entries as a last resort,
        # and never below the book's promised count: _trim_one_from_end will
        # happily pop whole chapters off the end once one runs out of
        # blocks, and with no floor here it could (and did) gut most of the
        # book's promised content while the title still claimed the
        # original count. An overshoot that can't be closed even with both
        # levers ships with a warning instead of silently underdelivering.
        floor = bible.original_promised_uses or bible.promised_uses or 0
        ratio_estimate = min(1.0, hi / max(current_pages, 1))
        escalation_floor = max(0.0, 1.0 - 0.3 * (scale_attempt + 1))
        cap_scale = min(ratio_estimate, escalation_floor)
        total = _count_trimmable_units(out)
        # Only lean on entry removal for the portion of the overshoot that
        # proportional shrinking alone is unlikely to close (shrinking text
        # saves some room but rarely the full ratio, since layout has fixed
        # per-entry overhead independent of prose length).
        residual = max(0.0, 1.0 - cap_scale - 0.25)
        to_remove = max(0, int(total * residual)) if total else 0
        while to_remove > 0:
            if floor > 0 and count_generated_entries(out) <= floor:
                log.warning("page_trim_floor_reached", floor=floor,
                            entries=count_generated_entries(out))
                break
            if not _trim_one_from_end(out):
                break
            removed += 1
            to_remove -= 1

    elif current_pages < lo:
        deficit = lo - current_pages
        n = max(len(bible.sections), 1)
        bump = max(1, deficit // n)
        for s in bible.sections:
            s.page_budget += bump
        log.info("page_expand_budget_bump", deficit=deficit, per_section=bump)

    trimmed = layout_budget.trim_chapters(out, bible, scale=cap_scale)
    return trimmed, removed


def expand_chapters_for_pages(
    chapters: list[Chapter],
    bible: BookBible,
    *,
    current_pages: int,
    target_pages: int,
) -> tuple[list[Chapter], int]:
    """Bump section budgets when PDF is under the page floor (for regen hints)."""
    lo, _ = allowed_page_range(target_pages)
    if current_pages >= lo:
        return chapters, 0
    out = copy.deepcopy(chapters)
    deficit = lo - current_pages
    n = max(len(bible.sections), 1)
    bump = max(1, deficit // n)
    for s in bible.sections:
        s.page_budget += bump
    log.info("page_expand_for_regen", deficit=deficit, per_section=bump)
    return out, bump


def _trim_sections_to_estimate(
    sections: list[SectionPlan],
    profile,
    *,
    target_estimate: int,
    min_entries: int = 0,
    min_sections: int = 0,
    specials: list | None = None,
    pamphlet: bool = False,
) -> list[SectionPlan]:
    from bookforge.pipeline.content_guardrails import (
        planned_use_count,
        shrink_sections_to_count,
    )

    out = [s.model_copy(deep=True) for s in sections]
    for _ in range(512):
        est = estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        )
        if est <= target_estimate:
            return out
        trimmed = False
        for s in sorted(out, key=lambda x: x.list_quota, reverse=True):
            if s.list_quota > 0:
                s.list_quota -= 1
                trimmed = True
                break
        if trimmed:
            continue
        for s in sorted(out, key=lambda x: x.full_card_quota, reverse=True):
            floor = 2
            if min_entries > 0 and planned_use_count(out) <= min_entries:
                break
            if s.full_card_quota > floor:
                s.full_card_quota -= 1
                trimmed = True
                break
        if trimmed:
            continue
        if len(out) > max(1, min_sections):
            # Same promised-count floor the card_quota loop just above
            # enforces — without this, popping a whole section (with all its
            # entries) can silently blow straight through min_entries in one
            # shot, breaking the promise instead of correctly leaving
            # reconcile_plan_to_page_band's overshoot check to raise
            # PlanInfeasibleError when the promise genuinely can't fit.
            if min_entries > 0 and planned_use_count(out) <= min_entries:
                break
            out.pop()
            continue
        break
    if min_entries > 0 and planned_use_count(out) > min_entries:
        out = shrink_sections_to_count(out, max(min_entries, planned_use_count(out)))
    return out


def _upsize_sections_to_estimate(
    sections: list[SectionPlan],
    profile,
    *,
    target_estimate: int,
    min_entries: int = 0,
    specials: list | None = None,
    pamphlet: bool = False,
    promised_ceiling: int | None = None,
    exact: bool = False,
) -> list[SectionPlan]:
    """Pad section quotas until the page estimate clears `target_estimate`.

    `promised_ceiling` (the book's stated/promised item count, with the same
    10% tolerance `reconcile_promise` uses elsewhere — or zero tolerance when
    `exact=True`, see `_promise_ceiling`) stops the *quantity* fallbacks from
    inventing entries past what the title promises — once hit, any remaining
    page deficit is left to page_pad.py's free layout-level padding and
    prose-depth expansion instead of more filler items."""
    from bookforge.pipeline.content_guardrails import (
        expand_sections_to_count,
        planned_use_count,
    )
    from bookforge.pipeline.feasibility import (
        MAX_CARDS_PER_SECTION,
        MAX_LIST_QUOTA_PER_SECTION,
    )

    ceiling = _promise_ceiling(promised_ceiling, exact=exact)

    out = [s.model_copy(deep=True) for s in sections]
    for _ in range(512):
        est = estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        )
        if est >= target_estimate:
            return out
        progressed = False
        under_ceiling = ceiling is None or planned_use_count(out) < ceiling
        if under_ceiling:
            for s in out:
                if s.full_card_quota < MAX_CARDS_PER_SECTION:
                    s.full_card_quota += 1
                    progressed = True
                    break
            if progressed:
                continue
            for s in out:
                if s.list_quota < MAX_LIST_QUOTA_PER_SECTION:
                    s.list_quota += 1
                    progressed = True
                    break
            if progressed:
                continue
        # page_budget adds page-room to EXISTING entries (deeper prose per
        # recipe), not more entries — stays available past the quantity
        # ceiling so a promise-honoring plan can still close a page gap.
        for s in out:
            if getattr(s, "page_budget", 0) < 40:
                s.page_budget = getattr(s, "page_budget", 0) + 1
                progressed = True
                break
        if progressed:
            continue
        break
    if min_entries > planned_use_count(out):
        out = expand_sections_to_count(out, min_entries)
    return out


def reconcile_plan_to_page_band(
    sections: list[SectionPlan],
    profile,
    *,
    target_pages: int,
    specials: list | None = None,
    pamphlet: bool = False,
    min_entries: int = 0,
    min_sections: int = 0,
    promised_ceiling: int | None = None,
    sections_locked: bool = False,
    exact: bool = False,
) -> list[SectionPlan]:
    """Upsize or trim the plan so the render estimate falls in [lo, hi].

    `promised_ceiling` (the book's stated item count) caps how far the
    quantity-based padding fallbacks in `_upsize_sections_to_estimate` may
    inflate entry counts — see that function's docstring.

    `sections_locked` (True when the chapter list came from the user's own
    table of contents) means the section *count* cannot grow no matter what
    target_pages is passed — the TOC is followed exactly, so raising the
    target only raises the floor `lo`, never the achievable `est`, in
    exactly the same way a promised-item ceiling does.

    `exact` (True for an unambiguous "create exactly N" / complete
    numbered-list promise) removes the usual 10% tolerance on
    `promised_ceiling` — the plan may never end up with more than exactly N
    entries, not just "close to" N."""
    lo, hi = allowed_page_range(target_pages)
    out = [s.model_copy(deep=True) for s in sections]
    est = estimate_rendered_pages(out, profile, specials=specials, pamphlet=pamphlet)

    if est < lo:
        from bookforge.pipeline.feasibility import PROSE_EXPAND_BUFFER

        out = _upsize_sections_to_estimate(
            out, profile, target_estimate=lo,
            min_entries=min_entries, specials=specials, pamphlet=pamphlet,
            promised_ceiling=promised_ceiling, exact=exact,
        )
        est = estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        )

    if est < lo:
        from bookforge.pipeline.content_guardrails import planned_use_count
        from bookforge.pipeline.feasibility import PROSE_EXPAND_BUFFER

        gap = lo - est
        if gap <= PROSE_EXPAND_BUFFER:
            log.warning(
                "plan_slightly_under_floor",
                estimated=est,
                floor=lo,
                target=target_pages,
                gap=gap,
                note="page-expand will deepen content at render time",
            )
            return out

        ceiling = _promise_ceiling(promised_ceiling, exact=exact)
        ceiling_capped = ceiling is not None and planned_use_count(out) >= ceiling
        if ceiling_capped or sections_locked:
            # Raising target_pages only raises `lo` (the floor) — it cannot
            # raise `est`, because either _upsize_sections_to_estimate
            # refuses to add entries past the promised-item ceiling, or (for
            # a locked table of contents) the section count itself can't
            # grow at all. Telling the user to "raise pages" here is not
            # just unhelpful, it's backwards: it widens the shortfall. The
            # only things that actually move `est` are a lower target, or
            # (respectively) a higher promised count / more chapters in the
            # table of contents / more source material.
            reason = (
                f"it's already at its promised item ceiling "
                f"({planned_use_count(out)} items)"
                if ceiling_capped else
                "its chapter count is locked to your table of contents"
            )
            fix = (
                "add more source material / promise more items"
                if ceiling_capped else
                "add more chapters to your table of contents / more source material"
            )
            raise PlanInfeasibleError(
                f"plan tops out at {est} pages — {reason} and target "
                f"{target_pages} needs at least {lo}. Raising pages will not "
                f"help; use ~{est} pages instead, or {fix} to allow a "
                f"bigger plan.",
                est=est, lo=lo, hi=hi, target_pages=target_pages,
                ceiling_capped=ceiling_capped, suggested_pages=max(1, est),
            )
        raise PlanInfeasibleError(
            f"plan estimates {est} pages but need at least {lo} "
            f"(target {target_pages}, max {hi}) — raise pages or add content",
            est=est, lo=lo, hi=hi, target_pages=target_pages,
            ceiling_capped=False,
        )

    if est > hi:
        out = _trim_sections_to_estimate(
            out, profile, target_estimate=hi,
            min_entries=min_entries, min_sections=min_sections,
            specials=specials, pamphlet=pamphlet,
        )
        est = estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        )

    if est > hi:
        # Unlike the undershoot ceiling-capped case above, raising target_pages
        # DOES directly raise `hi` (the ceiling), so "raise target" is genuinely
        # actionable here — give a concrete, checked number instead of a vague
        # one. target_pages=est is always sufficient (hard_page_ceiling(t) >= t
        # for any t <= ABSOLUTE_PAGE_CEILING), even if not the tightest possible.
        suggested = min(est, ABSOLUTE_PAGE_CEILING)
        raise PlanInfeasibleError(
            f"plan estimates {est} pages but maximum is {hi} (target "
            f"{target_pages}) — raise target to ~{suggested} pages, or reduce "
            f"chapters/items",
            est=est, lo=lo, hi=hi, target_pages=target_pages,
            ceiling_capped=False, suggested_pages=suggested,
        )

    log.info(
        "plan_reconciled_to_band",
        estimated=est, lo=lo, hi=hi, target=target_pages,
        sections=len(out), entries=sum(s.full_card_quota for s in out),
    )
    return out


def scale_images_for_chapters(
    images: list[GeneratedImage],
    chapters: list[Chapter],
) -> list[GeneratedImage]:
    """Drop card/divider images whose blocks were removed during page scaling."""
    valid = _chapter_block_refs(chapters)
    kept: list[GeneratedImage] = []
    for img in images:
        ref = img.block_ref or ""
        if ref == "cover" or not ref:
            kept.append(img)
        elif ref in valid:
            kept.append(img)
    return kept
