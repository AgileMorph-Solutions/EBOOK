"""Per-book pipeline checkpoints — persist stage outputs so a failed run can be
resumed without re-paying for LLM work.

Layout (all keys are `books/{book_id}/…` so retries never leak across books):

    books/{id}/bible.json       — BookBible.model_dump_json()
    books/{id}/chapters.json    — list[Chapter], full-file atomic write
    books/{id}/images.json      — list[GeneratedImage] (metadata + storage_key refs)
    books/{id}/cover.jpg
    books/{id}/div_*.jpg
    books/{id}/img_*.jpg
    books/{id}/interior.pdf     — cleared before layout re-runs

All I/O goes through `bookforge.clients.storage`, so this transparently supports
both the local-filesystem dev store and S3/MinIO in production.
"""
from __future__ import annotations

import json

import structlog

from bookforge.clients import storage
from bookforge.schemas import BookBible, Chapter, GeneratedImage

log = structlog.get_logger(__name__)


def _key(book_id: str, name: str) -> str:
    return f"books/{book_id}/{name}"


# ---------- Bible ----------

def save_bible(book_id: str, bible: BookBible) -> None:
    storage.put(_key(book_id, "bible.json"), bible.model_dump_json().encode("utf-8"),
                "application/json")


def load_bible(book_id: str) -> BookBible | None:
    key = _key(book_id, "bible.json")
    if not storage.exists(key):
        return None
    try:
        return BookBible.model_validate_json(storage.get(key))
    except Exception as exc:
        log.warning("bible_checkpoint_invalid", book_id=book_id, err=str(exc)[:200])
        return None


# ---------- Chapters ----------

def save_chapters(book_id: str, chapters: list[Chapter]) -> None:
    payload = json.dumps([c.model_dump() for c in chapters]).encode("utf-8")
    storage.put(_key(book_id, "chapters.json"), payload, "application/json")


def load_chapters(book_id: str) -> list[Chapter]:
    key = _key(book_id, "chapters.json")
    if not storage.exists(key):
        return []
    try:
        raw = json.loads(storage.get(key))
        return [Chapter.model_validate(c) for c in raw]
    except Exception as exc:
        log.warning("chapters_checkpoint_invalid", book_id=book_id, err=str(exc)[:200])
        return []


# ---------- Images ----------

def save_images(book_id: str, images: list[GeneratedImage]) -> None:
    payload = json.dumps([i.model_dump() for i in images]).encode("utf-8")
    storage.put(_key(book_id, "images.json"), payload, "application/json")


def load_images(book_id: str) -> list[GeneratedImage]:
    key = _key(book_id, "images.json")
    if not storage.exists(key):
        return []
    try:
        raw = json.loads(storage.get(key))
        return [GeneratedImage.model_validate(i) for i in raw]
    except Exception as exc:
        log.warning("images_checkpoint_invalid", book_id=book_id, err=str(exc)[:200])
        return []


def has_image(storage_key: str) -> bool:
    """True when the given image key is already materialized on disk / S3.

    Used by the image loop to skip briefs whose output survived a prior attempt.
    """
    return storage.exists(storage_key)


# ---------- Final artifact ----------

def clear_pdf(book_id: str) -> None:
    """Remove the interior PDF so a stale one from a partial prior attempt
    can't shadow the fresh output of the retry."""
    storage.delete(_key(book_id, "interior.pdf"))
