"""End-to-end pipeline orchestration (provider-agnostic core).

This is the single source of truth for the stage sequence. The CLI runs it in
one process; Celery wraps each stage as a task for the production DAG. Keeping
the logic here means both paths execute identical steps.

Never-fail contract: every stage repairs first, then degrades to shipping the
best available book with `warnings` — a job only fails for cancelled input,
an unusable source, a provider hard-down past the parking window, or budget
exhausted before anything shippable existed. Checkpoints (source, bible,
per-section, chapters, qa, images, spend) make every park/crash resumable
without re-spending tokens.
"""
from __future__ import annotations

from collections.abc import Callable

import structlog

from bookforge import cost
from bookforge.clients import llm, storage
from bookforge.config import settings
from bookforge.cost import BudgetExceeded
from bookforge.errors import GeometryError, LayoutError, PageLimitError
from bookforge.pipeline import (
    assemble, bible as bible_stage, checkpoints, content, content_guide, guide_images,
    images, ingest, layout_budget, layout_qa, qa,
)
from bookforge.pipeline.content_guardrails import finalize_marketing_counts
from bookforge.pipeline.page_limits import (
    allowed_page_range,
    expand_chapters_for_pages,
    estimate_rendered_pages,
    is_acceptable_page_count,
    scale_chapters_to_pages,
    scale_images_for_chapters,
    _upsize_sections_to_estimate,
)
from bookforge.schemas import (
    BlockType, BookArtifact, BookBible, Chapter, QAResult, SourceDoc,
)

log = structlog.get_logger(__name__)


def _total_words(chapters: list[Chapter]) -> int:
    """Rough body-word count — the yardstick for "did expansion actually expand".

    Counts every surface the layout turns into pages: block prose plus the
    per-entry headnote, ingredient/needs list and steps. Absolute accuracy does
    not matter; it is only ever compared against itself across one regen.
    """
    total = 0
    for ch in chapters:
        for b in ch.blocks:
            total += len((b.text or "").split())
            for e in b.entries:
                total += len((e.intro or "").split())
                total += sum(len(n.split()) for n in e.what_you_need)
                total += sum(len((s.text or "").split()) for s in e.steps)
    return total


def _structural_expand_plan(
    book_bible: BookBible,
    chapters: list[Chapter],
    *,
    target_pages: int,
) -> tuple[BookBible, dict[int, list[str]]]:
    """Upsize section quotas when the plan estimate sits below the page floor."""
    from bookforge.pipeline.content_guardrails import planned_use_count

    profile = book_bible.profile
    if not profile or book_bible.book_kind != "practical_guide":
        return book_bible, {}

    lo, _ = allowed_page_range(target_pages)
    pamphlet = target_pages <= 15
    est = estimate_rendered_pages(
        book_bible.sections, profile, pamphlet=pamphlet,
    )
    if est >= lo:
        return book_bible, {}

    new_sections = _upsize_sections_to_estimate(
        book_bible.sections,
        profile,
        target_estimate=lo,
        min_entries=planned_use_count(book_bible.sections),
        pamphlet=pamphlet,
        promised_ceiling=book_bible.original_promised_uses or book_bible.promised_uses,
    )
    if planned_use_count(new_sections) <= planned_use_count(book_bible.sections):
        return book_bible, {}

    new_bible = book_bible.model_copy(update={"sections": new_sections})
    noun = profile.entry_schema.entry_noun
    sections_by_idx = {s.index: s for s in new_sections}
    faults: dict[int, list[str]] = {}
    for ch in chapters:
        section = sections_by_idx.get(ch.index)
        if not section:
            continue
        n_cards = sum(1 for b in ch.blocks if b.type == BlockType.use_card)
        if n_cards < section.full_card_quota:
            faults[ch.index] = [
                f"add {section.full_card_quota - n_cards} full {noun} cards "
                f"(book must reach at least {lo} pages)"
            ]
    log.info(
        "page_structural_expand",
        estimated_before=est,
        floor=lo,
        entries_before=planned_use_count(book_bible.sections),
        entries_after=planned_use_count(new_sections),
        sections_with_new_cards=len(faults),
    )
    return new_bible, faults


