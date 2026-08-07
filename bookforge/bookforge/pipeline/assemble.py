"""Stage 6 — assembly, file-size preflight, delivery.

Renders the print HTML to PDF, linearizes it, checks geometry and the
file-size budget, and stores the final interior PDF. (CMYK/PDF-X conversion via
Ghostscript runs in the `pdftools` Docker service; the spike ships the RGB PDF
KDP accepts.)

Always-ship contract: every gate here repairs first (measured self-repair,
terminal rescue, geometry re-render) and, when SHIP_ON_LAYOUT_DEFECTS is on,
degrades to shipping the best rendered PDF with `warnings` on the artifact
instead of raising. LayoutError still exists so the orchestrator's block-regen
loop gets first shot at MAPPABLE defects — but it is no longer job-fatal.
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
from bookforge.pipeline import (
    checkpoints,
    layout_budget,
    layout_qa,
    page_pad,
    pdf_geometry,
)
from bookforge.pipeline.page_limits import (
    allowed_page_range,
    is_within_page_range,
)
from bookforge.schemas import BookArtifact, BookBible, Chapter, GeneratedImage, QAResult

log = structlog.get_logger(__name__)

# Deterministic page-pad rounds allowed before a short book ships as-is. Each
# round is one extra render (free) and pads only the measured deficit.
_MAX_PAD_ROUNDS = 3

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
    "underfilled", "empty gap", "sparse table", "too few rows",
    "single row", "under-filled", "whitespace below",
)


def _is_non_blocking_content_nit(issue: str) -> bool:
    lower = issue.lower()
    return any(p in lower for p in _NON_BLOCKING_PATTERNS)


def blocking_vision_issues(issues: list[str]) -> list[str]:
    """The subset of vision-QA issue strings that indicate a real print defect.
    Shared by the inline gate here and the final-pass gate in the orchestrator."""
    hard = [i for i in issues
            if "(info)" not in i and "(skipped)" not in i
            and not _is_non_blocking_content_nit(i)]
    if settings.layout_qa_block:
        return hard
    return [i for i in hard if any(k in i.lower() for k in _BLOCKING_LAYOUT_HINTS)]


def _render_gated(html: str) -> tuple[bytes, list[str]]:
    """Render HTML → (PDF, warnings) for the narrative path.

    The fit pass inside the renderer audits every text clip-container and
    reports text that is still clipped or overflowing after fitting. With the
    always-ship policy on, residual defects ship as warnings (narrative has no
    block-regen ladder to feed); with it off, this hard-fails as before."""
    pdf_raw, report = render.render_pdf(html)
    pdf = _linearize(pdf_raw)
    warnings = render.layout_warnings(report)
    defects = render.layout_defects(report)
    if defects:
        log.error("layout_audit_failed", defects=defects[:8])
        if not settings.ship_on_layout_defects:
            raise LayoutError(
                "layout audit failed — refusing to ship clipped/overflowing text: "
                + " | ".join(defects[:3])
            )
        warnings.extend(f"layout defect shipped: {d}" for d in defects[:10])
    if report is None:
        # remote render service path: no fit JS ran, so no audit is available
        log.warning("layout_audit_unavailable", reason="render_service_path")
    return pdf, warnings


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
                      safety_data, pro,
                      degraded: bool = False,
                      seed_repairs: dict[str, int] | None = None,
                      ) -> tuple[str, bytes, dict | None, list[str]]:
    """Build + render a practical guide with MEASURED one-shot self-repair.

    Render once, and if the in-page audit reports overflows, compute the exact
    repair for every offender from its measured overflow ratio (chart → exact
    page split, entry → the right flow level, essay → the right squeeze) and
    rebuild with ALL repairs applied — expected clean on the second render,
    with at most two corrective rounds. Fixed display regions (cover/divider/
    safety bands) resolve via the in-render terminal rescue and surface as
    warnings. What remains after 4 rounds raises LayoutError with structured
    defects so the orchestrator can regenerate the offending blocks — unless
    `degraded` is set, in which case the best render ships with the residual
    defects as warnings (the orchestrator's last resort, never a dead job).

    `seed_repairs` pre-loads the repair dict — used by _render_practical_to_floor
    to apply deterministic page padding. Audit-driven repairs then escalate on
    top of the seed, so padding can never mask a genuine overflow."""
    repairs: dict[str, int] = dict(seed_repairs or {})
    defects: list[str] = []
    raw: list[dict] = []
    html = ""
    pdf = b""
    report: dict | None = None
    for _ in range(4):
        html = guide.build_practical_html(
            bible, chapters, images, cover_key=cover_key,
            how_to=layout_budget.trim_how_to(how_to), safety=safety_data, pro=pro,
            repairs=dict(repairs) if repairs else None,
        )
        pdf_raw, report = render.render_pdf(html)
        pdf = _linearize(pdf_raw)
        defects = render.layout_defects(report)
        if not defects:
            if report is None:
                log.warning("layout_audit_unavailable", reason="render_service_path")
            return html, pdf, report, render.layout_warnings(report)
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
    log.error("layout_audit_failed", defects=defects[:8], degraded_ship=degraded)
    if degraded:
        warnings = render.layout_warnings(report)
        warnings.extend(f"layout defect shipped: {d}" for d in defects[:10])
        return html, pdf, report, warnings
    raise LayoutError(
        "layout audit failed — refusing to ship clipped/overflowing text: "
        + " | ".join(defects[:3]),
        defects=raw,
    )


def _render_practical_to_floor(
    bible, chapters, images, *, cover_key, how_to, safety_data, pro,
    degraded: bool = False, page_floor: int = 0,
) -> tuple[str, bytes, dict | None, list[str]]:
    """Render, then deterministically pad until the PDF meets `page_floor`.

    target_pages is a floor, not a ceiling. Content expansion upstream is the
    preferred way to reach it (real prose beats spread-out prose), but when a
    book still renders short this closes the gap at zero API cost by driving
    layout levels through the existing repair channel — see pipeline/page_pad.

    Padding is incremental and stops at the first render that clears the floor,
    so the result lands just above target rather than at the ceiling.
    """
    seed: dict[str, int] = {}
    result = _render_practical(
        bible, chapters, images, cover_key=cover_key, how_to=how_to,
        safety_data=safety_data, pro=pro, degraded=degraded,
    )
    if page_floor <= 0 or bible.book_kind != "practical_guide":
        return result
    profile = bible.profile
    if profile is None:
        return result

    for attempt in range(1, _MAX_PAD_ROUNDS + 1):
        pages = _page_count(result[1])
        if pages >= page_floor:
            if attempt > 1:
                log.info("page_pad_reached_floor", pages=pages, floor=page_floor)
            return result
        pad = page_pad.plan_pad(
            chapters, profile, deficit=page_floor - pages, prev=seed,
        )
        if not pad:
            log.warning("page_pad_exhausted", pages=pages, floor=page_floor)
            return result
        for key, level in pad.items():
            seed[key] = max(seed.get(key, 0), level)
        log.info("page_pad_applied", attempt=attempt, pages_before=pages,
                 floor=page_floor, keys=len(seed))
        result = _render_practical(
            bible, chapters, images, cover_key=cover_key, how_to=how_to,
            safety_data=safety_data, pro=pro, degraded=degraded,
            seed_repairs=seed,
        )

    pages = _page_count(result[1])
    if pages < page_floor:
        log.warning("page_pad_incomplete", pages=pages, floor=page_floor)
    return result


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


def _geometry_gate(html: str, pdf: bytes,
                   warnings: list[str]) -> tuple[bytes, list[str]]:
    """Deterministic geometry gate on the ACTUAL delivered PDF (zero API cost).

    On failure: one full re-render + re-check first (a Chromium hiccup is
    transient); if the defect persists, ship-with-warning under the always-ship
    policy (loud log + operator alert via the warnings channel) or hard-fail
    with SHIP_ON_LAYOUT_DEFECTS=false."""
    issues = pdf_geometry.check_pdf_geometry(pdf)
    if not issues:
        return pdf, warnings
    log.warning("pdf_geometry_retry", issues=issues[:3])
    try:
        pdf_raw, _report = render.render_pdf(html)
        pdf2 = _linearize(pdf_raw)
    except Exception as exc:  # keep the first render if the retry itself dies
        log.warning("pdf_geometry_retry_render_failed", err=str(exc)[:160])
        pdf2 = pdf
    issues2 = pdf_geometry.check_pdf_geometry(pdf2)
    if not issues2:
        return pdf2, warnings
    if settings.ship_on_layout_defects:
        log.error("pdf_geometry_failed_shipping_degraded", issues=issues2[:5])
        return pdf2, warnings + [f"geometry: {g}" for g in issues2[:5]]
    log.error("pdf_geometry_failed", issues=issues2[:5])
    raise GeometryError(
        "PDF geometry gate failed — refusing to ship a broken layout: "
        + " | ".join(issues2[:3])
    )


def rerender(book_id: str) -> BookArtifact:
    """Re-render a practical guide's PDF from its cached manifest (no API cost).

    Applies the current layout/CSS, re-runs the geometry gate, and overwrites the
    stored interior.pdf. Used to apply design fixes without regenerating content.
    Follows the same always-ship policy as the pipeline path."""
    data = json.loads(storage.get(f"books/{book_id}/render_manifest.json"))
    bible = BookBible(**data["bible"])
    chapters = [Chapter(**c) for c in data["chapters"]]
    images = [GeneratedImage(**i) for i in data["images"]]
    html, pdf, report, warnings = _render_practical_to_floor(
        bible, chapters, images, cover_key=data.get("cover_key"),
        how_to=data.get("how_to"), safety_data=data.get("safety"),
        pro=data.get("pro"),
        degraded=settings.ship_on_layout_defects,
        page_floor=bible.target_pages or 0,
    )
    checkpoints.save_fit_report(book_id, report)
    pdf, warnings = _geometry_gate(html, pdf, warnings)
    key = f"books/{book_id}/interior.pdf"
    pages = _page_count(pdf)
    size_mb = round(len(pdf) / 1_048_576, 2)
    storage.put(key, pdf, "application/pdf")
    log.info("rerendered", book_id=book_id, pages=pages, mb=size_mb,
             warnings=len(warnings))
    return BookArtifact(book_id=book_id, interior_pdf_key=key,
                        page_count=pages, file_size_mb=size_mb,
                        qa=QAResult(groundedness=1.0, passed=not warnings,
                                    layout_issues=list(warnings)),
                        warnings=warnings, degraded=bool(warnings))


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
    degraded: bool = False,
) -> BookArtifact:
    warnings: list[str] = []
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
        html, pdf, report, render_warnings = _render_practical_to_floor(
            bible, chapters, images, cover_key=cover_key,
            how_to=how_to, safety_data=safety_data, pro=pro, degraded=degraded,
            page_floor=bible.target_pages or 0,
        )
        warnings.extend(render_warnings)
        # Page-class map of THIS render, for the final vision QA page picker.
        checkpoints.save_fit_report(book_id, report)
        budget_pages = sum(p.page_budget for p in bible.sections)
        # Inline vision QA runs only in legacy mode; the default final-only mode
        # inspects the accepted artifact once, in the orchestrator (cheaper —
        # intermediate page-scale renders are discarded anyway).
        if settings.layout_qa_enabled and not settings.layout_qa_final_only:
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
            blocking = blocking_vision_issues(layout_issues)
            if blocking and (settings.layout_qa_block or bible.profile is not None):
                # A vision-only hit on a layout the deterministic audit passed
                # clean is (observed in prod) usually a false positive — it may
                # only BLOCK when the audit corroborates that something was
                # actually degraded on this render. Uncorroborated hits ship as
                # loud warnings instead of killing the book.
                corroborated = degraded or bool(render_warnings) or bool(
                    (report or {}).get("rescued") or (report or {}).get("stuck"))
                if corroborated and not degraded:
                    qa.passed = False
                    log.error("layout_qa_blocking", issues=blocking[:5])
                    raise LayoutError(
                        "vision layout QA found print defects: " + " | ".join(blocking[:3])
                    )
                warnings.extend(f"vision QA (uncorroborated): {b}"
                                for b in blocking[:5])
                log.warning("layout_qa_uncorroborated_shipped", issues=blocking[:5])
    else:
        html = build.build_html(bible, chapters, images)
        budget_pages = sum(p.page_budget for p in bible.chapters)
        pdf, render_warnings = _render_gated(html)
        warnings.extend(render_warnings)

    # Geometry gate with one re-render retry, then ship-with-warning policy.
    pdf, warnings = _geometry_gate(html, pdf, warnings)

    size_mb = len(pdf) / 1_048_576
    pages = _page_count(pdf)
    issues = list(qa.layout_issues)

    if size_mb > settings.max_file_size_mb:
        issues.append(
            f"file {size_mb:.1f}MB exceeds budget {settings.max_file_size_mb}MB "
            "— recompress images"
        )
    # Page-count limits are OWNED by the orchestrator's scale loops (they see
    # the artifact and can trim/expand); assemble only records advisories.
    target = bible.target_pages or budget_pages or pages
    lo, hi = allowed_page_range(target)
    log.info(
        "page_limit_check",
        pages=pages,
        target=target,
        allowed_lo=lo,
        allowed_hi=hi,
        budget=budget_pages,
    )
    if budget_pages:
        drift = abs(pages - budget_pages) / budget_pages
        log.info("page_budget_check", pages=pages, budget=budget_pages,
                 drift=round(drift, 2))
        if lo <= pages <= hi and drift > 0.30:
            issues.append(
                f"page count {pages} drifts {drift*100:.0f}% from budget "
                f"{budget_pages} — review chapter sizing"
            )
    if not is_within_page_range(pages, target):
        issues.append(
            f"page count {pages} outside target range {lo}–{hi} "
            f"(target {target}) — content may need scaling"
        )

    key = f"books/{book_id}/interior.pdf"
    storage.put(key, pdf, "application/pdf")
    if warnings:
        qa.passed = False
        issues.extend(warnings)
    qa.layout_issues = issues

    art = BookArtifact(book_id=book_id, interior_pdf_key=key, page_count=pages,
                       file_size_mb=round(size_mb, 2), qa=qa,
                       warnings=warnings, degraded=degraded or bool(warnings))
    log.info("assembled", book_id=book_id, pages=pages, mb=art.file_size_mb,
             kind=bible.book_kind, warnings=len(warnings), degraded=art.degraded)
    return art
