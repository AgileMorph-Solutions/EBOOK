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


def run_layout_qa(
    html: str,
    bible: BookBible,
    *,
    section_count: int = 0,
) -> list[str]:
    """Render page PNGs and vision-check a sample. Returns issue strings."""
    if not settings.layout_qa_enabled:
        return []
    if not settings.anthropic_api_key:
        log.info("layout_qa_skipped", reason="no_api_key")
        return []

    with tempfile.TemporaryDirectory(prefix="layout_qa_") as tmp:
        paths = render.html_to_pngs(html, tmp, scale=1.5)
        if not paths:
            return []
        # html_to_pngs ran the fit pass, so the report carries the page map —
        # every cover/divider/safety page is inspected, never sampled away.
        report = render.last_fit_report or {}
        must = _must_check_pages(report.get("pages", []))
        sample_n = settings.layout_qa_sample_pages
        if section_count and section_count <= 3:
            to_check = list(range(1, len(paths) + 1))
        else:
            to_check = _pick_pages(len(paths), section_count, sample_n, must)

        issues: list[str] = []
        skipped = 0
        for page_num in to_check:
            if page_num > len(paths):
                continue
            try:
                v = check_page(paths[page_num - 1], page_num, bible)
            except Exception as exc:
                # fail CLOSED: an uninspected page is reported, not passed
                skipped += 1
                issues.append(f"page {page_num} (skipped): vision QA "
                              f"unavailable: {str(exc)[:120]}")
                continue
            if not v.passed:
                for issue in v.issues:
                    issues.append(f"page {page_num}: {issue}")
            elif v.issues:
                for issue in v.issues:
                    issues.append(f"page {page_num} (info): {issue}")

        log.info("layout_qa_done", pages_checked=len(to_check),
                 issues=len(issues), skipped=skipped)
        return issues