def _layout_faults(defects: list[dict], chapters: list[Chapter]) -> dict[int, list[str]]:
    """Map audit overflow records to per-chapter rewrite instructions."""
    faults: dict[int, list[str]] = {}
    for d in defects:
        key = str(d.get("entry") or "")
        core = key.split(":", 1)[1] if ":" in key else key
        ch_part = core.split(".", 1)[0]
        if not ch_part.isdigit():
            continue
        idx = int(ch_part)
        snippet = str(d.get("text") or "")[:60]
        if key.startswith("essay:"):
            faults.setdefault(idx, []).append(
                "the chapter-opener essay is too long for its pages even after "
                "re-pagination — rewrite it at most 300 words, same substance")
        elif key.startswith("chart:"):
            faults.setdefault(idx, []).append(
                "the reference chart has too few rows or bad layout — "
                "regenerate the full chart with every planned row filled")
        else:
            faults.setdefault(idx, []).append(
                f"the entry starting '{snippet}' cannot fit even a multi-page "
                "layout — rewrite it tighter: intro ≤ 25 words, at most 5 steps "
                "of ≤ 18 words each, ≤ 8 what_you_need items, variation/note "
                "≤ 12 words each; keep every quantity and safety detail")
    return faults


def _vision_layout_faults(
    message: str,
    bible: BookBible,
    chapters: list[Chapter] | None = None,
) -> dict[int, list[str]]:
    """Map vision QA defects to chapter regen faults."""
    lower = message.lower()
    faults: dict[int, list[str]] = {}
    if any(k in lower for k in ("pull-quote", "pull quote", "blockquote")) and any(
        k in lower for k in ("clip", "clipped", "cut off", "overflow", "page edge")
    ):
        for ch in chapters or []:
            has_essay = any(b.type == BlockType.history for b in ch.blocks)
            has_quote = any(b.type == BlockType.pull_quote for b in ch.blocks)
            if has_essay and has_quote:
                faults.setdefault(ch.index, []).append(
                    "the chapter-opener essay is too long — rewrite it at most "
                    "280 words and shorten the pull quote to under 18 words")
        if faults:
            return faults
    if not any(k in lower for k in ("chart", "table", "reference", "swap", "row")):
        return {}
    for sp in bible.specials:
        if sp.kind != "chart":
            continue
        idx = sp.after_section
        faults.setdefault(idx, []).append(
            f"regenerate the chart '{sp.heading}' with EXACTLY {sp.rows} full rows "
            f"in columns {', '.join(sp.columns)} — no sparse or single-row tables")
    return faults


def _assemble_with_layout_revise(
    book_id: str,
    book_bible: BookBible,
    chapters: list[Chapter],
    all_images: list,
    qa_result,
    *,
    cover_key: str | None,
    pro: dict | None,
    source,
    stage: Callable[[str], None],
) -> BookArtifact:
    """Assemble, and if the layout audit fails even after the deterministic
    repair ladder, REGENERATE the offending blocks with hard length budgets and
    assemble again (bounded by MAX_LAYOUT_REVISIONS). When faults are
    unmappable or the revision budget is spent, the book SHIPS in degraded mode
    (best render + warnings + operator alert) instead of dying — restore the
    old hard-fail with SHIP_ON_LAYOUT_DEFECTS=false."""
    chapters_cur = chapters
    last_exc: LayoutError | None = None
    for attempt in range(settings.max_layout_revisions + 1):
        try:
            return assemble.assemble(
                book_id, book_bible, chapters_cur, all_images, qa_result,
                cover_key=cover_key, pro=pro,
            )
        except LayoutError as exc:
            last_exc = exc
            faults = _layout_faults(exc.defects, chapters_cur)
            if not faults:
                faults = _vision_layout_faults(str(exc), book_bible, chapters_cur)
            if not faults or attempt >= settings.max_layout_revisions:
                break
            stage(f"layout-revise ({attempt + 1})")
            log.warning("layout_revise", attempt=attempt + 1,
                        chapters=sorted(faults), issues=sum(map(len, faults.values())))
            try:
                chapters_cur = content.regenerate_guide_sections(
                    chapters_cur, book_bible, source, faults,
                    registry_seed=chapters_cur)
            except BudgetExceeded:
                log.warning("layout_revise_budget_exhausted", attempt=attempt + 1)
                break
            chapters_cur = layout_budget.trim_chapters(chapters_cur, book_bible)
    if not settings.ship_on_layout_defects:
        raise last_exc if last_exc is not None else AssertionError("unreachable")
    stage("layout-degraded")
    log.error("layout_ship_degraded", book_id=book_id,
              err=str(last_exc)[:200] if last_exc else "")
    return assemble.assemble(
        book_id, book_bible, chapters_cur, all_images, qa_result,
        cover_key=cover_key, pro=pro, degraded=True,
    )


