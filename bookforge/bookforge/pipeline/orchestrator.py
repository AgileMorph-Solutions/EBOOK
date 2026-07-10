"""End-to-end pipeline orchestration (provider-agnostic core).

This is the single source of truth for the stage sequence. The CLI runs it in
one process; Celery wraps each stage as a task for the production DAG. Keeping
the logic here means both paths execute identical steps.
"""
from __future__ import annotations

from collections.abc import Callable

import structlog

from bookforge.clients import llm
from bookforge.config import settings
from bookforge.errors import GeometryError, LayoutError, PageLimitError
from bookforge.pipeline import (
    assemble, bible as bible_stage, checkpoints, content, content_guide, guide_images,
    images, ingest, layout_budget, qa,
)
from bookforge.pipeline.content_guardrails import finalize_marketing_counts
from bookforge.pipeline.page_limits import (
    allowed_page_range,
    expand_chapters_for_pages,
    hard_page_ceiling,
    is_acceptable_page_count,
    is_within_page_range,
    meets_page_floor,
    scale_chapters_to_pages,
    scale_images_for_chapters,
)
from bookforge.schemas import BlockType, BookArtifact, BookBible, Chapter

log = structlog.get_logger(__name__)


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
    assemble again (bounded by MAX_LAYOUT_REVISIONS) — a quality-gate defect
    triggers repair, not a dead job."""
    for attempt in range(settings.max_layout_revisions + 1):
        try:
            return assemble.assemble(
                book_id, book_bible, chapters, all_images, qa_result,
                cover_key=cover_key, pro=pro,
            )
        except LayoutError as exc:
            faults = _layout_faults(exc.defects, chapters)
            if not faults:
                faults = _vision_layout_faults(str(exc), book_bible, chapters)
            if not faults or attempt >= settings.max_layout_revisions:
                raise
            stage(f"layout-revise ({attempt + 1})")
            log.warning("layout_revise", attempt=attempt + 1,
                        chapters=sorted(faults), issues=sum(map(len, faults.values())))
            chapters = content.regenerate_guide_sections(
                chapters, book_bible, source, faults, registry_seed=chapters)
            chapters = layout_budget.trim_chapters(chapters, book_bible)
    raise AssertionError("unreachable")  # loop always returns or raises


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
    """Assemble with layout repair; scale content to meet page floor/ceiling."""
    target = book_bible.target_pages or 100
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
            return artifact
        if scale_attempt >= settings.max_page_scale_revisions:
            hi = hard_page_ceiling(target)
            if pages > hi:
                lo, _ = allowed_page_range(target)
                raise PageLimitError(
                    f"page count {pages} exceeds hard maximum {hi} after scaling",
                    pages=pages, target=target, lo=lo, hi=hi,
                )
            log.warning(
                "page_scale_undershoot",
                pages=pages, target=target,
                msg="shipping below target after expand attempts",
            )
            return artifact

        if pages < target:
            stage(f"page-expand ({scale_attempt + 1})")
            expand_faults = {
                s.index: [
                    f"expand this section — the book must reach at least "
                    f"{target} pages; add depth to headnotes and steps without "
                    f"removing any {book_bible.profile.entry_schema.entry_noun if book_bible.profile else 'entry'}s",
                ]
                for s in book_bible.sections
            }
            chapters_cur = content.regenerate_guide_sections(
                chapters_cur, book_bible, source, expand_faults,
                registry_seed=chapters_cur,
            )
            chapters_cur, _ = expand_chapters_for_pages(
                chapters_cur, book_bible,
                current_pages=pages, target_pages=target,
            )
            content_guide.sync_card_image_briefs(chapters_cur, book_bible)
            finalize_marketing_counts(book_bible, chapters_cur)
            checkpoints.save_bible(book_id, book_bible)
            checkpoints.save_chapters(book_id, chapters_cur)
            images_cur, cover_key = guide_images.generate_all(
                book_bible, chapters_cur, book_id,
            )
            checkpoints.save_images(book_id, images_cur)
            log.info("page_scale_expand", attempt=scale_attempt + 1,
                     pages_before=pages, target=target)
            continue

        stage(f"page-scale ({scale_attempt + 1})")
        chapters_cur, removed = scale_chapters_to_pages(
            chapters_cur, book_bible,
            current_pages=pages, target_pages=target,
        )
        images_cur = scale_images_for_chapters(images_cur, chapters_cur)
        content_guide.sync_card_image_briefs(chapters_cur, book_bible)
        finalize_marketing_counts(book_bible, chapters_cur)
        checkpoints.save_bible(book_id, book_bible)
        checkpoints.save_chapters(book_id, chapters_cur)
        checkpoints.save_images(book_id, images_cur)
        # Fill any newly-needed image slots (briefs added after trim).
        images_cur, cover_key = guide_images.generate_all(
            book_bible, chapters_cur, book_id,
        )
        checkpoints.save_images(book_id, images_cur)
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
    """Narrative path: assemble and scale content if page count overshoots."""
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
        if scale_attempt >= settings.max_page_scale_revisions:
            hi = hard_page_ceiling(target)
            if pages > hi:
                lo, _ = allowed_page_range(target)
                raise PageLimitError(
                    f"page count {pages} exceeds hard maximum {hi} after scaling",
                    pages=pages, target=target, lo=lo, hi=hi,
                )
            log.warning(
                "page_scale_undershoot",
                pages=pages, target=target,
                msg="shipping below target after scaling",
            )
            return artifact
        chapters_cur, removed = scale_chapters_to_pages(
            chapters_cur, book_bible,
            current_pages=pages, target_pages=target,
        )
        images_cur = scale_images_for_chapters(images_cur, chapters_cur)
        log.info("page_scale_trim", attempt=scale_attempt + 1,
                 removed=removed, pages_before=pages)

    raise AssertionError("unreachable")


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
    on_stage: Callable[[str], None] | None = None,
) -> BookArtifact:
    limit = section_limit if section_limit is not None else chapter_limit
    log.info("run_start", book_id=book_id, topic=topic, limit=limit, kind=book_kind)

    # Pre-flight: verify render geometry is sound BEFORE spending any tokens on
    # content/images. A regression (e.g. the viewport/scale bug) fails here in
    # ~2s at ~$0 instead of wasting a full generation only to fail the final gate.
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

    cached_bible = checkpoints.load_bible(book_id)
    cached_chapters = checkpoints.load_chapters(book_id) if cached_bible else []
    resuming = bool(cached_bible and cached_chapters and book_kind == "practical_guide")

    if not resuming:
        require_ready(fresh=True)

    stage("ingest")
    source = ingest.ingest(topic, text=text, youtube_url=youtube_url)

    if resuming:
        log.info("checkpoint_resume", book_id=book_id,
                 chapters=len(cached_chapters), stage="post-content")
        book_bible = cached_bible
        chapters = cached_chapters
        qa_result = qa.run_guide_gate(chapters, source, book_bible)
    else:
        stage("bible")
        book_bible = bible_stage.build_bible(
            source, target_pages=target_pages, book_kind=book_kind,
            description=description, toc=toc,
            profile_overrides=profile_overrides,
        )
        checkpoints.save_bible(book_id, book_bible)

        if book_kind != "practical_guide":
            stage("content")
            chapters = content.write_book(book_bible, source, limit=limit)
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
            log.info("run_done", book_id=book_id, pages=artifact.page_count,
                     mb=artifact.file_size_mb,
                     qa_passed=artifact.qa.passed if artifact.qa else None)
            return artifact

        stage("content")
        chapters = content.write_guide(book_bible, source, limit=limit)
        stage("qa")
        qa_result = qa.run_guide_gate(chapters, source, book_bible)
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
            qa_result = qa.run_guide_gate(chapters, source, book_bible)

        if not qa_result.safety_passed:
            n_fixed = qa.sanitize_unsafe_steps(chapters, qa_result.safety_findings)
            log.warning("unsafe_steps_sanitized", count=n_fixed)
            qa_result = qa.run_guide_gate(chapters, source, book_bible)
        if not qa_result.passed:
            log.warning("guide_qa_shipped_with_faults",
                        grounded=qa_result.groundedness,
                        prose=qa_result.prose_quality_score,
                        audit_issues=len(qa_result.layout_issues))
        content_guide.sync_card_image_briefs(chapters, book_bible)
        finalize_marketing_counts(book_bible, chapters)
        checkpoints.save_bible(book_id, book_bible)
        checkpoints.save_chapters(book_id, chapters)

    if book_kind == "practical_guide":
        # Resume path: still sync briefs + marketing counts before images.
        if resuming:
            content_guide.sync_card_image_briefs(chapters, book_bible)
            finalize_marketing_counts(book_bible, chapters)
            checkpoints.save_bible(book_id, book_bible)
            checkpoints.save_chapters(book_id, chapters)
        stage("images")
        all_images, cover_key = guide_images.generate_all(
            book_bible, chapters, book_id,
        )
        checkpoints.save_images(book_id, all_images)
        try:
            pro = content_guide.make_pro(book_bible.pro_topic, book_bible)
        except Exception as exc:
            if llm.is_provider_unavailable(exc):
                raise
            log.warning("pro_page_skipped", err=str(exc)[:200])
            pro = None
        stage("layout")
        checkpoints.clear_pdf(book_id)
        artifact = _assemble_with_page_scale(
            book_id, book_bible, chapters, all_images, qa_result,
            cover_key=cover_key, pro=pro, source=source, stage=stage,
        )
    else:
        raise AssertionError("unreachable")

    log.info("run_done", book_id=book_id, pages=artifact.page_count,
             mb=artifact.file_size_mb, qa_passed=artifact.qa.passed if artifact.qa else None)
    return artifact
