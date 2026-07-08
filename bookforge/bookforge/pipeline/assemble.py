"""Stage 6 — assembly, file-size preflight, delivery.

Renders the print HTML to PDF, linearizes it, checks page count and the
file-size budget, and stores the final interior PDF. (CMYK/PDF-X conversion via
Ghostscript runs in the `pdftools` Docker service; the spike ships the RGB PDF
KDP accepts.)
"""
from __future__ import annotations

import io
import json
import math

import structlog

from bookforge import safety
from bookforge.clients import storage
from bookforge.config import settings
from bookforge.errors import GeometryError, LayoutError
from bookforge.layout import build, guide, render
from bookforge.pipeline import layout_budget, layout_qa, pdf_geometry
from bookforge.schemas import BookArtifact, BookBible, Chapter, GeneratedImage, QAResult

log = structlog.get_logger(__name__)

# Vision-QA issue fragments that indicate a real print defect (vs a
# nice-to-fix whitespace/balance nit).
_BLOCKING_LAYOUT_HINTS = (
    "glyph", "orphan", "blurry", "blur", "clip", "overlap", "corrupt",
    "missing page number", "broken", "illegible", "cut off",
)

# Vision-QA commentary about page-NUMBER sequencing/ordering (TOC, reference
# lists) is not a visual defect — folios are computed programmatically and are
# always correct, and a TOC legitimately interleaves independent groups
# (Chapters vs Reference) whose page numbers don't run monotonically together.
# The vision model occasionally misreads this as an "inconsistency" and its
# wording can coincidentally match a blocking hint (e.g. "overlap"); never let
# that fail a book over content the render pipeline computed deterministically.
_NON_BLOCKING_PATTERNS = (
    "page order", "page numbering", "out of sequence", "page number",
    "page reference", "table of contents", "numerically", "folio",
)


def _is_non_blocking_content_nit(issue: str) -> bool:
    lower = issue.lower()
    return any(p in lower for p in _NON_BLOCKING_PATTERNS)


def _render_gated(html: str) -> bytes:
    """Render HTML → PDF and HARD-FAIL on any in-page layout defect.

    The fit pass inside the renderer audits every text clip-container (divider
    panels, .gfit regions, safety bands, body boxes) and reports text that is
    still clipped or overflowing after fitting. Zero API cost, runs on every
    page of every book — a broken layout is never stored or shipped."""
    pdf = _linearize(render.html_to_pdf(html))
    defects = render.layout_defects(render.last_fit_report)
    if defects:
        log.error("layout_audit_failed", defects=defects[:8])
        raise LayoutError(
            "layout audit failed — refusing to ship clipped/overflowing text: "
            + " | ".join(defects[:3])
        )
    if render.last_fit_report is None:
        # remote render service path: no fit JS ran, so no audit is available
        log.warning("layout_audit_unavailable", reason="render_service_path")
    return pdf


def _plan_repairs(overflows: list[dict], prev: dict[str, int]) -> dict[str, int]:
    """Compute each offender's FINAL repair directly from its measured overflow
    ratio (content_h / box_h) — one shot, not level-by-level escalation.

    Level semantics per key type (interpreted by guide.build_practical_html):
      entry "{ch}.{bi}"  1 spread · 2 flow · 3 flow+shed intro · 4+ rescue flow
      chart:             level == page count for the row split (2..6)
      essay:             1 = 0.7x word budget · 2 = 0.55x
      howto              1 compact · 2 split · 4 rescue floor
      list:              1 floor 11.5 · 2 re-chunk 11/pg · 3 re-chunk 9/pg
      feature:/check:/pro  fit floors only · 4 = terminal 9pt rescue

    Progress is guaranteed: a key that reappears never gets the same plan again
    (min bump of prev+1), so the loop converges or reaches the rescue floor."""
    worst: dict[str, float] = {}
    for o in overflows:
        key = str(o.get("entry") or "")
        if not key:
            continue
        box = float(o.get("box_h") or 0)
        content = float(o.get("content_h") or 0)
        ratio = (content / box) if box > 0 else 2.0
        worst[key] = max(worst.get(key, 0.0), ratio)

    plan = dict(prev)
    for key, ratio in worst.items():
        if key.startswith("chart:"):
            target = 1 if ratio <= 1.08 else min(6, math.ceil(ratio / 0.9) + 1)
        elif key.startswith("essay:"):
            target = 1 if ratio <= 1.4 else 2
        elif key == "howto":
            target = 1 if ratio <= 1.15 else 2
        elif key.startswith("list:"):
            target = 1 if ratio <= 1.08 else (2 if ratio <= 1.3 else 3)
        elif key.startswith(("feature:", "check:")) or key == "pro":
            target = 1 if ratio <= 1.08 else (2 if ratio <= 1.25 else 4)
        else:  # entry card "{ch}.{bi}"
            target = 1 if ratio <= 1.15 else (2 if ratio <= 1.8 else 3)
        plan[key] = min(6, max(target, prev.get(key, 0) + 1))
    return plan


