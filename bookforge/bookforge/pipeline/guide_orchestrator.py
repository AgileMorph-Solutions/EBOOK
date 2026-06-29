"""Drive end-to-end generation of a practical guide (Claude content + images +
print PDF). Use `limit_sections` for a cheap quality-proof sample, and `reuse`
to re-render from the cached manifest without re-spending on the API (missing
images, e.g. new section-divider photos, are filled in and cached).
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog

from bookforge import books, safety
from bookforge.cost import budget
from bookforge.layout import guide, render
from bookforge.pipeline import content_guide
from bookforge.pipeline import images as images_stage
from bookforge.schemas import Chapter, GeneratedImage, ImageBrief

log = structlog.get_logger(__name__)


def _ensure_divider_images(chapters, images, bible, name) -> int:
    """Give every section a full-page divider photo (block_ref 'index.0').

    The subject must NOT echo a pest/topic name that would bias the image model
    into drawing the very thing the book is about avoiding (e.g. "Roach
    Prevention" -> roaches). A section can supply an explicit, on-message
    `divider_subject`; otherwise we fall back to a neutral clean-home scene.
    """
    have = {gi.block_ref for gi in images}
    subjects = {s.index: (s.divider_subject or "").strip() for s in bible.sections}
    added = 0
    for ch in chapters:
        ref = f"{ch.index}.0"
        if ref in have:
            continue
        subject = subjects.get(ch.index) or (
            f"{ch.title} in a clean, bright, tidy modern home, wide view, "
            f"soft natural daylight, no people")
        gi = images_stage.generate_for_brief(
            ImageBrief(prompt=subject, kind="photo", aspect="full_bleed"),
            bible, f"books/{name}/div_{ch.index}.jpg")
        if gi:
            gi.block_ref = ref
            images.append(gi)
            added += 1
    if added:
        log.info("divider_images_added", added=added, spent=round(budget.spent, 2))
    return added


def _save_manifest(manifest: Path, chapters, images, cover_key, pro, total_uses) -> None:
    manifest.write_text(json.dumps({
        "chapters": [c.model_dump() for c in chapters],
        "images": [i.model_dump() for i in images],
        "cover_key": cover_key, "pro": pro, "uses": total_uses}, indent=1))


def _generate(book, bible, name, limit_sections, with_images, with_pro):
    sections = bible.sections[:limit_sections] if limit_sections else bible.sections
    chapters: list[Chapter] = []
    n = 1
    for section in sections:
        ch, n = content_guide.write_section(
            section, bible, start_number=n, safety_key=book.safety_key)
        for sp in book.specials_for(section.index):
            if sp.kind == "chart":
                ch.blocks.append(content_guide.make_chart(
                    section.title, sp.heading, sp.columns, sp.rows, bible))
            elif sp.kind == "checklist":
                ch.blocks.append(content_guide.make_checklist(
                    sp.heading, sp.about, sp.items, bible))
        chapters.append(ch)
    total_uses = n - 1
    log.info("content_done", sections=len(chapters), uses=total_uses,
             spent=round(budget.spent, 2))

    images: list[GeneratedImage] = []
    cover_key = None
    if with_images:
        cov = images_stage.generate_cover(book.cover_subject, bible, f"books/{name}/cover.jpg")
        cover_key = cov.storage_key if cov else None
        for ch in chapters:
            for bi, b in enumerate(ch.blocks):
                if not b.image_brief:
                    continue
                ref = f"{ch.index}.{bi}"
                gi = images_stage.generate_for_brief(
                    b.image_brief, bible, f"books/{name}/img_{ch.index}_{bi}.jpg")
                if gi:
                    gi.block_ref = ref
                    images.append(gi)
        _ensure_divider_images(chapters, images, bible, name)
        log.info("images_done", count=len(images), spent=round(budget.spent, 2))

    pro = content_guide.make_pro(book.pro_topic, bible) if with_pro else None
    return chapters, images, cover_key, pro, total_uses


def run_practical_book(
    name: str,
    *,
    limit_sections: int | None = None,
    with_images: bool = True,
    with_pro: bool = True,
    out_dir: str | Path = "artifacts/guides",
    render_pngs: bool = False,
    reuse: bool = False,
) -> dict:
    book = books.load(name)
    bible = book.bible
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.json"

    if reuse and manifest.exists():
        data = json.loads(manifest.read_text())
        chapters = [Chapter.model_validate(c) for c in data["chapters"]]
        images = [GeneratedImage.model_validate(i) for i in data["images"]]
        cover_key, pro, total_uses = data.get("cover_key"), data.get("pro"), data.get("uses", 0)
        log.info("reused_manifest", chapters=len(chapters), images=len(images))
        if with_images and _ensure_divider_images(chapters, images, bible, name):
            _save_manifest(manifest, chapters, images, cover_key, pro, total_uses)
    else:
        chapters, images, cover_key, pro, total_uses = _generate(
            book, bible, name, limit_sections, with_images, with_pro)
        _save_manifest(manifest, chapters, images, cover_key, pro, total_uses)

    never_mix = safety.HP_NEVER_MIX if book.safety_key == "hp" else safety.PEST_NEVER_MIX
    html = guide.build_practical_html(
        bible, chapters, images, cover_key=cover_key,
        how_to=book.how_to, safety={"never_mix": never_mix}, pro=pro)
    (out / f"{name}.html").write_text(html)

    pdf = render.html_to_pdf(html)
    pdf_path = out / f"{name}.pdf"
    pdf_path.write_bytes(pdf)
    result = {"book": name, "sections": len(chapters), "uses": total_uses,
              "pdf": str(pdf_path), "size_mb": round(len(pdf) / 1_048_576, 2),
              "images": len(images), "cost_usd": round(budget.spent, 2)}
    if render_pngs:
        result["pngs"] = len(render.html_to_pngs(html, out / "png", scale=2.0))
    log.info("book_done", **result)
    return result
