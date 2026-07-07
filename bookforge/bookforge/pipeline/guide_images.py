"""Image generation helpers for practical guides."""
from __future__ import annotations

import structlog
from pydantic import BaseModel

from bookforge.clients import llm
from bookforge.cost import budget
from bookforge.pipeline import images as images_stage
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


def generate_all(
    bible: BookBible,
    chapters: list[Chapter],
    book_id: str,
) -> tuple[list[GeneratedImage], str | None]:
    """Cover, card heroes, and section divider images."""
    prefix = f"books/{book_id}"
    images: list[GeneratedImage] = []
    cover_key: str | None = None

    # The cover is the product's face — a silent failure used to fall back to
    # an arbitrary tip photo. Retry once, then flag loudly for review.
    if bible.cover_subject:
        for attempt in (1, 2):
            cov = images_stage.generate_cover(
                bible.cover_subject, bible, f"{prefix}/cover.jpg")
            if cov:
                cover_key = cov.storage_key
                break
            log.warning("cover_generation_failed", attempt=attempt,
                        subject=bible.cover_subject[:80])
        if not cover_key:
            log.error("cover_missing_flagged_for_review", book_id=book_id)

    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            if not block.image_brief:
                continue
            ref = f"{ch.index}.{bi}"
            gi = images_stage.generate_for_brief(
                block.image_brief, bible, f"{prefix}/img_{ch.index}_{bi}.jpg",
                placement="card",
            )
            if gi:
                gi.block_ref = ref
                images.append(gi)

    ensure_divider_images(chapters, images, bible, prefix)
    log.info("guide_images_done", count=len(images), cover=bool(cover_key))
    return images, cover_key
