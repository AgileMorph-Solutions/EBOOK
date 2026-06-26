"""End-to-end pipeline orchestration (provider-agnostic core).

This is the single source of truth for the stage sequence. The CLI runs it in
one process; Celery wraps each stage as a task for the production DAG. Keeping
the logic here means both paths execute identical steps.
"""
from __future__ import annotations

import structlog

from bookforge.pipeline import assemble, bible as bible_stage, content, images, ingest, qa
from bookforge.schemas import BookArtifact

log = structlog.get_logger(__name__)


def run_book(
    book_id: str,
    topic: str,
    *,
    text: str | None = None,
    youtube_url: str | None = None,
    target_pages: int = 100,
    chapter_limit: int | None = None,  # Phase-0 spike: just N chapters
) -> BookArtifact:
    log.info("run_start", book_id=book_id, topic=topic, limit=chapter_limit)

    # 0. ingest
    source = ingest.ingest(topic, text=text, youtube_url=youtube_url)

    # 1. book bible
    book_bible = bible_stage.build_bible(source, target_pages=target_pages)

    # 2. content (chapter-wise, novelist-grade)
    chapters = content.write_book(book_bible, source, limit=chapter_limit)

    # 3. images (style-locked, per chapter)
    all_images = []
    for ch in chapters:
        all_images.extend(images.generate_for_chapter(ch, book_bible))

    # 5. QA gate (blocking on groundedness)
    qa_result = qa.run_gate(chapters, source, book_bible.voice.banned_patterns)
    if not qa_result.passed:
        log.warning("qa_failed", grounded=qa_result.groundedness,
                    unsupported=len(qa_result.unsupported_claims))
        # In production this loops back to content rewrite; the spike surfaces
        # the issues in the artifact for human review.

    # 4 + 6. layout + assemble
    artifact = assemble.assemble(book_id, book_bible, chapters, all_images, qa_result)
    log.info("run_done", book_id=book_id, pages=artifact.page_count,
             mb=artifact.file_size_mb, qa_passed=qa_result.passed)
    return artifact
