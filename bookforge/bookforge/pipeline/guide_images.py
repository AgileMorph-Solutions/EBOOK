"""Image generation helpers for practical guides.

Never-fail policy: an image-provider quota/outage parks the book ONCE per
IMAGE_QUOTA_MAX_PARKS (daily quotas park until the actual reset — worth the
wait for a fully-illustrated book); after that the book ships with the images
it has plus a warning. The layout renders empty slots cleanly, so a missing
photo is a quality warning, never a dead job.
"""
from __future__ import annotations

import json
import threading

import structlog
from pydantic import BaseModel

from bookforge.clients import images as img_client
from bookforge.clients import llm, storage
from bookforge.config import settings
from bookforge.cost import budget
from bookforge.errors import ImageCoverageError  # noqa: F401 — legacy import surface
from bookforge.pipeline import images as images_stage
from bookforge.pipeline import checkpoints
from bookforge.pipeline.pools import ctx_pool
from bookforge.schemas import BookBible, Chapter, GeneratedImage, ImageBrief

log = structlog.get_logger(__name__)


# ---------- quota-park accounting (persisted per book) ----------

def _quota_parks_key(book_id: str) -> str:
    return f"books/{book_id}/image_quota_park.json"


def _load_quota_parks(book_id: str) -> int:
    key = _quota_parks_key(book_id)
    if not storage.exists(key):
        return 0
    try:
        return int(json.loads(storage.get(key)).get("parks", 0))
    except Exception:
        return 0


def _save_quota_parks(book_id: str, parks: int) -> None:
    try:
        storage.put(_quota_parks_key(book_id),
                    json.dumps({"parks": parks}).encode(), "application/json")
    except Exception as exc:
        log.warning("quota_parks_save_failed", book_id=book_id, err=str(exc)[:120])


def _clear_quota_parks(book_id: str) -> None:
    storage.delete(_quota_parks_key(book_id))


def _stop_or_raise(book_id: str, exc: Exception) -> str:
    """Decide between parking the book (re-raise) and shipping partial.

    API jobs (JobRun bound) park on the first IMAGE_QUOTA_MAX_PARKS provider
    failures so Celery can resume after the quota resets. CLI spikes have no
    resume loop — they always return a warning and ship with partial images."""
    from bookforge.pipeline import runctx

    parks = _load_quota_parks(book_id)
    if (runctx.active_job() is not None
            and parks < settings.image_quota_max_parks):
        _save_quota_parks(book_id, parks + 1)
        raise exc
    if isinstance(exc, img_client.ImageQuotaError):
        return (f"image quota exhausted after {parks} wait(s) — shipped with "
                "the images generated so far")
    return (f"image provider unavailable ({str(exc)[:80]}) — shipped with "
            "the images generated so far")


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
        if llm.never_swallow(exc):
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
    *,
    skip_generation: bool = False,
) -> int:
    """Give every section a divider photo (block_ref '{index}.divider')."""
    have = {gi.block_ref for gi in images}
    subjects = {s.index: (s.divider_subject or "").strip() for s in bible.sections}
    added = 0
    todo: list[tuple[Chapter, str, str]] = []
    for ch in chapters:
        ref = f"{ch.index}.divider"
        if ref in have or f"{ch.index}.0" in have:  # legacy manifests
            continue
        key = f"{storage_prefix}/div_{ch.index}.jpg"
        if checkpoints.has_image(key):
            images.append(GeneratedImage(
                block_ref=ref, storage_key=key, width=0, height=0,
                score=0.9, flagged_for_review=False,
            ))
            added += 1
            continue
        if skip_generation:
            continue
        todo.append((ch, ref, key))

    if todo:
        def _one(item: tuple[Chapter, str, str]) -> GeneratedImage | None:
            ch, ref, key = item
            subject = _divider_subject(ch, bible, subjects)
            gi = images_stage.generate_for_brief(
                ImageBrief(prompt=subject, kind="photo", aspect="portrait"),
                bible, key,
                placement="divider",
            )
            if gi:
                gi.block_ref = ref
            return gi

        with ctx_pool(settings.image_concurrency, "divimg") as pool:
            for gi in pool.map(_one, todo):
                if gi:
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


