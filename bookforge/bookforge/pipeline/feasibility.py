"""Feasibility math: can a promised entry count fit target_pages?

Prefer compact density (shorter entries) over shrinking the promised count.
Count reduction is never automatic — readiness blocks until the user accepts
a lower count or raises pages.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from bookforge.pipeline.content_guardrails import parse_promised_count
from bookforge.pipeline.page_limits import (
    ABSOLUTE_PAGE_CEILING,
    _guide_fixed_overhead,
    _guide_specials_overhead,
)
from bookforge.schemas import EditorialProfile, EntrySchema

COMPACT_PAGES_PER_ENTRY = 0.8
STANDARD_COOKBOOK_PPE = 2.0
MIN_COMPACT_PPE = 0.8
MAX_CARDS_PER_SECTION = 8

DensityStatus = Literal["ok", "compact", "infeasible"]


class FeasibilityOption(BaseModel):
    id: Literal["raise_pages", "lower_count"]
    label: str
    target_pages: int | None = None
    promised_count: int | None = None


class FeasibilityResult(BaseModel):
    status: DensityStatus
    promised: int | None = None
    target_pages: int
    pages_per_entry: float
    min_pages_required: int
    max_entries_at_target: int
    message: str = ""
    options: list[FeasibilityOption] = Field(default_factory=list)


def _compact_shell_pages() -> float:
    """Per-section overhead when essays are shortened for compact mode."""
    return 1.6


def _standard_shell_pages(has_opener_essay: bool = True) -> float:
    return 4.5 if has_opener_essay else 1.6


def _num_sections_guess(toc_len: int | None, target_pages: int, shell: float) -> int:
    overhead = _guide_fixed_overhead(False) + _guide_specials_overhead(False)
    max_secs = max(1, int((max(1, target_pages) - overhead) / max(shell, 0.5)))
    if toc_len and toc_len > 0:
        return max(1, min(toc_len, max_secs))
    return max(4, min(14, max_secs))


def min_pages_for_entries(
    count: int,
    pages_per_entry: float,
    num_sections: int,
    *,
    pamphlet: bool = False,
    shell_pages: float | None = None,
) -> int:
    """Minimum pages needed for `count` entries at the given density."""
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    n = max(1, num_sections)
    shell = shell_pages if shell_pages is not None else _compact_shell_pages()
    # Cap how many cards we put in one section for generation safety.
    n = max(n, math.ceil(count / MAX_CARDS_PER_SECTION))
    content = (count * max(MIN_COMPACT_PPE, pages_per_entry)) + (n * shell)
    return max(1, int(math.ceil(overhead + content)))


def max_entries_for_pages(
    target_pages: int,
    pages_per_entry: float,
    num_sections: int,
    *,
    pamphlet: bool = False,
    shell_pages: float | None = None,
) -> int:
    """Max entries that fit under target_pages at the given density."""
    overhead = _guide_fixed_overhead(pamphlet) + _guide_specials_overhead(pamphlet)
    content_pages = max(1, target_pages - overhead)
    n = max(1, num_sections)
    shell = shell_pages if shell_pages is not None else _compact_shell_pages()
    avail = content_pages - (n * shell)
    if avail <= 0:
        # Too many sections for the page budget — reduce section assumption.
        n = max(1, int(content_pages / max(shell, 0.5)))
        avail = content_pages - (n * shell)
    ppe = max(MIN_COMPACT_PPE, pages_per_entry)
    if avail <= 0:
        return 1
    raw = int(avail / ppe)
    # Respect per-section generation cap.
    return max(1, min(raw, n * MAX_CARDS_PER_SECTION))


def apply_compact_profile(profile: EditorialProfile) -> EditorialProfile:
    """Return a copy of profile tuned for short entries that keep count."""
    es = profile.entry_schema.model_copy(update={
        "headnote_lo": min(profile.entry_schema.headnote_lo, 12),
        "headnote_hi": min(profile.entry_schema.headnote_hi, 22),
        "max_steps": min(getattr(profile.entry_schema, "max_steps", 7) or 7, 5),
    })
    return profile.model_copy(update={
        "pages_per_entry": COMPACT_PAGES_PER_ENTRY,
        "entry_schema": es,
        "narrative_share": min(getattr(profile, "narrative_share", 0.35) or 0.35, 0.15),
    })


def resolve_density(
    *,
    promised: int | None,
    target_pages: int,
    toc_len: int | None = None,
    accepted_promised_count: int | None = None,
    cookbookish: bool = True,
) -> FeasibilityResult:
    """Decide whether the request fits at standard, compact, or not at all."""
    target = max(1, min(int(target_pages), ABSOLUTE_PAGE_CEILING))
    effective = accepted_promised_count if accepted_promised_count else promised

    if not effective or effective < 1:
        return FeasibilityResult(
            status="ok",
            promised=None,
            target_pages=target,
            pages_per_entry=STANDARD_COOKBOOK_PPE if cookbookish else 1.0,
            min_pages_required=target,
            max_entries_at_target=max_entries_for_pages(
                target, STANDARD_COOKBOOK_PPE if cookbookish else 1.0,
                _num_sections_guess(toc_len, target, _standard_shell_pages()),
            ),
            message="No marketing count detected — proceeding at standard density.",
        )

    std_ppe = STANDARD_COOKBOOK_PPE if cookbookish else 1.0
    std_shell = _standard_shell_pages(cookbookish)
    n_std = _num_sections_guess(toc_len, target, std_shell)
    max_std = max_entries_for_pages(target, std_ppe, n_std, shell_pages=std_shell)
    min_std = min_pages_for_entries(effective, std_ppe, n_std, shell_pages=std_shell)

    if effective <= max_std:
        return FeasibilityResult(
            status="ok",
            promised=effective,
            target_pages=target,
            pages_per_entry=std_ppe,
            min_pages_required=min_std,
            max_entries_at_target=max_std,
            message=(
                f"{effective} items fit in {target} pages at standard layout."
            ),
        )

    compact_shell = _compact_shell_pages()
    n_c = _num_sections_guess(toc_len, target, compact_shell)
    # Ensure enough sections for the entry count under the per-section cap.
    n_c = max(n_c, math.ceil(effective / MAX_CARDS_PER_SECTION))
    max_compact = max_entries_for_pages(
        target, COMPACT_PAGES_PER_ENTRY, n_c, shell_pages=compact_shell,
    )
    min_compact = min_pages_for_entries(
        effective, COMPACT_PAGES_PER_ENTRY, n_c, shell_pages=compact_shell,
    )
    # Cap suggested pages at absolute ceiling.
    suggested_pages = min(ABSOLUTE_PAGE_CEILING, max(target, min_compact))

    if effective <= max_compact and min_compact <= target:
        return FeasibilityResult(
            status="compact",
            promised=effective,
            target_pages=target,
            pages_per_entry=COMPACT_PAGES_PER_ENTRY,
            min_pages_required=min_compact,
            max_entries_at_target=max_compact,
            message=(
                f"Keeping {effective} items; writing shorter entries to fit "
                f"{target} pages."
            ),
        )

    # Even compact cannot fit — user must choose.
    options = [
        FeasibilityOption(
            id="raise_pages",
            label=(
                f"Keep {effective} items — use {suggested_pages} pages"
            ),
            target_pages=suggested_pages,
        ),
        FeasibilityOption(
            id="lower_count",
            label=(
                f"Keep {target} pages — use {max_compact} items"
            ),
            promised_count=max_compact,
        ),
    ]
    return FeasibilityResult(
        status="infeasible",
        promised=effective,
        target_pages=target,
        pages_per_entry=COMPACT_PAGES_PER_ENTRY,
        min_pages_required=min_compact,
        max_entries_at_target=max_compact,
        message=(
            f"{effective} items won't fit in {target} pages even when shortened. "
            f"Choose: raise to ~{suggested_pages} pages, or reduce to "
            f"~{max_compact} items."
        ),
        options=options,
    )


def assess_request(
    *,
    topic: str,
    description: str | None = None,
    target_pages: int,
    toc: list[str] | None = None,
    accepted_promised_count: int | None = None,
) -> FeasibilityResult:
    """Parse promise from topic/description and resolve density."""
    promised = parse_promised_count(topic, description or "")
    text = f"{topic} {(description or '')}".lower()
    cookbookish = (
        (promised is not None and promised >= 20)
        or "recipe" in text
        or "cookbook" in text
        or "supper" in text
        or "meal" in text
    )
    return resolve_density(
        promised=promised,
        target_pages=target_pages,
        toc_len=len(toc) if toc else None,
        accepted_promised_count=accepted_promised_count,
        cookbookish=cookbookish,
    )


def compact_profile_stub() -> EditorialProfile:
    """Minimal profile used only for math in tests / readiness."""
    return EditorialProfile(
        genre="cookbook",
        audience="general",
        angle="practical",
        entry_schema=EntrySchema(entry_noun="recipe", headnote_lo=12, headnote_hi=22),
        structure_template="essay_entries",
        pages_per_entry=COMPACT_PAGES_PER_ENTRY,
    )
