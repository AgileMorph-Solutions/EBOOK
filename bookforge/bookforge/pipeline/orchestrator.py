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
from bookforge.errors import GeometryError, LayoutError
from bookforge.pipeline import (
    assemble, bible as bible_stage, content, content_guide, guide_images, images, ingest,
    layout_budget, qa,
)
from bookforge.schemas import BookArtifact, BookBible, Chapter

log = structlog.get_logger(__name__)


def _layout_faults(defects: list[dict], chapters: list[Chapter]) -> dict[int, list[str]]:
    """Map audit overflow records to per-chapter rewrite instructions.

    Keys look like "{ch}.{bi}" (entry), "essay:{ch}", "feature:{ch}.{bi}" —
    anything with a parsable chapter index gets a concrete length budget so the
    regeneration converges instead of guessing."""
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
        else:
            faults.setdefault(idx, []).append(
                f"the entry starting '{snippet}' cannot fit even a multi-page "
                "layout — rewrite it tighter: intro ≤ 25 words, at most 5 steps "
                "of ≤ 18 words each, ≤ 8 what_you_need items, variation/note "
                "≤ 12 words each; keep every quantity and safety detail")
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
            if not faults or attempt >= settings.max_layout_revisions:
                raise
            stage(f"layout-revise ({attempt + 1})")
            log.warning("layout_revise", attempt=attempt + 1,
                        chapters=sorted(faults), issues=sum(map(len, faults.values())))
            chapters = content.regenerate_guide_sections(
                chapters, book_bible, source, faults, registry_seed=chapters)
            chapters = layout_budget.trim_chapters(chapters, book_bible)
    raise AssertionError("unreachable")  # loop always returns or raises


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

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    stage("ingest")
    source = ingest.ingest(topic, text=text, youtube_url=youtube_url)

    stage("bible")
    book_bible = bible_stage.build_bible(
        source, target_pages=target_pages, book_kind=book_kind,
        profile_overrides=profile_overrides,
    )

    if book_bible.book_kind == "practical_guide":
        stage("content")
        chapters = content.write_guide(book_bible, source, limit=limit)
        # Content QA runs BEFORE images: a defective book (missing quantities,
        # unsafe steps, mass duplication) must not spend the image budget.
        stage("qa")
        qa_result = qa.run_guide_gate(chapters, source, book_bible)
        # Fix loop: when the gate finds actionable defects, feed them back into
        # the writer and regenerate ONLY the offending sections, then re-check.
        # We always ship a book — never fail on quality — so this iterates for
        # quality, but the loop exits (and the book ships) either way.
        prev_defect_count: int | None = None
        for attempt in range(settings.max_qa_revisions):
            if qa_result.passed or not qa.deterministic_defects(qa_result):
                break
            faults = qa.faults_by_chapter(qa_result)
            defect_count = (len(qa_result.layout_issues)
                            + len(qa_result.safety_findings))
            if not faults or defect_count == prev_defect_count:
                break  # nothing actionable, or the last pass stopped improving
            prev_defect_count = defect_count
            stage(f"revise ({attempt + 1})")
            log.info("guide_qa_revision", attempt=attempt + 1,
                     defects=defect_count, sections=len(faults))
            chapters = content.regenerate_guide_sections(
                chapters, book_bible, source, faults, registry_seed=chapters)
            qa_result = qa.run_guide_gate(chapters, source, book_bible)

        # Ship no matter what. Any step still flagged unsafe after the loop is
        # neutralized so a dangerous instruction never ships; remaining cosmetic
        # defects ship best-effort with a warning.
        if not qa_result.safety_passed:
            n_fixed = qa.sanitize_unsafe_steps(chapters, qa_result.safety_findings)
            log.warning("unsafe_steps_sanitized", count=n_fixed)
            qa_result = qa.run_guide_gate(chapters, source, book_bible)
        if not qa_result.passed:
            log.warning("guide_qa_shipped_with_faults",
                        grounded=qa_result.groundedness,
                        prose=qa_result.prose_quality_score,
                        audit_issues=len(qa_result.layout_issues))
        stage("images")
        all_images, cover_key = guide_images.generate_all(
            book_bible, chapters, book_id,
        )
        # The back-matter page is an optional extra — never let it sink the
        # book (provider-wide failures still propagate to park).
        try:
            pro = content_guide.make_pro(book_bible.pro_topic, book_bible)
        except Exception as exc:
            if llm.is_provider_unavailable(exc):
                raise
            log.warning("pro_page_skipped", err=str(exc)[:200])
            pro = None
        stage("layout")
        artifact = _assemble_with_layout_revise(
            book_id, book_bible, chapters, all_images, qa_result,
            cover_key=cover_key, pro=pro, source=source, stage=stage,
        )
    else:
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
        artifact = assemble.assemble(book_id, book_bible, chapters, all_images, qa_result)

    log.info("run_done", book_id=book_id, pages=artifact.page_count,
             mb=artifact.file_size_mb, qa_passed=artifact.qa.passed if artifact.qa else None)
    return artifact