def _final_vision_qa(
    book_id: str,
    artifact: BookArtifact,
    book_bible: BookBible,
    chapters: list[Chapter],
    all_images: list,
    qa_result,
    *,
    cover_key: str | None,
    pro: dict | None,
    source,
    stage: Callable[[str], None],
) -> BookArtifact:
    """Vision-inspect the ACCEPTED artifact once (final-only mode): rasterized
    from the shipped PDF, so it sees the exact bytes that ship and costs no
    extra Chromium render. Blocking hits only trigger a block-regen round when
    the deterministic audit corroborates a degradation; uncorroborated hits
    (the observed false-positive class) ship as loud warnings. Never raises."""
    if not (settings.layout_qa_enabled and settings.layout_qa_final_only):
        return artifact
    for attempt in range(2):
        try:
            pdf = storage.get(f"books/{book_id}/interior.pdf")
        except Exception as exc:  # noqa: BLE001 — inspection is best-effort
            log.warning("final_vision_qa_no_pdf", err=str(exc)[:120])
            return artifact
        report = checkpoints.load_fit_report(book_id) or {}
        stage("layout-qa")
        try:
            issues = layout_qa.run_layout_qa_pdf(
                pdf, book_bible, page_classes=report.get("pages"),
                section_count=len(book_bible.sections),
            )
        except BudgetExceeded:
            artifact.warnings.append(
                "budget exhausted before final visual inspection — shipped uninspected")
            return artifact
        except Exception as exc:  # noqa: BLE001
            if llm.never_swallow(exc):
                raise
            issues = [f"layout QA (skipped): {str(exc)[:120]}"]
        if artifact.qa is not None:
            artifact.qa.layout_issues.extend(issues)
        blocking = assemble.blocking_vision_issues(issues)
        if not blocking:
            return artifact
        if not (settings.layout_qa_block or book_bible.profile is not None):
            return artifact
        corroborated = artifact.degraded or bool(artifact.warnings)
        faults = _vision_layout_faults(" | ".join(blocking), book_bible, chapters)
        if corroborated and faults and attempt == 0:
            stage("layout-revise (vision)")
            log.warning("final_vision_qa_revise", issues=blocking[:3])
            try:
                chapters = content.regenerate_guide_sections(
                    chapters, book_bible, source, faults, registry_seed=chapters)
            except BudgetExceeded:
                artifact.warnings.extend(
                    f"vision QA (unresolved): {b}" for b in blocking[:5])
                return artifact
            chapters = layout_budget.trim_chapters(chapters, book_bible)
            checkpoints.save_chapters(book_id, chapters)
            artifact = _assemble_with_layout_revise(
                book_id, book_bible, chapters, all_images, qa_result,
                cover_key=cover_key, pro=pro, source=source, stage=stage,
            )
            target = book_bible.target_pages
            if target:
                lo, _hi = allowed_page_range(target)
                if artifact.page_count < lo:
                    log.warning("vision_qa_trim_undershoot",
                                pages=artifact.page_count, floor=lo)
                    artifact.warnings.append(
                        f"page count {artifact.page_count} fell below the "
                        f"{lo}-page target after a late layout fix — shipped anyway")
                    artifact.degraded = True
            continue
        tag = "uncorroborated" if not corroborated else "unresolved"
        artifact.warnings.extend(f"vision QA ({tag}): {b}" for b in blocking[:5])
        log.warning("final_vision_qa_shipped_with_warnings", tag=tag,
                    issues=blocking[:5])
        return artifact
    return artifact


def _refresh_images(book_id: str, book_bible: BookBible,
                    chapters: list[Chapter], images_cur: list,
                    cover_key: str | None) -> tuple[list, str | None]:
    """Fill newly-needed image slots inside the page-scale loop; already-
    materialized slots are skipped by checkpoint. Quota/budget shortfalls keep
    the current set instead of killing the trim pass."""
    try:
        new_images, new_cover = guide_images.generate_all(
            book_bible, chapters, book_id,
        )
        checkpoints.save_images(book_id, new_images)
        return new_images, new_cover
    except BudgetExceeded:
        log.warning("page_scale_images_budget_exhausted", book_id=book_id)
        return images_cur, cover_key
    except Exception as exc:  # noqa: BLE001 — refill is an optimization
        if llm.never_swallow(exc):
            raise
        log.warning("page_scale_images_refresh_failed", err=str(exc)[:200])
        return images_cur, cover_key


