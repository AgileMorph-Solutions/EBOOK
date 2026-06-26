"""Stage 6 — assembly, file-size preflight, delivery.

Renders the print HTML to PDF, linearizes it, checks page count and the
file-size budget, and stores the final interior PDF. (CMYK/PDF-X conversion via
Ghostscript runs in the `pdftools` Docker service; the spike ships the RGB PDF
KDP accepts.)
"""
from __future__ import annotations

import io

import structlog

from bookforge.clients import storage
from bookforge.config import settings
from bookforge.layout import build, render
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
) -> BookArtifact:
    html = build.build_html(bible, chapters, images)
    pdf = _linearize(render.html_to_pdf(html))

    size_mb = len(pdf) / 1_048_576
    pages = _page_count(pdf)
    issues = list(qa.layout_issues)
    if size_mb > settings.max_file_size_mb:
        issues.append(
            f"file {size_mb:.1f}MB exceeds budget {settings.max_file_size_mb}MB "
            "— recompress images"
        )

    key = f"books/{book_id}/interior.pdf"
    storage.put(key, pdf, "application/pdf")
    qa.layout_issues = issues

    art = BookArtifact(book_id=book_id, interior_pdf_key=key, page_count=pages,
                       file_size_mb=round(size_mb, 2), qa=qa)
    log.info("assembled", book_id=book_id, pages=pages, mb=art.file_size_mb)
    return art
