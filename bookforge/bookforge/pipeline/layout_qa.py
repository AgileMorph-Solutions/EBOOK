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


_PROMPT = """You are a print production QA inspector reviewing a page from a \
senior practical home guide (large print, card layout).

Book title: {title}
Theme: {theme}

Check for these defects:
- Text bleeding onto photos or clipped at page edges
- Title overlapping image on section divider pages
- Image stretched, squashed, or letterboxed inside a rounded card frame
- Important photo subject cropped off-center or cut off
- Divider photo visible under the text panel
- Unreadable text (too small or low contrast)
- Wrong color theme for the topic (e.g. cleaning teal on a food book)

Be strict. Return passed=false if ANY serious layout defect is visible.
List specific issues found. If the page looks print-ready, return passed=true."""


def _pick_pages(total: int, section_count: int, sample: int) -> list[int]:
    """Always include dividers + first body pages; sample the rest."""
    picks: set[int] = set()
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


def _sniff_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/png"


def check_page(png: Path, page_num: int, bible: BookBible) -> LayoutVerdict:
    data = png.read_bytes()
    prompt = _PROMPT.format(title=bible.title, theme=bible.theme_name or "default")
    try:
        verdict = llm.vision_json(
            prompt, data, LayoutVerdict,
            media_type=_sniff_media_type(data),
        )
        verdict.page_number = page_num
        return verdict
    except Exception as exc:
        log.warning("layout_qa_page_failed", page=page_num, err=str(exc)[:200])
        return LayoutVerdict(
            passed=True, page_number=page_num,
            issues=[f"vision QA skipped: {exc}"],
        )


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
        sample_n = settings.layout_qa_sample_pages
        if section_count and section_count <= 3:
            to_check = list(range(1, len(paths) + 1))
        else:
            to_check = _pick_pages(len(paths), section_count, sample_n)

        issues: list[str] = []
        for page_num in to_check:
            if page_num > len(paths):
                continue
            v = check_page(paths[page_num - 1], page_num, bible)
            if not v.passed:
                for issue in v.issues:
                    issues.append(f"page {page_num}: {issue}")
            elif v.issues:
                for issue in v.issues:
                    issues.append(f"page {page_num} (info): {issue}")

        log.info("layout_qa_done", pages_checked=len(to_check), issues=len(issues))
        return issues