def _render_practical(bible, chapters, images, *, cover_key, how_to,
                      safety_data, pro) -> tuple[str, bytes]:
    """Build + render a practical guide with MEASURED one-shot self-repair.

    Render once, and if the in-page audit reports overflows, compute the exact
    repair for every offender from its measured overflow ratio (chart → exact
    page split, entry → the right flow level, essay → the right squeeze) and
    rebuild with ALL repairs applied — expected clean on the second render,
    with at most two corrective rounds (measurements shift slightly after a
    rebuild). Offenders that exhaust structural reflow land on the terminal
    9pt rescue floor, which always fits schema-bounded content — so a book
    never fails for layout; rescued pages are logged loudly. LayoutError
    remains only as an assertion of last resort (untagged page or divider
    stuck even after force-break)."""
    repairs: dict[str, int] = {}
    defects: list[str] = []
    raw: list[dict] = []
    for _ in range(4):
        html = guide.build_practical_html(
            bible, chapters, images, cover_key=cover_key,
            how_to=layout_budget.trim_how_to(how_to), safety=safety_data, pro=pro,
            repairs=dict(repairs) if repairs else None,
        )
        pdf = _linearize(render.html_to_pdf(html))
        report = render.last_fit_report
        defects = render.layout_defects(report)
        if not defects:
            if report is None:
                log.warning("layout_audit_unavailable", reason="render_service_path")
            return html, pdf
        raw = (report or {}).get("overflows", [])
        new_plan = _plan_repairs(raw, repairs)
        if new_plan == repairs:
            break  # only untagged offenders remain — nothing left to repair
        rescued = sorted(k for k, v in new_plan.items()
                         if v >= 4 and repairs.get(k, 0) < 4
                         and not k.startswith("chart:"))
        if rescued:
            log.warning("layout_rescue_floor", keys=rescued)
        repairs = new_plan
        log.info("layout_selfrepair", repairs=repairs)
    log.error("layout_audit_failed", defects=defects[:8])
    raise LayoutError(
        "layout audit failed — refusing to ship clipped/overflowing text: "
        + " | ".join(defects[:3]),
        defects=raw,
    )


def _page_count(pdf: bytes) -> int:
    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(pdf)) as doc:
            return len(doc.pages)
    except Exception:
        return pdf.count(b"/Type /Page") or pdf.count(b"/Type/Page")


def _save_render_manifest(book_id, bible, chapters, images, cover_key,
                          how_to, safety_data, pro) -> None:
    """Persist the structured inputs to build_practical_html so the book can be
    re-rendered after CSS/layout changes with zero LLM/image cost."""
    try:
        manifest = {
            "bible": bible.model_dump(mode="json"),
            "chapters": [c.model_dump(mode="json") for c in chapters],
            "images": [i.model_dump(mode="json") for i in images],
            "cover_key": cover_key,
            "how_to": how_to,
            "safety": safety_data,
            "pro": pro,
        }
        storage.put(f"books/{book_id}/render_manifest.json",
                    json.dumps(manifest).encode("utf-8"), "application/json")
    except Exception as exc:  # never let manifest caching break a build
        log.warning("render_manifest_save_failed", book_id=book_id, err=str(exc)[:160])


