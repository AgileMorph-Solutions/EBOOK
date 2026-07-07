"""Image generation helpers for practical guides."""
from __future__ import annotations

import structlog

from bookforge.clients import storage
from bookforge.cost import budget
from bookforge.pipeline import checkpoints
from bookforge.pipeline import images as images_stage
from bookforge.schemas import BookBible, Chapter, GeneratedImage, ImageBrief


def _reattach(storage_key: str, block_ref: str) -> GeneratedImage | None:
    """Rebuild GeneratedImage metadata from a surviving JPEG on disk."""
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(storage.get(storage_key))) as im:
            w, h = im.size
    except Exception:
        return None
    return GeneratedImage(block_ref=block_ref, storage_key=storage_key,
                          width=w, height=h)

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
        target = f"{storage_prefix}/div_{ch.index}.jpg"
        if checkpoints.has_image(target):
            gi = _reattach(target, ref)
            if gi is not None:
                images.append(gi)
                added += 1
                log.info("divider_reattached", key=target, ref=ref)
                continue
        subject = _divider_subject(ch, bible, subjects)
        gi = images_stage.generate_for_brief(
            ImageBrief(prompt=subject, kind="photo", aspect="portrait"),
            bible, target, placement="divider",
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
    *,
    existing: list[GeneratedImage] | None = None,
) -> tuple[list[GeneratedImage], str | None]:
    """Cover, card heroes, and section divider images.

    When ``existing`` is provided (resume path), any image whose storage_key
    already exists on disk is reused verbatim — no LLM call, no quota spend.
    Only missing / failed images are regenerated.
    """
    prefix = f"books/{book_id}"
    images: list[GeneratedImage] = list(existing or [])
    by_ref: dict[str, GeneratedImage] = {gi.block_ref: gi for gi in images if gi.block_ref}
    cover_key: str | None = None

    cover_target = f"{prefix}/cover.jpg"
    if bible.cover_subject:
        if checkpoints.has_image(cover_target):
            cover_key = cover_target
            log.info("image_reused", key=cover_target, kind="cover")
        else:
            cov = images_stage.generate_cover(bible.cover_subject, bible, cover_target)
            if cov:
                cover_key = cov.storage_key

    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            if not block.image_brief:
                continue
            ref = f"{ch.index}.{bi}"
            target = f"{prefix}/img_{ch.index}_{bi}.jpg"
            if ref in by_ref and checkpoints.has_image(by_ref[ref].storage_key):
                log.info("image_reused", key=by_ref[ref].storage_key, ref=ref)
                continue
            if checkpoints.has_image(target):
                gi = _reattach(target, ref)
                if gi is not None:
                    images.append(gi)
                    by_ref[ref] = gi
                    log.info("image_reattached", key=target, ref=ref)
                    continue
            gi = images_stage.generate_for_brief(
                block.image_brief, bible, target, placement="card",
            )
            if gi:
                gi.block_ref = ref
                images.append(gi)
                by_ref[ref] = gi

    ensure_divider_images(chapters, images, bible, prefix)
    log.info("guide_images_done", count=len(images), cover=bool(cover_key))
    return images, cover_key
