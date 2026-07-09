"""Image generation helpers for practical guides."""
from __future__ import annotations

import structlog
from pydantic import BaseModel

from bookforge.clients import llm
from bookforge.config import settings
from bookforge.cost import budget
from bookforge.errors import ImageCoverageError
from bookforge.pipeline import images as images_stage
from bookforge.pipeline import checkpoints
from bookforge.schemas import BookBible, Chapter, GeneratedImage, ImageBrief

log = structlog.get_logger(__name__)


class _Subject(BaseModel):
    subject: str


_SUBJECT_PROMPT = """Write ONE photo subject (a single sentence, no style words)
for the chapter-opener photo panel of the chapter "{title}" in the book
"{book_title}" ({angle}). It must be a CLEAN vertical portrait scene — one
centered subject with margin on all sides — that visually represents this
chapter's content: {summary}
No text, no faces, no people."""


def _divider_subject(ch, bible: BookBible, subjects: dict[int, str]) -> str:
    """Divider photo subject: always from the bible's per-chapter plan; if the
    plan somehow lacks one, a small LLM call derives it from the chapter itself
    — never a canned scene (canned fallbacks leak one book's domain into the
    next)."""
    subject = subjects.get(ch.index) or ""
    if subject.strip():
        return subject.strip()
    profile = bible.profile
    try:
        result = llm.structured(
            _SUBJECT_PROMPT.format(
                title=ch.title, book_title=bible.title,
                angle=(profile.angle if profile else bible.tone) or bible.subtitle,
                summary=next(
                    (b.text for b in ch.blocks if b.text), ch.title),
            ),
            _Subject, max_tokens=300,
        )
        if result.subject.strip():
            return result.subject.strip()
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("divider_subject_fallback", chapter=ch.index,
                    err=str(exc)[:160])
    # last resort: derive from the chapter title alone (still book-specific)
    return (f"a clean, centered vertical scene that represents {ch.title}, "
            f"soft natural light, no people, no text")


def ensure_divider_images(
    chapters: list[Chapter],
    images: list[GeneratedImage],
    bible: BookBible,
    storage_prefix: str,
) -> int:
    """Give every section a divider photo (block_ref '{index}.divider')."""
    have = {gi.block_ref for gi in images}
    subjects = {s.index: (s.divider_subject or "").strip() for s in bible.sections}
    added = 0
    for ch in chapters:
        ref = f"{ch.index}.divider"
        if ref in have or f"{ch.index}.0" in have:  # legacy manifests
            continue
        subject = _divider_subject(ch, bible, subjects)
        gi = images_stage.generate_for_brief(
            ImageBrief(prompt=subject, kind="photo", aspect="portrait"),
            bible, f"{storage_prefix}/div_{ch.index}.jpg",
            placement="divider",
        )
        if gi:
            gi.block_ref = ref
            images.append(gi)
            added += 1
    if added:
        log.info("divider_images_added", added=added, spent=round(budget.spent, 2))
    return added


def required_image_slots(
    bible: BookBible, chapters: list[Chapter],
) -> list[tuple[str, str]]:
    """(slot_id, label) for every image the layout expects for these chapters."""
    slots: list[tuple[str, str]] = []
    if (bible.cover_subject or "").strip():
        slots.append(("cover", "cover photo"))
    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            if block.image_brief:
                slots.append((f"{ch.index}.{bi}", f"card {ch.index}.{bi}"))
        slots.append((f"{ch.index}.divider", f"divider ch{ch.index}"))
    return slots


def _assert_image_coverage(
    bible: BookBible,
    chapters: list[Chapter],
    images: list[GeneratedImage],
    cover_key: str | None,
) -> None:
    """Hard-fail when required photo slots are empty — ebooks must ship with images."""
    floor = settings.image_require_coverage
    if floor <= 0:
        return
    required = required_image_slots(bible, chapters)
    if not required:
        return
    have = {gi.block_ref for gi in images}
    if cover_key:
        have.add("cover")
    missing = [label for slot_id, label in required if slot_id not in have]
    got = len(required) - len(missing)
    coverage = got / len(required)
    if coverage >= floor:
        return
    msg = (f"image coverage {got}/{len(required)} ({coverage:.0%}) below required "
           f"{floor:.0%}: missing {', '.join(missing[:6])}"
           + ("…" if len(missing) > 6 else ""))
    log.error("image_coverage_failed", required=len(required), got=got,
              missing=missing[:10])
    raise ImageCoverageError(msg, required=len(required), got=got, missing=missing)


def generate_all(
    bible: BookBible,
    chapters: list[Chapter],
    book_id: str,
) -> tuple[list[GeneratedImage], str | None]:
    """Cover, card heroes, and section divider images."""
    prefix = f"books/{book_id}"
    images: list[GeneratedImage] = list(checkpoints.load_images(book_id))
    have_refs = {gi.block_ref for gi in images}
    cover_key: str | None = None
    cover_path = f"{prefix}/cover.jpg"
    if checkpoints.has_image(cover_path):
        cover_key = cover_path

    if bible.cover_subject and not cover_key:
        for attempt in (1, 2):
            cov = images_stage.generate_cover(
                bible.cover_subject, bible, f"{prefix}/cover.jpg")
            if cov:
                cover_key = cov.storage_key
                break
            log.warning("cover_generation_failed", attempt=attempt,
                        subject=bible.cover_subject[:80])
        if not cover_key:
            log.error("cover_missing", book_id=book_id)

    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            if not block.image_brief:
                continue
            ref = f"{ch.index}.{bi}"
            if ref in have_refs:
                continue
            key = f"{prefix}/img_{ch.index}_{bi}.jpg"
            if checkpoints.has_image(key):
                images.append(GeneratedImage(
                    block_ref=ref, storage_key=key, width=0, height=0,
                    score=0.9, flagged_for_review=False,
                ))
                have_refs.add(ref)
                continue
            gi = images_stage.generate_for_brief(
                block.image_brief, bible, key,
                placement="card",
            )
            if gi:
                gi.block_ref = ref
                images.append(gi)
            else:
                log.warning("card_image_missing", book_id=book_id, ref=ref)

    ensure_divider_images(chapters, images, bible, prefix)
    _assert_image_coverage(bible, chapters, images, cover_key)
    flagged = sum(1 for gi in images if gi.flagged_for_review)
    log.info("guide_images_done", count=len(images), cover=bool(cover_key),
             flagged=flagged)
    return images, cover_key