def _assemble_with_page_scale(
    book_id: str,
    book_bible: BookBible,
    chapters: list[Chapter],
    all_images: list,
    qa_result,
    *,
    cover_key: str | None,
    pro: dict | None,
    source,
    stage: Callable[[str], None],
) -> BookArtifact:
    """Assemble with layout repair; scale content to meet the page band.
    Books outside [target, page ceiling] after all scale passes raise
    PageLimitError unless SHIP_ON_PAGE_OVERSHOOT=true (overshoot only)."""
    target = book_bible.target_pages or 100
    # The bible-stage plan estimate, captured BEFORE the scale loop can
    # mutate book_bible (page-expand replaces it) — this is what the
    # synchronous pre-flight gate saw for this book. Logging it against the
    # real final page count on every completed run (success, shipped-anyway,
    # or PageLimitError) turns every run into a calibration data point for
    # estimate_rendered_pages(), instead of re-deriving constants by hand
    # from a one-off incident (see PLAN.md Part 5).
    plan_estimate = estimate_rendered_pages(
        book_bible.sections, book_bible.profile, specials=book_bible.specials,
        pamphlet=target <= 15,
    )
    chapters_cur = chapters
    images_cur = all_images
    artifact: BookArtifact | None = None

    for scale_attempt in range(settings.max_page_scale_revisions + 1):
        artifact = _assemble_with_layout_revise(
            book_id, book_bible, chapters_cur, images_cur, qa_result,
            cover_key=cover_key, pro=pro, source=source, stage=stage,
        )
        pages = artifact.page_count
        if is_acceptable_page_count(pages, target):
            log.info("page_estimate_vs_actual", book_id=book_id,
                      plan_estimate=plan_estimate, actual_pages=pages,
                      target=target, outcome="in_band",
                      ratio=round(pages / max(1, plan_estimate), 2))
            return _final_vision_qa(
                book_id, artifact, book_bible, chapters_cur, images_cur,
                qa_result, cover_key=cover_key, pro=pro, source=source,
                stage=stage,
            )
        if scale_attempt >= settings.max_page_scale_revisions:
            lo, hi = allowed_page_range(target)
            ship = (
                (pages > hi and settings.ship_on_page_overshoot)
                or (pages < lo and settings.ship_on_page_undershoot)
            )
            log.info("page_estimate_vs_actual", book_id=book_id,
                      plan_estimate=plan_estimate, actual_pages=pages,
                      target=target, outcome="shipped_out_of_band" if ship else "failed",
                      ratio=round(pages / max(1, plan_estimate), 2))
            if ship:
                if pages > hi:
                    detail = (f"page count {pages} exceeds ceiling {hi} after "
                              f"{scale_attempt} trim passes — shipped anyway")
                    log.warning("page_overshoot_shipped", pages=pages, ceiling=hi)
                else:
                    detail = (f"page count {pages} is below the {lo}-page target "
                              f"after content expansion and layout padding — "
                              f"shipped anyway")
                    log.warning("page_undershoot_shipped", pages=pages, floor=lo)
                artifact.warnings.append(detail)
                artifact.degraded = True
                return _final_vision_qa(
                    book_id, artifact, book_bible, chapters_cur, images_cur,
                    qa_result, cover_key=cover_key, pro=pro, source=source,
                    stage=stage,
                )
            raise PageLimitError(
                f"page count {pages} outside allowed range {lo}–{hi} after scaling",
                pages=pages, target=target, lo=lo, hi=hi,
            )

        if pages < target:
            stage(f"page-expand ({scale_attempt + 1})")
            lo, hi = allowed_page_range(target)
            book_bible, structural_faults = _structural_expand_plan(
                book_bible, chapters_cur, target_pages=target,
            )
            noun = (
                book_bible.profile.entry_schema.entry_noun
                if book_bible.profile else "entry"
            )
            expand_faults: dict[int, list[str]] = dict(structural_faults)
            for s in book_bible.sections:
                if s.index in expand_faults:
                    continue
                expand_faults[s.index] = [
                    f"expand this section — the book must reach at least "
                    f"{lo} pages; add depth to headnotes and steps without "
                    f"removing any {noun}s",
                ]
            try:
                expanded = content.regenerate_guide_sections(
                    chapters_cur, book_bible, source, expand_faults,
                    registry_seed=chapters_cur,
                )
                # An EXPAND pass must never shrink the book. When the primary
                # model times out, llm falls back to a smaller one (observed in
                # prod: opus -> sonnet -> haiku), and smaller models write more
                # tersely — so the pass meant to add pages removed them instead
                # (49 -> 47 on book b09a09). Keep the longer draft; the
                # deterministic pad in assemble closes any remaining gap.
                before_words = _total_words(chapters_cur)
                after_words = _total_words(expanded)
                if after_words < before_words:
                    log.warning("page_expand_shrank_discarded",
                                before_words=before_words,
                                after_words=after_words)
                else:
                    chapters_cur = expanded
            except BudgetExceeded:
                log.warning("page_expand_budget_exhausted")
                lo, hi = allowed_page_range(target)
                raise PageLimitError(
                    f"budget exhausted while expanding to page target "
                    f"({pages} pages, need {lo}–{hi})",
                    pages=pages, target=target, lo=lo, hi=hi,
                )
            chapters_cur, _ = expand_chapters_for_pages(
                chapters_cur, book_bible,
                current_pages=pages, target_pages=target,
            )
            content_guide.sync_card_image_briefs(chapters_cur, book_bible)
            finalize_marketing_counts(book_bible, chapters_cur)
            checkpoints.save_bible(book_id, book_bible)
            checkpoints.save_chapters(book_id, chapters_cur)
            images_cur, cover_key = _refresh_images(
                book_id, book_bible, chapters_cur, images_cur, cover_key)
            log.info("page_scale_expand", attempt=scale_attempt + 1,
                     pages_before=pages, target=target)
            continue

        stage(f"page-scale ({scale_attempt + 1})")
        chapters_cur, removed = scale_chapters_to_pages(
            chapters_cur, book_bible,
            current_pages=pages, target_pages=target,
            scale_attempt=scale_attempt,
        )
        images_cur = scale_images_for_chapters(images_cur, chapters_cur)
        content_guide.sync_card_image_briefs(chapters_cur, book_bible)
        finalize_marketing_counts(book_bible, chapters_cur)
        checkpoints.save_bible(book_id, book_bible)
        checkpoints.save_chapters(book_id, chapters_cur)
        checkpoints.save_images(book_id, images_cur)
        # Fill any newly-needed image slots (briefs added after trim).
        images_cur, cover_key = _refresh_images(
            book_id, book_bible, chapters_cur, images_cur, cover_key)
        log.info("page_scale_trim", attempt=scale_attempt + 1,
                 removed=removed, pages_before=pages, target=target)

    raise AssertionError("unreachable")


