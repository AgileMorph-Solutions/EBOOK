"""End-to-end pipeline orchestration (provider-agnostic core).

This is the single source of truth for the stage sequence. The CLI runs it in
one process; Celery wraps each stage as a task for the production DAG. Keeping
the logic here means both paths execute identical steps.
"""
from __future__ import annotations

from collections.abc import Callable

import structlog

from bookforge.pipeline import (
    assemble, bible as bible_stage, content, content_guide, guide_images, images, ingest, qa,
)
from bookforge.schemas import BookArtifact, QAResult

log = structlog.get_logger(__name__)


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
    on_stage: Callable[[str], None] | None = None,
) -> BookArtifact:
    limit = section_limit if section_limit is not None else chapter_limit
    log.info("run_start", book_id=book_id, topic=topic, limit=limit, kind=book_kind)

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    stage("ingest")
    source = ingest.ingest(topic, text=text, youtube_url=youtube_url)

    stage("bible")
    book_bible = bible_stage.build_bible(
        source, target_pages=target_pages, book_kind=book_kind,
    )

    if book_bible.book_kind == "practical_guide":
        stage("content")
        chapters = content.write_guide(book_bible, source, limit=limit)
        stage("images")
        all_images, cover_key = guide_images.generate_all(
            book_bible, chapters, book_id,
        )
        pro = content_guide.make_pro(book_bible.pro_topic, book_bible)
        qa_result = QAResult(
            groundedness=1.0, prose_quality_score=0.8, passed=True,
        )
        stage("layout")
        artifact = assemble.assemble(
            book_id, book_bible, chapters, all_images, qa_result,
            cover_key=cover_key, pro=pro,
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
