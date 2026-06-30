"""HTML -> print PDF (+ PNG page previews for QA).

In deployment this can POST to a `render` service (Node + Chromium) at
RENDER_URL. Locally it uses Playwright's Chromium directly. Either way, before
printing we run a TEXT-FIT pass: each `.gfit` region is scaled to the largest
font that still fits its page box (down to a senior-readable floor), and
growable regions then expand to fill any slack — so pages are big-text,
fully-covered, and never clipped.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

RENDER_URL = os.environ.get("RENDER_URL", "")

# ONE canonical body size (pt) for the whole book. Body text == 1em of the .gfit
# container, and every region renders at this size so the type is CONSISTENT from
# page to page — a real production book does not change body size per page.
# Dense regions may SHRINK toward the floor (only when content would otherwise
# overflow); nothing ever grows above BASE. A page may raise its own floor via
# data-fitmin on the .gfit.
_FIT_BASE = float(os.environ.get("FIT_BASE_PT", "15"))
_FIT_FLOOR = float(os.environ.get("FIT_FLOOR_PT", "12.5"))

# Runs in the page: set every .gfit to the canonical BASE size; only if its
# content overflows do we binary-search DOWN to the largest size that fits (never
# below the floor). Growable regions then absorb slack with whitespace.
# Also fits section-divider titles and safety-band headings so text never bleeds.
# Returns a report of any region still overflowing at the floor.
_LAYOUT_FIT_JS = """
() => {
  const FLOOR = %f, BASE = %f;
  const stuck = [];

  const shrinkToFit = (el, prop, minPt, maxPt, container) => {
    const box = container || el.parentElement;
    if (!box) return;
    const fits = (s) => { el.style[prop] = s + 'pt';
      return box.scrollHeight <= box.clientHeight + 2; };
    let best = minPt;
    if (fits(maxPt)) { best = maxPt; }
    else {
      let lo = minPt, hi = maxPt;
      for (let k = 0; k < 22; k++) {
        const mid = (lo + hi) / 2;
        if (fits(mid)) { best = mid; lo = mid; } else { hi = mid; }
      }
    }
    el.style[prop] = best + 'pt';
    if (best <= minPt + 0.01 && box.scrollHeight > box.clientHeight + 2)
      stuck.push(el.className || prop);
  };

  document.querySelectorAll('.gfit').forEach((el, i) => {
    const MIN = parseFloat(el.dataset.fitmin) || FLOOR;
    el.classList.add('measuring');
    const fits = (s) => { el.style.fontSize = s + 'pt';
                          return el.scrollHeight <= el.clientHeight + 1; };
    let best = MIN;
    if (fits(BASE)) { best = BASE; }
    else {
      let lo = MIN, hi = BASE;
      for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2;
        if (fits(mid)) { best = mid; lo = mid; } else { hi = mid; } }
    }
    el.style.fontSize = best + 'pt';
    el.classList.remove('measuring');
    if (best <= MIN + 0.01 && el.scrollHeight > el.clientHeight + 2)
      stuck.push('gfit:' + i);
  });

  document.querySelectorAll('.gdivider .dpanel').forEach((panel) => {
    const title = panel.querySelector('.t');
    if (title) shrinkToFit(title, 'fontSize', 20, 34, panel);
  });

  document.querySelectorAll('.gsafety .band h2').forEach((h2) => {
    const band = h2.closest('.band');
    if (band) shrinkToFit(h2, 'fontSize', 28, 40, band);
  });

  return { count: document.querySelectorAll('.gfit').length, stuck };
}
""" % (_FIT_FLOOR, _FIT_BASE)


def _setup_page(page) -> None:
    page.emulate_media(media="print")


def _prepare(page, html: str) -> dict:
    page.set_content(html, wait_until="networkidle")
    page.evaluate("() => document.fonts.ready")
    report = page.evaluate(_LAYOUT_FIT_JS)
    if report.get("stuck"):
        log.warning("fit_overflow_at_floor", regions=report["stuck"])
    return report


def html_to_pdf(html: str) -> bytes:
    if RENDER_URL:
        import urllib.request
        req = urllib.request.Request(
            RENDER_URL.rstrip("/") + "/render",
            data=html.encode("utf-8"),
            headers={"Content-Type": "text/html"},
        )
        return urllib.request.urlopen(req, timeout=300).read()
    return _playwright_pdf(html)


def _playwright_pdf(html: str) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "No RENDER_URL set and Playwright not installed. "
            "`pip install playwright && playwright install chromium`."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            _setup_page(page)
            _prepare(page, html)
            pdf = page.pdf(print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
        return pdf


def html_to_pngs(html: str, out_dir, scale: float = 2.0) -> list[Path]:
    """Render each .page to a PNG (for visual QA). Returns the PNG paths."""
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(device_scale_factor=scale)
            _setup_page(page)
            _prepare(page, html)
            for i, el in enumerate(page.query_selector_all(".page"), 1):
                pth = out / f"page-{i:02d}.png"
                el.screenshot(path=str(pth))
                paths.append(pth)
        finally:
            browser.close()
    log.info("pngs_rendered", count=len(paths), out=str(out))
    return paths
