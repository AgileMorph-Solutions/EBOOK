"""Vision layout QA — catch visual bugs on rendered guide page PNGs.

Samples key pages (dividers, first cards, evenly spaced) and asks a vision model
to flag overflow, bleed, wrong crops, and theme mismatches before PDF ships.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from bookforge.clients import llm
from bookforge.config import settings
from bookforge.layout import render
from bookforge.schemas import BookBible

log = structlog.get_logger(__name__)


class LayoutVerdict(BaseModel):
    passed: bool = True
    issues: list[str] = Field(default_factory=list)
    page_number: int = 0


_PROMPT = """You are a print production QA inspector reviewing a page from \
"{title}" — a {genre} for {audience} (large print, card layout).

Theme: {theme}

Check for these VISUAL defects only:
- Text bleeding onto photos or clipped at page edges
- A word broken across two lines WITHOUT a hyphen (e.g. "Rebuildi" / "ng")
- Title overlapping image on section divider pages
- Image stretched, squashed, or letterboxed inside a rounded card frame
- Important photo subject cropped off-center or cut off
- Divider photo visible under the text panel
- Unreadable text (too small or low contrast)
- Wrong color theme for the topic (e.g. cleaning teal on a food book)
- Large empty vertical band (more than ~25% of the body area) between the page
  heading and the main content block, especially on pages marked "(continued)"
- Table or chart rows bunched at the top with a large empty area below them
  (underfilled reference table)

Do NOT evaluate whether page numbers, a table of contents, or a reference
list are numerically "in order" or "sequential" — folio numbers are computed
programmatically and are always correct; a table of contents legitimately
lists multiple independent groups (e.g. "Chapters" and "Reference") whose
page numbers naturally interleave since those pages are physically
distributed throughout the book. This is normal book structure, NOT a defect
— never flag it.