def _assemble_narrative_with_page_scale(
    book_id: str,
    book_bible: BookBible,
    chapters: list[Chapter],
    all_images: list,
    qa_result,
) -> BookArtifact:
    """Narrative path: assemble and scale content to meet the page band."""
    target = book_bible.target_pages or 100
    chapters_cur = chapters
    images_cur = all_images
    artifact: BookArtifact | None = None

    for scale_attempt in range(settings.max_page_scale_revisions + 1):
        artifact = assemble.assemble(
            book_id, book_bible, chapters_cur, images_cur, qa_result,
        )
        pages = artifact.page_count
        if is_acceptable_page_count(pages, target):
            return artifact
        lo, _hi = allowed_page_range(target)
        if pages < lo:
            # This path can only TRIM (scale_chapters_to_pages just bumps
            # page_budget when under the floor and returns the chapters
            # untouched), so re-rendering an undershooting narrative book
            # produces a byte-identical PDF. Stop burning renders and let the
            # ship/raise policy below decide immediately.
            scale_attempt = settings.max_page_scale_revisions
        if scale_attempt >= settings.max_page_scale_revisions:
            lo, hi = allowed_page_range(target)
            ship = (
                (pages > hi and settings.ship_on_page_overshoot)
                or (pages < lo and settings.ship_on_page_undershoot)
            )
            if ship:
                if pages > hi:
                    detail = (f"page count {pages} exceeds ceiling {hi} after "
                              "scaling — shipped anyway")
                    log.warning("page_overshoot_shipped", pages=pages, ceiling=hi)
                else:
                    detail = (f"page count {pages} is below the {lo}-page "
                              "target — shipped anyway")
                    log.warning("page_undershoot_shipped", pages=pages, floor=lo)
                artifact.warnings.append(detail)
                artifact.degraded = True
                return artifact
            raise PageLimitError(
                f"page count {pages} outside allowed range {lo}–{hi} after scaling",
                pages=pages, target=target, lo=lo, hi=hi,
            )
        chapters_cur, removed = scale_chapters_to_pages(
            chapters_cur, book_bible,
            current_pages=pages, target_pages=target,
            scale_attempt=scale_attempt,
        )
        images_cur = scale_images_for_chapters(images_cur, chapters_cur)
        log.info("page_scale_trim", attempt=scale_attempt + 1,
                 removed=removed, pages_before=pages)

    raise AssertionError("unreachable")


