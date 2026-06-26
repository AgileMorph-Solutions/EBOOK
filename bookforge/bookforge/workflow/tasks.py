"""Celery tasks — the chord/chain DAG.

Shape:
    ingest + bible           (coordinator, cheap, synchronous)
        |
    chord( per-chapter: write_chapter -> generate_images )   <-- fan-out
        |
    assemble_book            (barrier callback: QA gate + layout + PDF)

Each task is idempotent and keyed by book_id; results are validated Pydantic
models passed as dicts (Celery's JSON serializer).
"""
from __future__ import annotations

import structlog
from celery import chord

from bookforge.pipeline import (
    assemble as assemble_stage,
    bible as bible_stage,
    content,
    images as images_stage,
    ingest as ingest_stage,
    qa as qa_stage,
)
from bookforge.schemas import (
    BookBible,
    Chapter,
    ChapterPlan,
    GeneratedImage,
    SourceDoc,
)
from bookforge.workflow.celery_app import app

log = structlog.get_logger(__name__)


@app.task(bind=True, max_retries=3, name="bookforge.run_book")
def run_book(self, book_id, topic, text=None, youtube_url=None,
             target_pages=100, chapter_limit=None):
    """Coordinator: ingest + bible, then fan out chapters as a chord."""
    source = ingest_stage.ingest(topic, text=text, youtube_url=youtube_url)
    bible = bible_stage.build_bible(source, target_pages=target_pages)

    plans = bible.chapters[:chapter_limit] if chapter_limit else bible.chapters
    src_d, bible_d = source.model_dump(), bible.model_dump()

    header = [
        write_and_illustrate.s(plan.model_dump(), bible_d, src_d)
        for plan in plans
    ]
    callback = assemble_book.s(book_id, bible_d, src_d)
    chord(header)(callback)
    return {"book_id": book_id, "chapters": len(plans), "status": "dispatched"}


@app.task(bind=True, max_retries=3, name="bookforge.content.write_and_illustrate")
def write_and_illustrate(self, plan_d, bible_d, source_d):
    """Per-chapter chain collapsed into one task: write -> images.

    (Running as a chord member; the running-summary coherence pass is applied in
    the single-process orchestrator. Here chapters write in parallel, so we pass
    an empty running summary and rely on the Book Bible + dedup QA for coherence.)
    """
    bible = BookBible.model_validate(bible_d)
    source = SourceDoc.model_validate(source_d)
    plan = ChapterPlan.model_validate(plan_d)

    chapter = content.write_chapter(plan, bible, source, running_summary="")
    imgs = images_stage.generate_for_chapter(chapter, bible)
    return {"chapter": chapter.model_dump(),
            "images": [i.model_dump() for i in imgs]}


@app.task(bind=True, name="bookforge.assemble_book")
def assemble_book(self, results, book_id, bible_d, source_d):
    """Barrier: QA gate + layout + PDF once all chapters are done."""
    bible = BookBible.model_validate(bible_d)
    source = SourceDoc.model_validate(source_d)
    results = [r for r in results if r]
    results.sort(key=lambda r: r["chapter"]["index"])

    chapters = [Chapter.model_validate(r["chapter"]) for r in results]
    images = [GeneratedImage.model_validate(i) for r in results for i in r["images"]]

    qa_result = qa_stage.run_gate(chapters, source, bible.voice.banned_patterns)
    artifact = assemble_stage.assemble(book_id, bible, chapters, images, qa_result)
    return artifact.model_dump()
