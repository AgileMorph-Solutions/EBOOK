"""Page-count limits and content scaling for book generation.

Finished PDFs must fall in [target_pages, min(ceil(1.5 * target_pages), 300)].
Post-layout scaling expands or trims content; jobs fail if still out of band.
"""
from __future__ import annotations

import copy
import math

import structlog

from bookforge.errors import PageLimitError
from bookforge.schemas import BlockType, BookBible, Chapter, GeneratedImage

log = structlog.get_logger(__name__)

MAX_BOOK_PAGES = 300
MAX_PAGES_OVERSHOOT_RATIO = 1.5
ABSOLUTE_PAGE_CEILING = MAX_BOOK_PAGES


def hard_page_ceiling(target_pages: int) -> int:
    """Hard maximum pages for the finished PDF — 1.5× target, capped at 300."""
    target = max(1, target_pages)
    return min(math.ceil(target * MAX_PAGES_OVERSHOOT_RATIO), ABSOLUTE_PAGE_CEILING)


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
    """True when PDF page count is within [target, 1.5× target]."""
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


def estimate_guide_pages(
    sections: list,
    *,
    pamphlet: bool = False,
) -> int:
    """Rough planned page total from section page budgets + book overhead."""
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    return overhead + sum(getattr(s, "page_budget", 0) for s in sections)


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
    section_shell = _section_shell_pages(profile)
    pages_per_section = content_pages / num_sections
    cards_per_section = max(
        1,
        int((pages_per_section - section_shell) / pages_per_entry),
    )
    return max(1, cards_per_section * num_sections)


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
        f"must NOT exceed {hi} pages (target {target_pages}, hard maximum "
        f"{hi} = 1.5× target). Plan chapters and entries to land in that band. "
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
