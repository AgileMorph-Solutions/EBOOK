"""Image generation helpers for practical guides."""
from __future__ import annotations

import structlog

from bookforge.cost import budget
from bookforge.pipeline import images as images_stage
from bookforge.schemas import BookBible, Chapter, GeneratedImage, ImageBrief

log = structlog.get_logger(__name__)


_FOOD_HINTS = ("rice", "cook", "recipe", "meal", "kitchen", "dinner", "food",
               "supper", "baking", "british")


def _divider_subject(ch, bible: BookBible, subjects: dict[int, str]) -> str:
    subject = subjects.get(ch.index) or ""
    if subject.strip():
        return subject.strip()
    topic = f"{bible.title} {ch.title}".lower()
    if any(w in topic for w in _FOOD_HINTS):
        return (
            f"a single inviting bowl or plate of home-cooked food for {ch.title}, "
            f"centered in frame on a wooden table, soft daylight, shallow depth, "
            f"tidy kitchen background, no people, no text"
        )
    if bible.theme_name == "pest_guard":
        return (
            f"a clean protected home interior related to {ch.title}, "
            f"wide view, soft daylight, no pests visible, no people"
        )
    return (
        f"a clean, bright, tidy modern home scene related to {ch.title}, "
        f"wide view, soft natural daylight, no people"
    )


def ensure_divider_images(
    chapters: list[Chapter],
    images: list[GeneratedImage],
    bible: BookBible,
    storage_prefix: str,
) -> int:
    """Give every section a divider photo (block_ref '{index}.0')."""
    have = {gi.block_ref for gi in images}
    subjects = {s.index: (s.divider_subject or "").strip() for s in bible.sections}
    added = 0
    for ch in chapters:
        ref = f"{ch.index}.0"
        if ref in have:
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

    if bible.cover_subject:
        cov = images_stage.generate_cover(
            bible.cover_subject, bible, f"{prefix}/cover.jpg")
        if cov:
            cover_key = cov.storage_key

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
