"""Feasibility math: can a promised entry count fit target_pages?

Prefer compact density (shorter entries) over shrinking the promised count.
Count reduction is never automatic — readiness blocks until the user accepts
a lower count or raises pages.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from bookforge.pipeline.content_guardrails import parse_promised_count
from bookforge.pipeline.plan_scope import (
    PlanScope,
    PlanScopeSummary,
    build_plan_scope,
    scope_summary,
)
from bookforge.pipeline.page_limits import (
    ABSOLUTE_PAGE_CEILING,
    allowed_page_range,
    estimate_preflight_pages,
    max_entries_for_band,
    _guide_fixed_overhead,
    _guide_specials_overhead,
)
from bookforge.schemas import EditorialProfile, EntrySchema

COMPACT_PAGES_PER_ENTRY = 0.8
STANDARD_COOKBOOK_PPE = 2.0
MIN_COMPACT_PPE = 0.8
MAX_CARDS_PER_SECTION = 8
PROSE_EXPAND_BUFFER = 15

DensityStatus = Literal["ok", "compact", "infeasible"]
AssessmentStatus = Literal["ok", "compact", "infeasible", "needs_input"]
AssessmentMode = Literal["standard", "compact", "expand"]


class FeasibilityOption(BaseModel):
    id: Literal["raise_pages", "lower_count", "lower_pages"]
    label: str
    target_pages: int | None = None
    promised_count: int | None = None


class FeasibilityResult(BaseModel):
    status: DensityStatus
    promised: int | None = None
    target_pages: int
    page_range_lo: int = 0
    page_range_hi: int = 0
    pages_per_entry: float
    min_pages_required: int
    max_entries_at_target: int
    estimated_pages: int | None = None
    message: str = ""
    options: list[FeasibilityOption] = Field(default_factory=list)


class BookAssessment(BaseModel):
    can_create: bool
    target_pages: int
    page_range_lo: int
    page_range_hi: int
    estimated_pages: int | None = None
    scope: PlanScopeSummary = Field(default_factory=PlanScopeSummary)
    status: AssessmentStatus
    message: str
    options: list[FeasibilityOption] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assessment_mode: AssessmentMode = "standard"


@dataclass
class _AssessmentBundle:
    feas: FeasibilityResult
    assessment: BookAssessment


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
        n = max(1, int(content_pages / max(shell, 0.5)))
        avail = content_pages - (n * shell)
    ppe = max(MIN_COMPACT_PPE, pages_per_entry)
    if avail <= 0:
        return 1
    raw = int(avail / ppe)
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


def _default_profile(cookbookish: bool) -> EditorialProfile:
    return EditorialProfile(
        genre="cookbook" if cookbookish else "practical guide",
        audience="general",
        angle="practical",
        entry_schema=EntrySchema(
            entry_noun="recipe" if cookbookish else "skill",
            headnote_lo=12 if cookbookish else 25,
            headnote_hi=22 if cookbookish else 45,
        ),
        structure_template="essay_entries" if cookbookish else "project_based",
        pages_per_entry=STANDARD_COOKBOOK_PPE if cookbookish else 2.0,
        image_density="every_entry" if cookbookish else "every_entry",
    )


def _entry_count(scope: PlanScope, effective: int | None, n_sections: int) -> int:
    entry_floor = effective or scope.min_entries or scope.source_entry_floor
    if entry_floor and entry_floor > 0:
        return entry_floor
    return max(1, n_sections) * 4


def _max_expandable_entries(
    n_sections: int,
    scope: PlanScope,
    effective: int | None,
) -> int:
    n = max(1, n_sections)
    cap = n * MAX_CARDS_PER_SECTION
    if effective and effective > 0:
        return effective
    floor = scope.source_entry_floor or scope.min_entries or 0
    return max(cap, floor) if floor > 0 else cap


def _max_expandable_estimate(
    n_sections: int,
    scope: PlanScope,
    profile: EditorialProfile,
    *,
    pamphlet: bool,
    effective: int | None,
) -> int:
    entries = _max_expandable_entries(n_sections, scope, effective)
    return estimate_preflight_pages(
        num_sections=max(1, n_sections),
        num_entries=entries,
        profile=profile,
        pamphlet=pamphlet,
    )


def _expand_undershoot(
    *,
    est: int,
    lo: int,
    hi: int,
    target: int,
    n_sections: int,
    scope: PlanScope,
    profile: EditorialProfile,
    pamphlet: bool,
    effective: int | None,
    promised: int | None,
    pages_per_entry: float,
    max_entries: int,
    warnings: list[str],
) -> _AssessmentBundle | None:
    """Return expand-allowed bundle when estimate is below floor but fillable."""
    if est >= lo:
        return None

    max_est = _max_expandable_estimate(
        n_sections, scope, profile, pamphlet=pamphlet, effective=effective,
    )
    expand_warnings = list(warnings)

    if max_est >= lo:
        expand_warnings.append(
            f"Outline is light (~{est} pp); planning will expand entries "
            f"to reach {lo} pages."
        )
        msg = (
            f"Ready — outline is light (~{est} pages); planning will expand "
            f"content to reach your {target}-page target (band {lo}–{hi})."
        )
        feas = FeasibilityResult(
            status="ok",
            promised=promised,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            pages_per_entry=pages_per_entry,
            min_pages_required=lo,
            max_entries_at_target=max_entries,
            estimated_pages=est,
            message=msg,
        )
        assessment = BookAssessment(
            can_create=True,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            estimated_pages=est,
            scope=scope_summary(scope),
            status="ok",
            message=msg,
            warnings=expand_warnings,
            assessment_mode="expand",
        )
        return _AssessmentBundle(feas=feas, assessment=assessment)

    gap = lo - max_est
    prose_ok = gap <= PROSE_EXPAND_BUFFER or max_est >= int(lo * 0.80)
    if prose_ok:
        expand_warnings.append(
            f"Outline estimates ~{est} pages; page-expand will deepen prose "
            f"to reach {lo}."
        )
        msg = (
            f"Ready — outline is slightly light (~{est} pages); generation will "
            f"expand content to reach {lo} pages (band {lo}–{hi})."
        )
        feas = FeasibilityResult(
            status="ok",
            promised=promised,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            pages_per_entry=pages_per_entry,
            min_pages_required=lo,
            max_entries_at_target=max_entries,
            estimated_pages=est,
            message=msg,
        )
        assessment = BookAssessment(
            can_create=True,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            estimated_pages=est,
            scope=scope_summary(scope),
            status="ok",
            message=msg,
            warnings=expand_warnings,
            assessment_mode="expand",
        )
        return _AssessmentBundle(feas=feas, assessment=assessment)

    suggested = max(max_est, est)
    msg = (
        f"Your outline needs at least ~{max_est} pages but you asked for "
        f"{target} (band {lo}–{hi}). Add more source detail or lower the target."
    )
    options = [
        FeasibilityOption(
            id="lower_pages",
            label=f"Use ~{suggested} pages",
            target_pages=suggested,
        ),
    ]
    if promised:
        options.append(
            FeasibilityOption(
                id="raise_pages",
                label=f"Keep {promised} items — use {min(ABSOLUTE_PAGE_CEILING, lo + gap)} pages",
                target_pages=min(ABSOLUTE_PAGE_CEILING, lo + gap),
            ),
        )
    feas = FeasibilityResult(
        status="infeasible",
        promised=promised,
        target_pages=target,
        page_range_lo=lo,
        page_range_hi=hi,
        pages_per_entry=pages_per_entry,
        min_pages_required=max_est,
        max_entries_at_target=max_entries,
        estimated_pages=est,
        message=msg,
        options=options,
    )
    assessment = BookAssessment(
        can_create=False,
        target_pages=target,
        page_range_lo=lo,
        page_range_hi=hi,
        estimated_pages=est,
        scope=scope_summary(scope),
        status="infeasible",
        message=msg,
        options=options,
        warnings=warnings,
        assessment_mode="standard",
    )
    return _AssessmentBundle(feas=feas, assessment=assessment)


def _handle_overshoot(
    *,
    est_std: int,
    lo: int,
    hi: int,
    target: int,
    n_sections: int,
    num_entries: int,
    profile: EditorialProfile,
    pamphlet: bool,
    effective: int | None,
    promised: int | None,
    scope: PlanScope,
    warnings: list[str],
    std_ppe: float,
    std_shell: float,
    user_compact: bool,
) -> _AssessmentBundle:
    """Try compact shortening before declaring infeasible."""
    compact_profile = apply_compact_profile(profile)
    n_compact = max(
        n_sections,
        math.ceil((effective or num_entries) / MAX_CARDS_PER_SECTION),
    )
    est_compact = estimate_preflight_pages(
        num_sections=n_compact,
        num_entries=effective or num_entries,
        profile=compact_profile,
        pamphlet=pamphlet,
    )
    max_compact = max_entries_for_band(
        hi, n_compact, compact_profile, pamphlet=pamphlet,
    )
    min_compact = min_pages_for_entries(
        effective or num_entries,
        COMPACT_PAGES_PER_ENTRY,
        n_compact,
        shell_pages=_compact_shell_pages(),
    )

    count = effective or promised
    if est_compact <= hi and (not count or count <= max_compact):
        use_compact = user_compact or est_std > target or est_std > hi
        status: DensityStatus = "compact" if use_compact else "ok"
        ppe = COMPACT_PAGES_PER_ENTRY if status == "compact" else std_ppe
        if count:
            msg = (
                f"Keeping {count} items; writing shorter entries to fit "
                f"{target}–{hi} pages (~{est_compact} estimated)."
                if status == "compact"
                else f"{count} items fit in {target}–{hi} pages (~{est_compact})."
            )
        else:
            msg = (
                f"Outline fits {target}–{hi} pages with shorter entries "
                f"(estimated ~{est_compact} pages)."
            )
        feas = FeasibilityResult(
            status=status,
            promised=promised,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            pages_per_entry=ppe,
            min_pages_required=min_compact if status == "compact" else max(lo, est_std),
            max_entries_at_target=max_compact,
            estimated_pages=est_compact,
            message=msg,
        )
        assessment = BookAssessment(
            can_create=True,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            estimated_pages=est_compact,
            scope=scope_summary(scope),
            status="compact" if status == "compact" else "ok",
            message=msg,
            warnings=warnings,
            assessment_mode="compact" if status == "compact" else "standard",
        )
        return _AssessmentBundle(feas=feas, assessment=assessment)

    suggested_pages = min(
        ABSOLUTE_PAGE_CEILING,
        max(target, min_compact, est_std, est_compact),
    )
    if count:
        options = [
            FeasibilityOption(
                id="raise_pages",
                label=f"Keep {count} items — use {suggested_pages} pages",
                target_pages=suggested_pages,
            ),
            FeasibilityOption(
                id="lower_count",
                label=f"Keep {target} pages — use {max_compact} items",
                promised_count=max_compact,
            ),
        ]
        msg = (
            f"{count} items won't fit in {target}–{hi} pages even when shortened "
            f"(need ~{max(min_compact, est_std)}). Choose: raise pages or reduce count."
        )
    else:
        options = [
            FeasibilityOption(
                id="raise_pages",
                label=f"Use {suggested_pages} pages",
                target_pages=suggested_pages,
            ),
        ]
        msg = (
            f"Your outline needs about {est_std} pages but the maximum at "
            f"target {target} is {hi}. Raise pages or shorten the outline."
        )
    feas = FeasibilityResult(
        status="infeasible",
        promised=promised,
        target_pages=target,
        page_range_lo=lo,
        page_range_hi=hi,
        pages_per_entry=COMPACT_PAGES_PER_ENTRY,
        min_pages_required=max(min_compact, est_std),
        max_entries_at_target=max_compact,
        estimated_pages=max(est_std, est_compact),
        message=msg,
        options=options,
    )
    assessment = BookAssessment(
        can_create=False,
        target_pages=target,
        page_range_lo=lo,
        page_range_hi=hi,
        estimated_pages=max(est_std, est_compact),
        scope=scope_summary(scope),
        status="infeasible",
        message=msg,
        options=options,
        warnings=warnings,
        assessment_mode="standard",
    )
    return _AssessmentBundle(feas=feas, assessment=assessment)


def _assess_book(
    *,
    promised: int | None,
    target_pages: int,
    toc_len: int | None = None,
    accepted_promised_count: int | None = None,
    cookbookish: bool = True,
    scope: PlanScope | None = None,
    text: str | None = None,
    density_mode: Literal["standard", "compact"] | None = None,
) -> _AssessmentBundle:
    """Unified assessment ladder — single source of truth for can_create."""
    target = max(1, min(int(target_pages), ABSOLUTE_PAGE_CEILING))
    lo, hi = allowed_page_range(target)
    effective = accepted_promised_count if accepted_promised_count else promised
    result_promised = effective if accepted_promised_count else promised

    profile = _default_profile(cookbookish)
    if density_mode == "compact":
        profile = apply_compact_profile(profile)
    pamphlet = target <= 15
    std_ppe = STANDARD_COOKBOOK_PPE if cookbookish else 2.0
    std_shell = _standard_shell_pages(cookbookish)
    user_compact = density_mode == "compact"

    if scope is None:
        scope = build_plan_scope(
            topic="", description=None, text=text, toc=None,
            accepted_promised_count=accepted_promised_count,
        )

    n_sections = scope.min_sections or _num_sections_guess(
        toc_len, target, std_shell,
    )
    if effective and effective > 0:
        n_sections = max(
            n_sections,
            _num_sections_guess(toc_len, target, std_shell),
            math.ceil(effective / MAX_CARDS_PER_SECTION),
        )

    num_entries = _entry_count(scope, effective, n_sections)
    est = estimate_preflight_pages(
        num_sections=n_sections,
        num_entries=num_entries,
        profile=profile,
        pamphlet=pamphlet,
    )
    max_entries = max_entries_for_band(hi, n_sections, profile, pamphlet=pamphlet)

    warnings: list[str] = []
    if scope.locked_toc and scope.min_sections > 0:
        warnings.append(
            f"{scope.min_sections} chapters from your outline will be kept."
        )

    # --- Overshoot: shorten first (compact) ---
    if est > hi:
        return _handle_overshoot(
            est_std=est,
            lo=lo,
            hi=hi,
            target=target,
            n_sections=n_sections,
            num_entries=num_entries,
            profile=_default_profile(cookbookish),
            pamphlet=pamphlet,
            effective=effective,
            promised=result_promised,
            scope=scope,
            warnings=warnings,
            std_ppe=std_ppe,
            std_shell=std_shell,
            user_compact=user_compact,
        )

    # --- Undershoot: expand when possible ---
    undershoot = _expand_undershoot(
        est=est,
        lo=lo,
        hi=hi,
        target=target,
        n_sections=n_sections,
        scope=scope,
        profile=_default_profile(cookbookish),
        pamphlet=pamphlet,
        effective=effective,
        promised=result_promised,
        pages_per_entry=profile.pages_per_entry,
        max_entries=max_entries,
        warnings=warnings,
    )
    if undershoot is not None:
        return undershoot

    # --- In band [lo, hi] ---
    if effective and effective > 0:
        if est > target and not user_compact:
            compact_profile = apply_compact_profile(_default_profile(cookbookish))
            est_compact = estimate_preflight_pages(
                num_sections=n_sections,
                num_entries=effective,
                profile=compact_profile,
                pamphlet=pamphlet,
            )
            if est_compact <= hi:
                min_compact = min_pages_for_entries(
                    effective, COMPACT_PAGES_PER_ENTRY, n_sections,
                    shell_pages=_compact_shell_pages(),
                )
                msg = (
                    f"{effective} items fit in {target}–{hi} pages with shorter "
                    f"entries (~{est_compact} estimated)."
                )
                feas = FeasibilityResult(
                    status="compact",
                    promised=effective,
                    target_pages=target,
                    page_range_lo=lo,
                    page_range_hi=hi,
                    pages_per_entry=COMPACT_PAGES_PER_ENTRY,
                    min_pages_required=min_compact,
                    max_entries_at_target=max_entries_for_band(
                        hi, n_sections, compact_profile, pamphlet=pamphlet,
                    ),
                    estimated_pages=est_compact,
                    message=msg,
                )
                assessment = BookAssessment(
                    can_create=True,
                    target_pages=target,
                    page_range_lo=lo,
                    page_range_hi=hi,
                    estimated_pages=est_compact,
                    scope=scope_summary(scope),
                    status="compact",
                    message=msg,
                    warnings=warnings,
                    assessment_mode="compact",
                )
                return _AssessmentBundle(feas=feas, assessment=assessment)

        msg = (
            f"{effective} items fit in {target}–{hi} pages (~{est} estimated)."
        )
        feas = FeasibilityResult(
            status="ok",
            promised=effective,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            pages_per_entry=profile.pages_per_entry,
            min_pages_required=max(lo, est),
            max_entries_at_target=max_entries,
            estimated_pages=est,
            message=msg,
        )
        assessment = BookAssessment(
            can_create=True,
            target_pages=target,
            page_range_lo=lo,
            page_range_hi=hi,
            estimated_pages=est,
            scope=scope_summary(scope),
            status="ok",
            message=msg,
            warnings=warnings,
            assessment_mode="compact" if user_compact else "standard",
        )
        return _AssessmentBundle(feas=feas, assessment=assessment)

    msg = f"Ready — estimated {est} pages (target {target}, band {lo}–{hi})."
    feas = FeasibilityResult(
        status="ok",
        promised=None,
        target_pages=target,
        page_range_lo=lo,
        page_range_hi=hi,
        pages_per_entry=profile.pages_per_entry,
        min_pages_required=max(lo, est),
        max_entries_at_target=max_entries,
        estimated_pages=est,
        message=msg,
    )
    assessment = BookAssessment(
        can_create=True,
        target_pages=target,
        page_range_lo=lo,
        page_range_hi=hi,
        estimated_pages=est,
        scope=scope_summary(scope),
        status="ok",
        message=msg,
        warnings=warnings,
        assessment_mode="compact" if user_compact else "standard",
    )
    return _AssessmentBundle(feas=feas, assessment=assessment)


def resolve_density(
    *,
    promised: int | None,
    target_pages: int,
    toc_len: int | None = None,
    accepted_promised_count: int | None = None,
    cookbookish: bool = True,
    scope: PlanScope | None = None,
    text: str | None = None,
    density_mode: Literal["standard", "compact"] | None = None,
) -> FeasibilityResult:
    """Decide whether the request fits at standard, compact, or not at all."""
    return _assess_book(
        promised=promised,
        target_pages=target_pages,
        toc_len=toc_len,
        accepted_promised_count=accepted_promised_count,
        cookbookish=cookbookish,
        scope=scope,
        text=text,
        density_mode=density_mode,
    ).feas


def assess_request(
    *,
    topic: str,
    description: str | None = None,
    text: str | None = None,
    target_pages: int,
    toc: list[str] | None = None,
    accepted_promised_count: int | None = None,
    density_mode: Literal["standard", "compact"] | None = None,
) -> FeasibilityResult:
    """Parse promise / scope from the request and resolve density."""
    promised = parse_promised_count(topic, description or "")
    combined = f"{topic} {(description or '')}".lower()
    cookbookish = (
        (promised is not None and promised >= 20)
        or "recipe" in combined
        or "cookbook" in combined
        or "supper" in combined
        or "meal" in combined
    )
    scope = build_plan_scope(
        topic=topic,
        description=description,
        text=text,
        toc=toc,
        accepted_promised_count=accepted_promised_count,
    )
    return resolve_density(
        promised=promised,
        target_pages=target_pages,
        toc_len=len(toc) if toc else None,
        accepted_promised_count=accepted_promised_count,
        cookbookish=cookbookish,
        scope=scope,
        text=text,
        density_mode=density_mode,
    )


def build_book_assessment(
    *,
    topic: str,
    description: str | None = None,
    text: str | None = None,
    target_pages: int,
    toc: list[str] | None = None,
    accepted_promised_count: int | None = None,
    density_mode: Literal["standard", "compact"] | None = None,
) -> BookAssessment:
    """Pre-flight assessment shown before generation starts."""
    promised = parse_promised_count(topic, description or "")
    combined = f"{topic} {(description or '')}".lower()
    cookbookish = (
        (promised is not None and promised >= 20)
        or "recipe" in combined
        or "cookbook" in combined
        or "supper" in combined
        or "meal" in combined
    )
    scope = build_plan_scope(
        topic=topic,
        description=description,
        text=text,
        toc=toc,
        accepted_promised_count=accepted_promised_count,
    )
    return _assess_book(
        promised=promised,
        target_pages=target_pages,
        toc_len=len(toc) if toc else None,
        accepted_promised_count=accepted_promised_count,
        cookbookish=cookbookish,
        scope=scope,
        text=text,
        density_mode=density_mode,
    ).assessment


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