Be strict about VISUAL print defects only. Return passed=false if ANY serious
visual layout defect is visible. List specific issues found. If the page
looks print-ready, return passed=true."""


def _pick_pages(total: int, section_count: int, sample: int,
                must: set[int] | None = None) -> list[int]:
    """Always include `must` pages (all dividers/cover/safety, from the render
    page map) + first body pages; sample the rest."""
    picks: set[int] = set(p for p in (must or set()) if 1 <= p <= total)
    if total <= 0:
        return []
    # front matter + early section pages
    for p in (1, 2, 3, 4, 5, 6, 7, 8):
        if p <= total:
            picks.add(p)
    # evenly spaced sample
    if total > sample:
        step = max(1, total // sample)
        for p in range(step, total + 1, step):
            picks.add(p)
    else:
        picks.update(range(1, total + 1))
    return sorted(picks)


# page classes that must ALWAYS be vision-inspected: the highest-risk templates
# (full-bleed art + fitted display type).
_MUST_CHECK_CLASSES = ("gcover", "gdivider", "gsafety", "gtitle")


def _must_check_pages(page_classes: list[str]) -> set[int]:
    return {i for i, cls in enumerate(page_classes, 1)
            if any(k in cls for k in _MUST_CHECK_CLASSES)}


def _sniff_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/png"


def check_page(png: Path, page_num: int, bible: BookBible) -> LayoutVerdict:
    """Vision-inspect one page. FAIL CLOSED: if the vision call errors (after one
    retry), raise — the caller records the page as NOT inspected instead of
    silently passing a possibly-broken page."""
    data = png.read_bytes()
    from bookforge.schemas import profile_of

    _p = profile_of(bible)
    prompt = _PROMPT.format(title=bible.title, genre=_p.genre,
                            audience=_p.audience,
                            theme=bible.theme_name or "default")
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            verdict = llm.vision_json(
                prompt, data, LayoutVerdict,
                media_type=_sniff_media_type(data),
            )
            verdict.page_number = page_num
            return verdict
        except Exception as exc:
            last_exc = exc
            log.warning("layout_qa_page_failed", page=page_num,
                        attempt=attempt, err=str(exc)[:200])
    raise last_exc  # type: ignore[misc]


def _check_pages(page_pngs: dict[int, Path], bible: BookBible) -> list[str]:
    """Vision-check the given pages concurrently. Fail CLOSED per page: an
    uninspected page is reported as "(skipped)", never silently passed."""
    from bookforge.pipeline.pools import ctx_pool

    issues: list[str] = []
    skipped = 0

    def _one(page_num: int, path: Path) -> tuple[int, LayoutVerdict | None, str]:
        try:
            return page_num, check_page(path, page_num, bible), ""
        except Exception as exc:  # noqa: BLE001 — recorded as skipped
            if llm.never_swallow(exc):
                raise
            return page_num, None, str(exc)[:120]

    ordered = sorted(page_pngs.items())
    with ctx_pool(settings.vision_qa_concurrency, "visionqa") as pool:
        results = list(pool.map(lambda item: _one(*item), ordered))
    for page_num, verdict, err in results:
        if verdict is None:
            skipped += 1
            issues.append(f"page {page_num} (skipped): vision QA "
                          f"unavailable: {err}")
        elif not verdict.passed:
            issues.extend(f"page {page_num}: {i}" for i in verdict.issues)
        elif verdict.issues:
            issues.extend(f"page {page_num} (info): {i}" for i in verdict.issues)

    log.info("layout_qa_done", pages_checked=len(ordered),
             issues=len(issues), skipped=skipped)
    return issues


def run_layout_qa_pdf(
    pdf: bytes,
    bible: BookBible,
    *,
    page_classes: list[str] | None = None,
    section_count: int = 0,
) -> list[str]:
    """Vision-check the SHIPPED PDF: rasterize the sampled pages via pypdfium2
    (no second Chromium render — and it inspects the exact bytes that ship).
    `page_classes` (from the render's fit report) pins every cover/divider/
    safety page into the sample."""
    if not settings.layout_qa_enabled:
        return []
    if not settings.anthropic_api_key:
        log.info("layout_qa_skipped", reason="no_api_key")
        return []
    from bookforge.pipeline import pdf_geometry

    total = pdf_geometry.pdf_page_count(pdf)
    if total <= 0:
        return ["layout QA (skipped): could not open the PDF for inspection"]
    must = _must_check_pages(page_classes or [])
    if section_count and section_count <= 3:
        to_check = list(range(1, total + 1))
    else:
        to_check = _pick_pages(total, section_count,
                               settings.layout_qa_sample_pages, must)
    with tempfile.TemporaryDirectory(prefix="layout_qa_") as tmp:
        pngs = pdf_geometry.rasterize_pages(pdf, to_check, dpi=108, out_dir=tmp)
        if not pngs:
            return ["layout QA (skipped): PDF rasterization unavailable"]
        return _check_pages(pngs, bible)


def run_layout_qa(
    html: str,
    bible: BookBible,
    *,
    section_count: int = 0,
) -> list[str]:
    """Legacy HTML path: render page PNGs and vision-check a sample."""
    if not settings.layout_qa_enabled:
        return []
    if not settings.anthropic_api_key:
        log.info("layout_qa_skipped", reason="no_api_key")
        return []

    with tempfile.TemporaryDirectory(prefix="layout_qa_") as tmp:
        paths, report = render.render_pngs(html, tmp, scale=1.5)
        if not paths:
            return []
        # render_pngs ran the fit pass, so the report carries the page map —
        # every cover/divider/safety page is inspected, never sampled away.
        must = _must_check_pages((report or {}).get("pages", []))
        sample_n = settings.layout_qa_sample_pages
        if section_count and section_count <= 3:
            to_check = list(range(1, len(paths) + 1))
        else:
            to_check = _pick_pages(len(paths), section_count, sample_n, must)
        pngs = {p: paths[p - 1] for p in to_check if p <= len(paths)}
        return _check_pages(pngs, bible)
