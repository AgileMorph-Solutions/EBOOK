"""Stage 6 — assembly, file-size preflight, delivery.

Renders the print HTML to PDF, linearizes it, checks page count and the
file-size budget, and stores the final interior PDF. (CMYK/PDF-X conversion via
Ghostscript runs in the `pdftools` Docker service; the spike ships the RGB PDF
KDP accepts.)
"""
from __future__ import annotations

import io

import structlog

from bookforge import safety
from bookforge.clients import storage
from bookforge.config import settings
from bookforge.layout import build, guide, render
from bookforge.pipeline import layout_qa
from bookforge.schemas import BookArtifact, BookBible, Chapter, GeneratedImage, QAResult

log = structlog.get_logger(__name__)


def _page_count(pdf: bytes) -> int:
    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(pdf)) as doc:
            return len(doc.pages)
    except Exception:
        return pdf.count(b"/Type /Page") or pdf.count(b"/Type/Page")


def _linearize(pdf: bytes) -> bytes:
    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(pdf)) as doc:
            out = io.BytesIO()
            doc.save(out, linearize=True, compress_streams=True)
            return out.getvalue()
    except Exception as exc:
        log.warning("linearize_skipped", err=str(exc))
        return pdf


def assemble(
    book_id: str,
    bible: BookBible,
    chapters: list[Chapter],
    images: list[GeneratedImage],
    qa: QAResult,
    *,
    cover_key: str | None = None,
    pro: dict | None = None,
) -> BookArtifact:
    if bible.book_kind == "practical_guide":
        how_to = bible.how_to.model_dump() if bible.how_to else None
        html = guide.build_practical_html(
            bible, chapters, images, cover_key=cover_key,
            how_to=how_to,
            safety={"never_mix": safety.never_mix_for(bible.safety_key)},
            pro=pro,
        )
        budget_pages = sum(p.page_budget for p in bible.sections)
        layout_issues = layout_qa.run_layout_qa(
            html, bible, section_count=len(bible.sections),
        )
        qa.layout_issues.extend(layout_issues)
        if settings.layout_qa_block and layout_issues:
            qa.passed = False
    else:
        html = build.build_html(bible, chapters, images)
        budget_pages = sum(p.page_budget for p in bible.chapters)

    pdf = _linearize(render.html_to_pdf(html))

    size_mb = len(pdf) / 1_048_576
    pages = _page_count(pdf)
    issues = list(qa.layout_issues)
    if size_mb > settings.max_file_size_mb:
        issues.append(
            f"file {size_mb:.1f}MB exceeds budget {settings.max_file_size_mb}MB "
            "— recompress images"
        )
    if budget_pages:
        drift = abs(pages - budget_pages) / budget_pages
        log.info("page_budget_check", pages=pages, budget=budget_pages,
                 drift=round(drift, 2))
        if drift > 0.30:
            issues.append(
                f"page count {pages} drifts {drift*100:.0f}% from budget "
                f"{budget_pages} — review chapter sizing"
            )

    key = f"books/{book_id}/interior.pdf"
    storage.put(key, pdf, "application/pdf")
    qa.layout_issues = issues

    art = BookArtifact(book_id=book_id, interior_pdf_key=key, page_count=pages,
                       file_size_mb=round(size_mb, 2), qa=qa)
    log.info("assembled", book_id=book_id, pages=pages, mb=art.file_size_mb,
             kind=bible.book_kind)
    return art