def _budget_qa_result(note: str) -> QAResult:
    return QAResult(groundedness=0.0, passed=False, layout_issues=[note])


def plan_source(
    book_id: str, topic: str, *, text: str | None, youtube_url: str | None,
) -> SourceDoc:
    """Load-or-ingest this book's source, checkpointing on ingest."""
    source = checkpoints.load_source(book_id)
    if source is not None:
        log.info("source_checkpoint_hit", book_id=book_id,
                 passages=len(source.passages))
        return source
    source = ingest.ingest(topic, text=text, youtube_url=youtube_url)
    checkpoints.save_source(book_id, source)
    return source


def plan_bible(
    book_id: str,
    source: SourceDoc,
    *,
    target_pages: int,
    book_kind: str = "practical_guide",
    description: str | None = None,
    toc: list[str] | None = None,
    profile_overrides: dict | None = None,
    density_mode: str | None = None,
    accepted_promised_count: int | None = None,
) -> BookBible:
    """Load-or-build this book's bible, checkpointing on build."""
    bible = checkpoints.load_bible(book_id)
    if bible is not None:
        log.info("bible_checkpoint_hit", book_id=book_id)
        return bible
    bible = bible_stage.build_bible(
        source, target_pages=target_pages, book_kind=book_kind,
        description=description, toc=toc,
        profile_overrides=profile_overrides,
        density_mode=density_mode,
        accepted_promised_count=accepted_promised_count,
    )
    checkpoints.save_bible(book_id, bible)
    return bible


def plan_book(
    book_id: str,
    topic: str,
    *,
    text: str | None,
    youtube_url: str | None,
    target_pages: int,
    book_kind: str = "practical_guide",
    description: str | None = None,
    toc: list[str] | None = None,
    profile_overrides: dict | None = None,
    density_mode: str | None = None,
    accepted_promised_count: int | None = None,
) -> tuple[SourceDoc, BookBible]:
    """Ingest + build this book's bible, checkpointing both (idempotent on
    checkpoint hit — safe to call again once a job resumes).

    Used both as the synchronous pre-flight gate in api/main.py (before a job
    is created, so an infeasible plan is never even queued) and inside
    `_run_book_impl`'s cold-start path — sharing this one function means the
    gate and the real run can never drift apart."""
    source = plan_source(book_id, topic, text=text, youtube_url=youtube_url)
    bible = plan_bible(
        book_id, source, target_pages=target_pages, book_kind=book_kind,
        description=description, toc=toc,
        profile_overrides=profile_overrides,
        density_mode=density_mode,
        accepted_promised_count=accepted_promised_count,
    )
    return source, bible