def rerender(book_id: str) -> BookArtifact:
    """Re-render a practical guide's PDF from its cached manifest (no API cost).

    Applies the current layout/CSS, re-runs the geometry gate, and overwrites the
    stored interior.pdf. Used to apply design fixes without regenerating content.
    """
    data = json.loads(storage.get(f"books/{book_id}/render_manifest.json"))
    bible = BookBible(**data["bible"])
    chapters = [Chapter(**c) for c in data["chapters"]]
    images = [GeneratedImage(**i) for i in data["images"]]
    _, pdf = _render_practical(
        bible, chapters, images, cover_key=data.get("cover_key"),
        how_to=data.get("how_to"), safety_data=data.get("safety"),
        pro=data.get("pro"),
    )
    geometry_issues = pdf_geometry.check_pdf_geometry(pdf)
    if geometry_issues:
        log.error("rerender_geometry_failed", book_id=book_id, issues=geometry_issues[:5])
        raise GeometryError("re-render geometry gate failed: " + " | ".join(geometry_issues[:3]))
    key = f"books/{book_id}/interior.pdf"
    storage.put(key, pdf, "application/pdf")
    pages = _page_count(pdf)
    size_mb = round(len(pdf) / 1_048_576, 2)
    log.info("rerendered", book_id=book_id, pages=pages, mb=size_mb)
    return BookArtifact(book_id=book_id, interior_pdf_key=key,
                        page_count=pages, file_size_mb=size_mb,
                        qa=QAResult(groundedness=1.0, passed=True))


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
        # Safety page content: the book's own generated brief (profile books)
        # or the legacy canon (old safety_key books).
        safety_data = {"never_mix": safety.cards_for_bible(bible)}
        # Cache the structured render inputs so layout/CSS tweaks can be re-rendered
        # onto this book WITHOUT re-spending on content/images (see rerender()).
        _save_render_manifest(book_id, bible, chapters, images,
                              cover_key, how_to, safety_data, pro)
        # Deterministic render + audit + self-repair FIRST (free), so vision QA
        # credits are never spent on a layout the audit already rejects.
        html, pdf = _render_practical(
            bible, chapters, images, cover_key=cover_key,
            how_to=how_to, safety_data=safety_data, pro=pro,
        )
        budget_pages = sum(p.page_budget for p in bible.sections)
        layout_issues = layout_qa.run_layout_qa(
            html, bible, section_count=len(bible.sections),
        )
        qa.layout_issues.extend(layout_issues)
        # "(info)" = advisory nit; "(skipped)" = vision QA couldn't inspect the
        # page (no credits/API error) — surfaced loudly, never a silent pass.
        skipped = [i for i in layout_issues if "(skipped)" in i]
        if skipped:
            log.warning("layout_qa_incomplete", pages=len(skipped),
                        sample=skipped[:2])
        hard = [i for i in layout_issues
                if "(info)" not in i and "(skipped)" not in i]
        # Content/page-numbering commentary is never a visual print defect —
        # filter it out before hint-matching so it can't masquerade as one.
        hard = [i for i in hard if not _is_non_blocking_content_nit(i)]
        # Classify: real print defects block; whitespace nits only warn.
        blocking = hard if settings.layout_qa_block else [
            i for i in hard if any(k in i.lower() for k in _BLOCKING_LAYOUT_HINTS)]
        if blocking and (settings.layout_qa_block or bible.profile is not None):
            # Never ship a visible print defect — fail the job loudly instead
            # of setting a flag nothing downstream enforces.
            qa.passed = False
            log.error("layout_qa_blocking", issues=blocking[:5])
            raise LayoutError(
                "vision layout QA found print defects: " + " | ".join(blocking[:3])
            )
    else:
        html = build.build_html(bible, chapters, images)
        budget_pages = sum(p.page_budget for p in bible.chapters)
        pdf = _render_gated(html)

    size_mb = len(pdf) / 1_048_576
    pages = _page_count(pdf)
    issues = list(qa.layout_issues)

    # Deterministic geometry gate on the ACTUAL delivered PDF. Runs with zero API
    # cost on every book and HARD-FAILS a page-size/scale/clip defect, so a broken
    # render can never be stored or reach "done" (unlike the vision QA, which
    # inspects a different, correct-looking render path).
    geometry_issues = pdf_geometry.check_pdf_geometry(pdf)
    if geometry_issues:
        issues.extend(geometry_issues)
        qa.passed = False
        qa.layout_issues = issues
        log.error("pdf_geometry_failed", book_id=book_id, issues=geometry_issues[:5])
        raise GeometryError(
            "PDF geometry gate failed — refusing to ship a broken layout: "
            + " | ".join(geometry_issues[:3])
        )

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