def _coverage_warning(
    bible: BookBible,
    chapters: list[Chapter],
    images: list[GeneratedImage],
    cover_key: str | None,
) -> str | None:
    """Warning string when required photo slots are empty (None = coverage OK).

    Used to hard-fail via ImageCoverageError; the layout renders empty slots
    cleanly, so shipping with a warning beats killing a finished book."""
    floor = settings.image_require_coverage
    if floor <= 0:
        return None
    required = required_image_slots(bible, chapters)
    if not required:
        return None
    have = {gi.block_ref for gi in images}
    if cover_key:
        have.add("cover")
    missing = [label for slot_id, label in required if slot_id not in have]
    got = len(required) - len(missing)
    coverage = got / len(required)
    if coverage >= floor:
        return None
    msg = (f"image coverage {got}/{len(required)} ({coverage:.0%}) below required "
           f"{floor:.0%}: missing {', '.join(missing[:6])}"
           + ("…" if len(missing) > 6 else ""))
    log.warning("image_coverage_short", required=len(required), got=got,
                missing=missing[:10])
    return msg


def generate_all(
    bible: BookBible,
    chapters: list[Chapter],
    book_id: str,
    warnings: list[str] | None = None,
) -> tuple[list[GeneratedImage], str | None]:
    """Cover, card heroes, and section divider images.

    Provider quota/outage handling: park (re-raise) for the first
    IMAGE_QUOTA_MAX_PARKS failures, then stop generating and ship partial —
    already-materialized images are always collected either way. Coverage
    shortfalls are warnings, never failures."""
    prefix = f"books/{book_id}"
    images: list[GeneratedImage] = list(checkpoints.load_images(book_id))
    have_refs = {gi.block_ref for gi in images}
    cover_key: str | None = None
    cover_path = f"{prefix}/cover.jpg"
    if checkpoints.has_image(cover_path):
        cover_key = cover_path
    stop_warning: str | None = None

    if bible.cover_subject and not cover_key:
        try:
            for attempt in (1, 2):
                cov = images_stage.generate_cover(
                    bible.cover_subject, bible, f"{prefix}/cover.jpg")
                if cov:
                    cover_key = cov.storage_key
                    break
                log.warning("cover_generation_failed", attempt=attempt,
                            subject=bible.cover_subject[:80])
        except (img_client.ImageQuotaError, img_client.ImageGenerationError) as exc:
            stop_warning = _stop_or_raise(book_id, exc)
        if not cover_key:
            log.error("cover_missing", book_id=book_id)

    # Flat card-slot work list first (skips run during list construction), then
    # a bounded pool — slots are fully independent, and serial image calls were
    # the single largest wall-clock cost of a book.
    card_tasks: list[tuple[str, str, object]] = []
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
            card_tasks.append((ref, key, block.image_brief))

    if card_tasks and stop_warning is None:
        images_lock = threading.Lock()
        provider_down = threading.Event()
        first_provider_exc: list[Exception] = []

        def _slot(task: tuple[str, str, object]) -> None:
            ref, key, brief = task
            if provider_down.is_set():
                return
            try:
                gi = images_stage.generate_for_brief(
                    brief, bible, key, placement="card",
                )
            except (img_client.ImageQuotaError,
                    img_client.ImageGenerationError) as exc:
                provider_down.set()
                with images_lock:
                    if not first_provider_exc:
                        first_provider_exc.append(exc)
                return
            if gi:
                gi.block_ref = ref
                with images_lock:
                    images.append(gi)
                    # Incremental checkpoint: a park/crash mid-stage loses at
                    # most the in-flight slots, never finished ones.
                    checkpoints.save_images(book_id, images)
            else:
                log.warning("card_image_missing", book_id=book_id, ref=ref)

        with ctx_pool(settings.image_concurrency, "cardimg") as pool:
            list(pool.map(_slot, card_tasks))
        if first_provider_exc:
            stop_warning = _stop_or_raise(book_id, first_provider_exc[0])

    try:
        ensure_divider_images(chapters, images, bible, prefix,
                              skip_generation=stop_warning is not None)
    except (img_client.ImageQuotaError, img_client.ImageGenerationError) as exc:
        stop_warning = _stop_or_raise(book_id, exc)
        ensure_divider_images(chapters, images, bible, prefix,
                              skip_generation=True)

    coverage_warning = _coverage_warning(bible, chapters, images, cover_key)
    if warnings is not None:
        if stop_warning:
            warnings.append(stop_warning)
        if coverage_warning:
            warnings.append(coverage_warning)
    if stop_warning is None and coverage_warning is None:
        _clear_quota_parks(book_id)
    flagged = sum(1 for gi in images if gi.flagged_for_review)
    log.info("guide_images_done", count=len(images), cover=bool(cover_key),
             flagged=flagged, partial=bool(stop_warning))
    return images, cover_key