def run_book(
    book_id: str,
    topic: str,
    *,
    text: str | None = None,
    youtube_url: str | None = None,
    target_pages: int = 100,
    chapter_limit: int | None = None,
    section_limit: int | None = None,
    book_kind: str = "practical_guide",
    description: str | None = None,
    toc: list[str] | None = None,
    profile_overrides: dict | None = None,
    density_mode: str | None = None,
    accepted_promised_count: int | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> BookArtifact:
    """Thin wrapper: bind this book's budget (carrying spend across resumes)
    around the actual stage sequence, and persist spend on every exit path."""
    prior_spend = checkpoints.load_spend(book_id)
    with cost.bind(book_id, initial_spent=prior_spend) as book_budget:
        try:
            return _run_book_impl(
                book_id, topic, text=text, youtube_url=youtube_url,
                target_pages=target_pages, chapter_limit=chapter_limit,
                section_limit=section_limit, book_kind=book_kind,
                description=description, toc=toc,
                profile_overrides=profile_overrides,
                density_mode=density_mode,
                accepted_promised_count=accepted_promised_count,
                on_stage=on_stage,
            )
        finally:
            checkpoints.save_spend(book_id, book_budget.spent)


def _run_book_impl(
    book_id: str,
    topic: str,
    *,
    text: str | None,
    youtube_url: str | None,
    target_pages: int,
    chapter_limit: int | None,
    section_limit: int | None,
    book_kind: str,
    description: str | None,
    toc: list[str] | None,
    profile_overrides: dict | None,
    density_mode: str | None,
    accepted_promised_count: int | None,
    on_stage: Callable[[str], None] | None,
) -> BookArtifact:
    limit = section_limit if section_limit is not None else chapter_limit
    log.info("run_start", book_id=book_id, topic=topic, limit=limit, kind=book_kind)

    # Pre-flight: verify render geometry is sound BEFORE spending any tokens on
    # content/images. A regression (e.g. the viewport/scale bug) fails here in
    # ~2s at ~$0 instead of wasting a full generation only to fail the final gate.
    # (Cached per-process — it validates the renderer, not the book.)
    from bookforge.pipeline import pdf_geometry

    preflight = pdf_geometry.preflight_render_check()
    if preflight:
        raise GeometryError(
            "Pre-flight layout self-check failed — render geometry is broken, "
            "aborting before generation (no tokens spent): " + " | ".join(preflight[:2])
        )

    from bookforge.pipeline.readiness import require_ready

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    stage_warnings: list[str] = []
    # One gate cache per run: fix-loop re-gates only re-judge changed chapters.
    gate_cache = qa.GateCache()

    cached_bible = checkpoints.load_bible(book_id)
    cached_chapters = checkpoints.load_chapters(book_id) if cached_bible else []
    resuming = bool(cached_bible and cached_chapters)

    if not resuming:
        require_ready(fresh=True)

    stage("ingest")
    source = plan_source(book_id, topic, text=text, youtube_url=youtube_url)

    if book_kind != "practical_guide":
        # ---- narrative path (bible+chapters resume parity; images/QA re-run) ----
        if resuming:
            log.info("checkpoint_resume", book_id=book_id,
                     chapters=len(cached_chapters), stage="post-content",
                     kind=book_kind)
            book_bible = cached_bible
            chapters = cached_chapters
        else:
            stage("bible")
            book_bible = plan_bible(
                book_id, source, target_pages=target_pages, book_kind=book_kind,
                description=description, toc=toc,
                profile_overrides=profile_overrides,
                density_mode=density_mode,
                accepted_promised_count=accepted_promised_count,
            )
            stage("content")
            chapters = content.write_book(book_bible, source, limit=limit)
            checkpoints.save_chapters(book_id, chapters)
        stage("images")
        all_images = []
        for ch in chapters:
            all_images.extend(images.generate_for_chapter(ch, book_bible))
        stage("qa")
        qa_result = qa.run_gate(chapters, source, book_bible.voice.banned_patterns)
        if not qa_result.passed:
            log.warning("qa_failed", grounded=qa_result.groundedness,
                        unsupported=len(qa_result.unsupported_claims))
        stage("layout")
        artifact = _assemble_narrative_with_page_scale(
            book_id, book_bible, chapters, all_images, qa_result,
        )
        artifact.warnings = stage_warnings + artifact.warnings
        artifact.degraded = artifact.degraded or bool(artifact.warnings)
        log.info("run_done", book_id=book_id, pages=artifact.page_count,
                 mb=artifact.file_size_mb,
                 qa_passed=artifact.qa.passed if artifact.qa else None)
        return artifact

    # ---- practical guide path ----
    if resuming:
        log.info("checkpoint_resume", book_id=book_id,
                 chapters=len(cached_chapters), stage="post-content",
                 kind=book_kind)
        book_bible = cached_bible
        chapters = cached_chapters
        qa_result = checkpoints.load_qa(book_id, chapters)
        if qa_result is None:
            stage("qa")
            try:
                qa_result = qa.run_guide_gate(chapters, source, book_bible, gate_cache)
                checkpoints.save_qa(book_id, chapters, qa_result)
            except BudgetExceeded:
                stage_warnings.append(
                    "budget exhausted during quality checks — shipped without full QA")
                qa_result = _budget_qa_result("qa skipped: budget exhausted")
        else:
            log.info("qa_checkpoint_hit", book_id=book_id)
    else:
        stage("bible")
        book_bible = plan_bible(
            book_id, source, target_pages=target_pages, book_kind=book_kind,
            description=description, toc=toc,
            profile_overrides=profile_overrides,
            density_mode=density_mode,
            accepted_promised_count=accepted_promised_count,
        )

        stage("content")
        partial = checkpoints.load_sections(book_id)
        if partial:
            log.info("section_checkpoints_hit", book_id=book_id,
                     sections=len(partial))
        try:
            chapters = content.write_guide(
                book_bible, source, limit=limit, existing=partial,
                on_section=lambda ch: checkpoints.save_section(book_id, ch),
            )
        except BudgetExceeded:
            # Ship whatever sections finished before the cap; nothing finished
            # means nothing shippable — propagate for a clean "budget" failure.
            partial_now = checkpoints.load_sections(book_id)
            if not partial_now:
                raise
            chapters = partial_now
            total = len(book_bible.sections[:limit] if limit else book_bible.sections)
            stage_warnings.append(
                f"budget exhausted during content — shipped {len(chapters)} "
                f"of {total} sections")
            log.warning("content_budget_partial_ship",
                        written=len(chapters), planned=total)
        stage("qa")
        try:
            qa_result = qa.run_guide_gate(chapters, source, book_bible, gate_cache)
            prev_defect_count: int | None = None
            for attempt in range(settings.max_qa_revisions):
                if qa_result.passed or not qa.deterministic_defects(qa_result):
                    break
                faults = qa.faults_by_chapter(qa_result)
                defect_count = (len(qa_result.layout_issues)
                                + len(qa_result.safety_findings))
                if not faults or defect_count == prev_defect_count:
                    break
                prev_defect_count = defect_count
                stage(f"revise ({attempt + 1})")
                log.info("guide_qa_revision", attempt=attempt + 1,
                         defects=defect_count, sections=len(faults))
                chapters = content.regenerate_guide_sections(
                    chapters, book_bible, source, faults, registry_seed=chapters)
                qa_result = qa.run_guide_gate(chapters, source, book_bible, gate_cache)

            if not qa_result.safety_passed:
                n_fixed = qa.sanitize_unsafe_steps(chapters, qa_result.safety_findings)
                log.warning("unsafe_steps_sanitized", count=n_fixed)
                qa_result = qa.run_guide_gate(chapters, source, book_bible, gate_cache)
        except BudgetExceeded:
            stage_warnings.append(
                "budget exhausted during quality checks — shipped best effort")
            qa_result = _budget_qa_result("qa incomplete: budget exhausted")
        if not qa_result.passed:
            log.warning("guide_qa_shipped_with_faults",
                        grounded=qa_result.groundedness,
                        prose=qa_result.prose_quality_score,
                        audit_issues=len(qa_result.layout_issues))
        content_guide.sync_card_image_briefs(chapters, book_bible)
        finalize_marketing_counts(book_bible, chapters)
        checkpoints.save_bible(book_id, book_bible)
        checkpoints.save_chapters(book_id, chapters)
        checkpoints.save_qa(book_id, chapters, qa_result)
        checkpoints.clear_sections(book_id)

    # Resume path: still sync briefs + marketing counts before images.
    if resuming:
        content_guide.sync_card_image_briefs(chapters, book_bible)
        finalize_marketing_counts(book_bible, chapters)
        checkpoints.save_bible(book_id, book_bible)
        checkpoints.save_chapters(book_id, chapters)
    stage("images")
    try:
        all_images, cover_key = guide_images.generate_all(
            book_bible, chapters, book_id, warnings=stage_warnings,
        )
    except BudgetExceeded:
        stage_warnings.append(
            "budget exhausted during image creation — shipped with the "
            "images generated so far")
        all_images = checkpoints.load_images(book_id)
        cover_path = f"books/{book_id}/cover.jpg"
        cover_key = cover_path if checkpoints.has_image(cover_path) else None
    checkpoints.save_images(book_id, all_images)
    try:
        pro = content_guide.make_pro(book_bible.pro_topic, book_bible)
    except BudgetExceeded:
        log.warning("pro_page_skipped", err="budget exhausted")
        pro = None
    except Exception as exc:
        if llm.never_swallow(exc):
            raise
        log.warning("pro_page_skipped", err=str(exc)[:200])
        pro = None
    stage("layout")
    checkpoints.clear_pdf(book_id)
    artifact = _assemble_with_page_scale(
        book_id, book_bible, chapters, all_images, qa_result,
        cover_key=cover_key, pro=pro, source=source, stage=stage,
    )

    artifact.warnings = stage_warnings + artifact.warnings
    artifact.degraded = artifact.degraded or bool(artifact.warnings)
    log.info("run_done", book_id=book_id, pages=artifact.page_count,
             mb=artifact.file_size_mb, warnings=len(artifact.warnings),
             qa_passed=artifact.qa.passed if artifact.qa else None)
    return artifact
