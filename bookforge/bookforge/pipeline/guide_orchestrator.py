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
from bookforge.pipeline import content_guide, guide_images
from bookforge.schemas import Chapter, GeneratedImage

log = structlog.get_logger(__name__)


def _save_manifest(manifest: Path, chapters, images, cover_key, pro, total_uses) -> None:
    manifest.write_text(json.dumps({
        "chapters": [c.model_dump() for c in chapters],
        "images": [i.model_dump() for i in images],
        "cover_key": cover_key, "pro": pro, "uses": total_uses}, indent=1))


def _generate(book, bible, name, limit_sections, with_images, with_pro):
    sections = bible.sections[:limit_sections] if limit_sections else bible.sections
    chapters: list[Chapter] = []
    n = 1
    list_lead = bible.list_lead or (
        "Quick jobs you can do in a minute or two with items already at home."
    )
    for section in sections:
        ch, n = content_guide.write_section(
            section, bible, start_number=n,
            safety_key=book.safety_key,
            list_lead=list_lead,
        )
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
        images, cover_key = guide_images.generate_all(bible, chapters, name)
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
        if with_images and guide_images.ensure_divider_images(chapters, images, bible, f"books/{name}"):
            _save_manifest(manifest, chapters, images, cover_key, pro, total_uses)
    else:
        chapters, images, cover_key, pro, total_uses = _generate(
            book, bible, name, limit_sections, with_images, with_pro)
        _save_manifest(manifest, chapters, images, cover_key, pro, total_uses)

    html = guide.build_practical_html(
        bible, chapters, images, cover_key=cover_key,
        how_to=book.how_to,
        safety={"never_mix": safety.never_mix_for(book.safety_key)},
        pro=pro)
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
