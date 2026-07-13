"""HTML -> print PDF (+ PNG page previews for QA).

In deployment this can POST to a `render` service (Node + Chromium) at
RENDER_URL. Locally it uses Playwright's Chromium directly. Either way, before
printing we run a TEXT-FIT pass: each `.gfit` region is scaled to the largest
font that still fits its page box (down to a high-legibility floor), and
growable regions then expand to fill any slack — so pages are big-text,
fully-covered, and never clipped.

Fixed display regions (cover band, divider panels, safety bands) get a terminal
rescue ladder instead of a dead end: shrink -> force-break -> drop the floor ->
clamp with an ellipsis. A clamped region is reported under `rescued` (ships as
a warning), never under `stuck` (which used to kill the job).

`render_pdf` / `render_pngs` RETURN the fit/audit report — callers must gate on
the returned report, never on shared state, so two concurrent books can never
read each other's audit. (`last_fit_report` remains for legacy dev scripts.)
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from bookforge.config import settings

log = structlog.get_logger(__name__)

RENDER_URL = os.environ.get("RENDER_URL", "")

# CSS renders 1in == 96px. The page (trim + bleed) must therefore be laid out in a
# viewport of exactly that px size, or Chromium's print path scales the content
# (the classic 72/96 = 0.75 shrink pinned top-left). We pin BOTH the layout
# viewport and the page.pdf() sheet size to the same trim+bleed dimensions so the
# geometry is deterministic and independent of viewport defaults / @page quirks.
_CSS_PX_PER_IN = 96

# Renders are Chromium-heavy (RAM + CPU); serialize them process-wide so two
# concurrent books (or a book + a rerender) can't stack Chromium instances —
# that's what OOM'd the whole API. Also keeps the legacy report global coherent.
_render_lock = threading.Lock()

# Timeouts mirror render/server.js, which was already sized for large image-rich
# books ("Puppeteer's default 30s is too short"): 300s to lay out, 180s to print.
_SET_CONTENT_TIMEOUT_MS = 300_000
_PDF_TIMEOUT_MS = 180_000


def _page_inches() -> tuple[float, float]:
    """Full-bleed sheet size in inches (trim + 2*bleed)."""
    return (
        settings.trim_width_in + 2 * settings.bleed_in,
        settings.trim_height_in + 2 * settings.bleed_in,
    )


def _page_viewport() -> dict[str, int]:
    w_in, h_in = _page_inches()
    return {"width": round(w_in * _CSS_PX_PER_IN),
            "height": round(h_in * _CSS_PX_PER_IN)}

# ONE canonical body size (pt) for the whole book. Body text == 1em of the .gfit
# container, and every region renders at this size so the type is CONSISTENT from
# page to page — a real production book does not change body size per page.
# Dense regions may SHRINK toward the floor (only when content would otherwise
# overflow); nothing ever grows above BASE. A page may raise its own floor via
# data-fitmin on the .gfit.
_FIT_BASE = float(os.environ.get("FIT_BASE_PT", "16"))
_FIT_FLOOR = float(os.environ.get("FIT_FLOOR_PT", "13"))

# Runs in the page: set every .gfit to the canonical BASE size; only if its
# content overflows do we binary-search DOWN to the largest size that fits (never
# below the floor). Growable regions then absorb slack with whitespace.
# Also fits section-divider titles, safety-band headings, and the cover band.
# Fixed display regions that still overflow after force-break get a TERMINAL
# RESCUE: floor dropped 25%, then a line-clamp ellipsis — reported as `rescued`
# (ships with a warning) instead of `stuck` (job-fatal).
# Finishes with a defect AUDIT over every text clip-container and returns
# {stuck, rescued, overflows, pages}.
_LAYOUT_FIT_JS = """
() => {
  const FLOOR = %f, BASE = %f;
  const stuck = [];
  const rescued = [];

  // Fit BOTH axes: vertical overflow of the container AND horizontal overflow of
  // the element itself (a single word wider than the column). Both shrink
  // monotonically with font-size, so the binary search stays valid. Returns
  // whether the final size actually fits — the CALLER records stuck regions, so
  // a first pass that a fallback (e.g. force-break) later fixes is not reported.
  const shrinkToFit = (el, prop, minPt, maxPt, container) => {
    const box = container || el.parentElement;
    if (!box) return true;
    const fits = (s) => { el.style[prop] = s + 'pt';
      return box.scrollHeight <= box.clientHeight + 2
          && el.scrollWidth <= el.clientWidth + 1; };
    let best = minPt;
    if (fits(maxPt)) { best = maxPt; }
    else {
      let lo = minPt, hi = maxPt;
      for (let k = 0; k < 22; k++) {
        const mid = (lo + hi) / 2;
        if (fits(mid)) { best = mid; lo = mid; } else { hi = mid; }
      }
    }
    return fits(best);  // applies the final size as a side effect
  };

  // Terminal ladder for FIXED display regions (cover band, divider panel,
  // safety band): force-break -> floor dropped 25%% -> line-clamp ellipsis.
  // Always resolves; the page ships with a rescue warning instead of dying.
  const rescueFit = (el, label, minPt, maxPt, container) => {
    if (shrinkToFit(el, 'fontSize', minPt, maxPt, container)) return;
    el.classList.add('force-break');
    if (shrinkToFit(el, 'fontSize', minPt, maxPt, container)) return;
    const dropped = minPt * 0.75;
    if (shrinkToFit(el, 'fontSize', dropped, maxPt, container)) {
      rescued.push(label + ' (floor dropped)');
      return;
    }
    el.classList.add('rescue-clip');
    shrinkToFit(el, 'fontSize', dropped, maxPt, container);
    rescued.push(label + ' (clamped)');
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
    if (!title) return;
    rescueFit(title, 'divider:' + (title.textContent || '').trim().slice(0, 40),
              22, 34, panel);
  });

  document.querySelectorAll('.gsafety .band h2').forEach((h2) => {
    const band = h2.closest('.band');
    if (band) rescueFit(h2, 'safety-band', 28, 40, band);
  });

  // Cover title/subtitle: long topics ("365 Forgotten American Recipes...")
  // can overflow the fixed-height title band and get clipped at the page
  // edge. Shrink title first (it dominates height), then the subtitle,
  // against the whole band so both compete for the same space fairly.
  document.querySelectorAll('.gcover .cband').forEach((band) => {
    const title = band.querySelector('.ctitle');
    const sub = band.querySelector('.csub');
    if (title) rescueFit(title, 'cover-title', 24, 46, band);
    if (sub) rescueFit(sub, 'cover-sub', 11, 17, band);
  });

  // ---- defect audit -------------------------------------------------------
  // Text clip-containers where ANY overflow means clipped print output. Image
  // crop frames (.dphoto, entry frames) are deliberately excluded: their inner
  // img overflows by design (cover-crop), and an intermediate overflow:hidden
  // stops that overflow from propagating to these containers anyway.
  // Regions clamped by the terminal rescue are excluded too — their ellipsis
  // clip is deliberate and already reported under `rescued`.
  const pages = Array.from(document.querySelectorAll('.page'));
  const overflows = [];
  const AUDIT = '.gdivider .dpanel, .gfit, .gsafety .band, .gbody-box, .gcover .cband';
  document.querySelectorAll(AUDIT).forEach((el) => {
    if (el.querySelector('.rescue-clip') || el.classList.contains('rescue-clip')) return;
    const over_h = el.scrollHeight - el.clientHeight;
    const over_w = el.scrollWidth - el.clientWidth;
    if (over_h <= 2 && over_w <= 2) return;
    const pg = el.closest('.page');
    overflows.push({
      page: pg ? pages.indexOf(pg) + 1 : 0,
      entry: (pg && pg.getAttribute('data-entry')) || '',
      selector: (el.className || '').toString().split(' ').slice(0, 2).join('.'),
      over_px: Math.max(over_h, over_w),
      // raw measurements so the repair planner can compute the EXACT fix
      // (page count, flow density, squeeze) in one shot instead of escalating
      content_h: el.scrollHeight,
      box_h: el.clientHeight,
      text: (el.textContent || '').trim().slice(0, 60),
    });
  });

  return { count: document.querySelectorAll('.gfit').length, stuck, rescued,
           overflows, pages: pages.map((p) => p.className) };
}
""" % (_FIT_FLOOR, _FIT_BASE)

# Report of the most recent fit/audit pass — legacy convenience for dev scripts
# ONLY. Production code paths use the report RETURNED by render_pdf/render_pngs;
# reading this global under concurrency gates one book on another book's audit.
last_fit_report: dict | None = None


def _setup_page(page) -> None:
    page.emulate_media(media="print")


def _prepare(page, html: str) -> dict:
    global last_fit_report
    # "load" (not "networkidle"): all assets are inline/base64 data URIs, and
    # network-idle can stall on large inline payloads (per render/server.js).
    page.set_content(html, wait_until="load", timeout=_SET_CONTENT_TIMEOUT_MS)
    page.evaluate("() => document.fonts.ready")
    report = page.evaluate(_LAYOUT_FIT_JS)
    if report.get("stuck"):
        log.warning("fit_overflow_at_floor", regions=report["stuck"])
    if report.get("rescued"):
        log.warning("fit_rescued_regions", regions=report["rescued"])
    if report.get("overflows"):
        log.warning("layout_audit_overflow", regions=report["overflows"][:8])
    last_fit_report = report
    return report


def layout_defects(report: dict | None) -> list[str]:
    """Human-readable defect strings from a fit/audit report (empty = clean).
    Rescued regions are NOT defects — they shipped with a deliberate clamp and
    surface via layout_warnings() instead."""
    if not report:
        return []
    issues = [f"text stuck at floor size, still overflowing: {r}"
              for r in report.get("stuck", [])]
    issues += [
        f"page {o.get('page')}: content clipped in .{o.get('selector')} "
        f"by {o.get('over_px')}px ({o.get('text', '')!r})"
        for o in report.get("overflows", [])
    ]
    return issues


def layout_warnings(report: dict | None) -> list[str]:
    """Non-fatal degradations (terminal rescues) worth surfacing to ops."""
    if not report:
        return []
    return [f"layout rescue applied: {r}" for r in report.get("rescued", [])]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _render_service_pdf(html: str) -> tuple[bytes, dict | None]:
    """Render via the remote service, running the SAME fit+audit JS as the local
    Playwright path (the script is sent with the request; server.js executes it
    and returns the report). Returns (pdf, report); report is None only when an
    old service answers with the legacy raw-PDF protocol (no fit pass ran)."""
    import base64
    import json as _json
    import urllib.request

    w_in, h_in = _page_inches()
    payload = _json.dumps({
        "html": html, "fit_js": _LAYOUT_FIT_JS.strip(),
        "width_in": w_in, "height_in": h_in,
    }).encode("utf-8")
    req = urllib.request.Request(
        RENDER_URL.rstrip("/") + "/render",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=300)
    body = resp.read()
    if "application/json" in (resp.headers.get("Content-Type") or ""):
        data = _json.loads(body)
        return base64.b64decode(data["pdf"]), data.get("report")
    # legacy service: raw PDF, no fit pass / audit ran
    log.warning("render_service_legacy_no_fit_pass")
    return body, None


def render_pdf(html: str) -> tuple[bytes, dict | None]:
    """Render HTML → (PDF, fit/audit report). Prefer the render service (retried
    on transient errors), then fall back to local Playwright if the service is
    unreachable, so a render outage on one path doesn't fail the book.

    The report belongs to THIS render — gate on it, never on `last_fit_report`.
    """
    global last_fit_report
    if RENDER_URL:
        try:
            pdf, report = _render_service_pdf(html)
            last_fit_report = report
            if report and report.get("stuck"):
                log.warning("fit_overflow_at_floor", regions=report["stuck"])
            if report and report.get("overflows"):
                log.warning("layout_audit_overflow", regions=report["overflows"][:8])
            return pdf, report
        except Exception as exc:
            log.warning("render_service_failed_falling_back_to_playwright",
                        err=str(exc)[:200])
            try:
                return _playwright_pdf(html)
            except Exception:
                raise exc  # surface the original render-service error
    return _playwright_pdf(html)


def html_to_pdf(html: str) -> bytes:
    """Legacy wrapper (dev scripts): PDF only, report via `last_fit_report`."""
    pdf, _report = render_pdf(html)
    return pdf


def _playwright_pdf(html: str) -> tuple[bytes, dict | None]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "No RENDER_URL set and Playwright not installed. "
            "`pip install playwright && playwright install chromium`."
        ) from exc

    w_in, h_in = _page_inches()
    with _render_lock:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=_page_viewport(),
                                        device_scale_factor=1)
                _setup_page(page)
                report = _prepare(page, html)
                # Pin the sheet to trim+bleed and scale=1 so the sized-in-CSS
                # content maps 1:1 onto the page (no viewport-driven downscale).
                pdf = page.pdf(
                    width=f"{w_in}in", height=f"{h_in}in", scale=1,
                    print_background=True, prefer_css_page_size=False,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
            return pdf, report


def render_pngs(html: str, out_dir, scale: float = 2.0) -> tuple[list[Path], dict | None]:
    """Render each .page to a PNG (for visual QA). Returns (paths, fit report)."""
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with _render_lock:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=_page_viewport(),
                                        device_scale_factor=scale)
                _setup_page(page)
                report = _prepare(page, html)
                for i, el in enumerate(page.query_selector_all(".page"), 1):
                    pth = out / f"page-{i:02d}.png"
                    el.screenshot(path=str(pth))
                    paths.append(pth)
            finally:
                browser.close()
    log.info("pngs_rendered", count=len(paths), out=str(out))
    return paths, report


def html_to_pngs(html: str, out_dir, scale: float = 2.0) -> list[Path]:
    """Legacy wrapper (dev scripts): paths only, report via `last_fit_report`."""
    paths, _report = render_pngs(html, out_dir, scale=scale)
    return paths
