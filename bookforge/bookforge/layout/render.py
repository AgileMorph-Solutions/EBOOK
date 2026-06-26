"""HTML -> print PDF.

In deployment this POSTs to the `render` service (Node + Paged.js + headless
Chromium) at RENDER_URL. For a local spike with no Docker, it falls back to a
local Playwright Chromium if installed.
"""
from __future__ import annotations

import os
import urllib.request

import structlog

log = structlog.get_logger(__name__)

RENDER_URL = os.environ.get("RENDER_URL", "")


def html_to_pdf(html: str) -> bytes:
    if RENDER_URL:
        req = urllib.request.Request(
            RENDER_URL.rstrip("/") + "/render",
            data=html.encode("utf-8"),
            headers={"Content-Type": "text/html"},
        )
        return urllib.request.urlopen(req, timeout=300).read()
    return _playwright_fallback(html)


def _playwright_fallback(html: str) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "No RENDER_URL set and Playwright not installed. Either run the "
            "`render` Docker service or `pip install playwright && playwright "
            "install chromium`."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        # Paged.js polyfill drives @page; print_background keeps full-bleed art.
        pdf = page.pdf(print_background=True, prefer_css_page_size=True)
        browser.close()
        return pdf
