"""Deterministic geometry gate on the FINAL delivered PDF.

The vision layout QA (pipeline/layout_qa.py) inspects per-`.page` element
screenshots — a DIFFERENT render path than `page.pdf()` — so it cannot catch a
page-size/scale defect in the printed PDF (e.g. the 72/96 downscale that pins all
content to the top-left). This gate rasterizes the ACTUAL PDF bytes that ship and
measures, per sampled page, how much of the sheet the content covers. It runs with
zero API cost on every book and flags the unmistakable downscale/clip signature:
a large white band on BOTH the right and the bottom.

Kept intentionally conservative (requires large white on both axes simultaneously)
so legitimately sparse pages — which still carry a full-width running header and a
centred folio — are never falsely failed.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from bookforge.config import settings

log = structlog.get_logger(__name__)

_RASTER_DPI = 48
# White beyond the intended ~0.75in safe margin. The real bug left ~2.85in right
# and ~3.34in bottom; a healthy text page leaves ~0.5in. 1.25in cleanly separates.
_MAX_SIDE_WHITE_IN = 1.25
# The downscale bug scales content from the page ORIGIN, so it is pinned to the
# top-left: left/top white collapse to ~0 while right/bottom grow. A legitimately
# sparse page (e.g. a centred title page) has symmetric/inset margins instead, so
# requiring small left AND top white avoids false-failing those.
_PINNED_WHITE_IN = 0.6
_NONWHITE_DELTA = 10  # px differs from white by more than this => content


def _sample_pages(total: int, sample: int = 12) -> list[int]:
    """Cover front matter + evenly spaced interior pages (1-indexed)."""
    if total <= 0:
        return []
    picks: set[int] = {p for p in (1, 2, 3, 4) if p <= total}
    if total > sample:
        step = max(1, total // sample)
        picks.update(range(step, total + 1, step))
    else:
        picks.update(range(1, total + 1))
    return sorted(picks)


def check_pdf_geometry(pdf: bytes) -> list[str]:
    """Return a list of geometry-defect strings (empty == healthy)."""
    if not settings.pdf_geometry_check:
        return []
    try:
        import pypdfium2 as pdfium
        from PIL import Image, ImageChops
    except Exception as exc:  # pragma: no cover - dependency guard
        log.warning("pdf_geometry_skipped", reason=str(exc)[:120])
        return []

    try:
        doc = pdfium.PdfDocument(pdf)
    except Exception as exc:
        log.warning("pdf_geometry_open_failed", err=str(exc)[:120])
        return []

    total = len(doc)
    pages = _sample_pages(total)
    scale = _RASTER_DPI / 72.0
    issues: list[str] = []

    for pno in pages:
        try:
            im = doc[pno - 1].render(scale=scale).to_pil().convert("RGB")
        except Exception as exc:
            log.warning("pdf_geometry_render_failed", page=pno, err=str(exc)[:120])
            continue
        w, h = im.size
        bg = Image.new("RGB", im.size, (255, 255, 255))
        mask = ImageChops.difference(im, bg).convert("L").point(
            lambda p: 255 if p > _NONWHITE_DELTA else 0
        )
        bbox = mask.getbbox()
        if not bbox:
            issues.append(f"page {pno}: blank — no content rendered")
            continue
        left_white = bbox[0] / _RASTER_DPI
        top_white = bbox[1] / _RASTER_DPI
        right_white = (w - bbox[2]) / _RASTER_DPI
        bottom_white = (h - bbox[3]) / _RASTER_DPI
        # The scale bug pins content to the top-left origin (left/top white ~0)
        # AND leaves large right/bottom white. Both conditions must hold, so a
        # centred/sparse page (symmetric margins) is not falsely failed.
        pinned_top_left = left_white < _PINNED_WHITE_IN and top_white < _PINNED_WHITE_IN
        shrunk = right_white > _MAX_SIDE_WHITE_IN and bottom_white > _MAX_SIDE_WHITE_IN
        if pinned_top_left and shrunk:
            cov_w = (bbox[2] - bbox[0]) / w * 100
            cov_h = (bbox[3] - bbox[1]) / h * 100
            issues.append(
                f"page {pno}: content fills only {cov_w:.0f}%x{cov_h:.0f}% of the sheet "
                f"({right_white:.1f}in right + {bottom_white:.1f}in bottom white) "
                "— page-size/scale defect"
            )

    log.info("pdf_geometry_checked", pages=len(pages), issues=len(issues))
    return issues


def _probe_html() -> str:
    """A tiny full-bleed document that exercises the real page geometry. Correct
    rendering fills the sheet; the 72/96 downscale would pin it to the top-left."""
    from bookforge.layout import theme

    w_in = settings.trim_width_in + 2 * settings.bleed_in
    h_in = settings.trim_height_in + 2 * settings.bleed_in
    root = theme.css_root(theme.pick("layout self check"), w_in, h_in, settings.bleed_in)
    guide_css = (Path(__file__).resolve().parents[1] / "layout" / "guide.css").read_text()
    page = ('<div class="page">'
            '<div style="position:absolute;inset:0;background:#0b3d2e"></div>'
            '</div>')
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>{root}{guide_css}</style></head><body>{page}{page}</body></html>')


def preflight_render_check() -> list[str]:
    """Render a representative page through the REAL render path and gate it
    BEFORE any content/image generation, so a render-geometry regression fails
    fast (~2s, ~$0) instead of after a full expensive run. Infra errors (e.g.
    Chromium missing) do NOT block generation — only a real geometry defect does.
    """
    if not settings.pdf_geometry_check:
        return []
    try:
        from bookforge.layout import render
        pdf = render.html_to_pdf(_probe_html())
    except Exception as exc:  # pragma: no cover - probe infra guard
        log.warning("preflight_render_failed", err=str(exc)[:160])
        return []
    issues = check_pdf_geometry(pdf)
    if issues:
        log.error("preflight_geometry_failed", issues=issues[:3])
    else:
        log.info("preflight_geometry_ok")
    return issues
