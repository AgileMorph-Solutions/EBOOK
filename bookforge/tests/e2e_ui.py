"""Shared end-to-end helpers that drive the REAL BookForge UI with Playwright.

These are opt-in integration checks (real API keys, real spend, minutes-long
for full books) — run explicitly, not as part of the unit suite:

    .venv/bin/python -m tests.e2e_ui_smoke     # cheap, 1 section
    .venv/bin/python -m tests.e2e_ui_heavy     # two full ~200-page books

Env: UI_URL (default http://localhost:3000), API_URL (default http://localhost:8000).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

UI_URL = os.environ.get("UI_URL", "http://localhost:3000")
API_URL = os.environ.get("API_URL", "http://localhost:8000")
OUT = Path(os.environ.get("E2E_OUT", "/tmp/bookforge-e2e"))
OUT.mkdir(parents=True, exist_ok=True)

TOPIC_PH = "e.g. Forgotten British Meals Our Grandparents Lived On"
TEXT_PH = "Paste your source notes, transcript, or research here…"


class E2EFailure(AssertionError):
    pass


def submit_via_ui(topic: str, source_text: str, target_pages: int,
                  section_limit: int | None = None) -> str:
    """Fill and submit the create form in the real browser; return book_id."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 1000})
        pg.goto(UI_URL, wait_until="networkidle")
        pg.get_by_placeholder(TOPIC_PH).fill(topic)
        pg.get_by_placeholder(TEXT_PH).fill(source_text)
        pg.locator("#pages").fill(str(target_pages))
        if section_limit is not None:
            pg.get_by_text("Advanced options").click()
            pg.wait_for_timeout(300)
            pg.locator("#sections").fill(str(section_limit))
        pg.wait_for_timeout(300)
        pg.get_by_role("button", name="Generate ebook").click()
        pg.wait_for_url("**/books/**", timeout=30_000)
        book_id = pg.url.rstrip("/").split("/")[-1]
        browser.close()
    print(f"  submitted via UI → {book_id}")
    return book_id


def wait_for_done(book_id: str, timeout_s: int) -> dict:
    """Poll the status endpoint until done/failed or timeout."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        s = requests.get(f"{API_URL}/books/{book_id}", timeout=15).json()
        tag = f"{s.get('status')}/{s.get('stage','')}"
        if tag != last:
            print(f"  {tag}")
            last = tag
        if s.get("status") in ("done", "failed"):
            return s
        time.sleep(15)
    raise E2EFailure(f"{book_id}: timed out after {timeout_s}s (last={last})")


def download_via_ui(book_id: str) -> Path:
    """Click the real Download PDF button on the detail page; save the file."""
    from playwright.sync_api import sync_playwright

    dest = OUT / f"{book_id}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(f"{UI_URL}/books/{book_id}", wait_until="networkidle")
        pg.wait_for_timeout(1000)
        with pg.expect_download(timeout=60_000) as dl_info:
            pg.get_by_role("button", name="Download PDF").first.click()
        dl_info.value.save_as(str(dest))
        browser.close()
    print(f"  downloaded via UI → {dest} ({dest.stat().st_size} bytes)")
    return dest


def verify(status: dict, *, min_pages: int, max_mb: float, require_qa: bool = True) -> dict:
    """Assert the finished job meets the quality/length bar. Returns the artifact."""
    if status.get("status") != "done":
        raise E2EFailure(f"status={status.get('status')} error={status.get('error')}")
    art = status.get("artifact") or {}
    pages = art.get("page_count", 0)
    mb = art.get("file_size_mb", 0)
    qa = art.get("qa") or {}
    if pages < min_pages:
        raise E2EFailure(f"page_count {pages} < required {min_pages}")
    if mb > max_mb:
        raise E2EFailure(f"file_size {mb}MB exceeds cap {max_mb}MB")
    if require_qa and not qa.get("passed"):
        raise E2EFailure(f"QA did not pass: {qa}")
    print(f"  ✓ pages={pages} size={mb}MB qa_passed={qa.get('passed')}")
    return art


def verify_pdf(path: Path, expected_pages: int) -> None:
    """Open the downloaded PDF and confirm the page count matches the artifact."""
    import pikepdf

    with pikepdf.open(str(path)) as pdf:
        n = len(pdf.pages)
    if n != expected_pages:
        raise E2EFailure(f"downloaded PDF has {n} pages, artifact said {expected_pages}")
    print(f"  ✓ downloaded PDF opens with {n} pages (matches artifact)")


def run_case(name: str, *, topic: str, source_text: str, target_pages: int,
             min_pages: int, section_limit: int | None, timeout_s: int,
             max_mb: float = 80.0) -> None:
    print(f"\n=== {name} ===")
    book_id = submit_via_ui(topic, source_text, target_pages, section_limit)
    status = wait_for_done(book_id, timeout_s)
    art = verify(status, min_pages=min_pages, max_mb=max_mb)
    pdf = download_via_ui(book_id)
    verify_pdf(pdf, art.get("page_count", -1))
    print(f"=== {name}: PASS ===")
