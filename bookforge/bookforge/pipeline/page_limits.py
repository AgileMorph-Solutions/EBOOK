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


def _guide_fixed_overhead(pamphlet: bool) -> int:
    return 2 if pamphlet else 9


def _guide_specials_overhead(pamphlet: bool) -> int:
    return 0 if pamphlet else 4


def _section_shell_pages(profile) -> float:
    """Per-section layout overhead used for planning estimates."""
    from bookforge.pipeline import templates

    spec = templates.spec_for(profile.structure_template)
    if spec.has_opener_essay:
        return 4.5  # divider + essay + pull-quote page + shell
    return 1.6


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
    shell = _effective_section_shell(profile)
    ppe = max(0.5, getattr(profile, "pages_per_entry", 1.0) or 1.0)
    factor = _card_page_factor(profile)
    cards = sum(
        getattr(s, "full_card_quota", 0)
        + getattr(s, "list_quota", 0) * 0.15
        for s in sections
    )
    chart_pages = len(specials or []) * 2
    budget_depth = sum(
        max(0, getattr(s, "page_budget", 0) - 2) for s in sections
    ) * 0.25
    body = len(sections) * shell + cards * ppe * factor + chart_pages + budget_depth
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
    """Maximum chapter count that can fit under the planning target."""
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    shell = _section_shell_pages(profile)
    return max(1, int((max(1, target_pages) - overhead) / shell))


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
) -> tuple[list[Chapter], int]:
    """Trim or expand generated content so the next render meets page targets."""
    from bookforge.pipeline import layout_budget

    lo, hi = allowed_page_range(target_pages)
    if lo <= current_pages <= hi:
        return chapters, 0

    out: list[Chapter] = copy.deepcopy(chapters)
    removed = 0

    if current_pages > hi:
        ratio = min(1.0, hi / max(current_pages, 1))
        total = _count_trimmable_units(out)
        to_remove = max(1, int(total * (1 - ratio))) if total else 1
        while to_remove > 0 and _trim_one_from_end(out):
            removed += 1
            to_remove -= 1

    elif current_pages < lo:
        deficit = lo - current_pages
        n = max(len(bible.sections), 1)
        bump = max(1, deficit // n)
        for s in bible.sections:
            s.page_budget += bump
        log.info("page_expand_budget_bump", deficit=deficit, per_section=bump)

    trimmed = layout_budget.trim_chapters(out, bible)
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
) -> list[SectionPlan]:
    from bookforge.pipeline.content_guardrails import (
        expand_sections_to_count,
        planned_use_count,
    )
    from bookforge.pipeline.feasibility import MAX_CARDS_PER_SECTION

    out = [s.model_copy(deep=True) for s in sections]
    for _ in range(512):
        est = estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        )
        if est >= target_estimate:
            return out
        progressed = False
        for s in out:
            if s.full_card_quota < MAX_CARDS_PER_SECTION:
                s.full_card_quota += 1
                progressed = True
                break
        if progressed:
            continue
        for s in out:
            s.list_quota += 1
            progressed = True
            break
        if progressed:
            continue
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
) -> list[SectionPlan]:
    """Upsize or trim the plan so the render estimate falls in [lo, hi]."""
    lo, hi = allowed_page_range(target_pages)
    out = [s.model_copy(deep=True) for s in sections]
    est = estimate_rendered_pages(out, profile, specials=specials, pamphlet=pamphlet)

    if est < lo:
        from bookforge.pipeline.feasibility import PROSE_EXPAND_BUFFER

        out = _upsize_sections_to_estimate(
            out, profile, target_estimate=lo,
            min_entries=min_entries, specials=specials, pamphlet=pamphlet,
        )
        est = estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        )

    if est < lo:
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
        raise PlanInfeasibleError(
            f"plan estimates {est} pages but need at least {lo} "
            f"(target {target_pages}, max {hi}) — raise pages or add content"
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
        raise PlanInfeasibleError(
            f"plan estimates {est} pages but maximum is {hi} "
            f"(target {target_pages}) — raise target or reduce chapters/items"
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
