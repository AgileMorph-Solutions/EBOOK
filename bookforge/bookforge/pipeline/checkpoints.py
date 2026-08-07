"""Per-book pipeline checkpoints — persist stage outputs so a failed run can be
resumed without re-paying for LLM work.

Layout (all keys are `books/{book_id}/…` so retries never leak across books):

    books/{id}/source.json      — SourceDoc (skip re-ingest on resume)
    books/{id}/bible.json       — BookBible.model_dump_json()
    books/{id}/sections/NNN.json — one Chapter per finished section (mid-content
                                   crash safety; consolidated into chapters.json)
    books/{id}/chapters.json    — list[Chapter], full-file atomic write
    books/{id}/qa.json          — QAResult keyed to a sha256 of chapters.json,
                                   so resume skips an already-passed gate
    books/{id}/spend.json       — budget spend carried across park/resume cycles
    books/{id}/images.json      — list[GeneratedImage] (metadata + storage_key refs)
    books/{id}/cover.jpg
    books/{id}/div_*.jpg
    books/{id}/img_*.jpg
    books/{id}/fit_report.json  — final render's fit/audit report (page classes)
    books/{id}/interior.pdf     — cleared before layout re-runs

All I/O goes through `bookforge.clients.storage`, so this transparently supports
both the local-filesystem dev store and S3/MinIO in production.
"""
from __future__ import annotations

import hashlib
import json

import structlog

from bookforge.clients import storage
from bookforge.schemas import BookBible, Chapter, GeneratedImage, QAResult, SourceDoc

log = structlog.get_logger(__name__)


def _key(book_id: str, name: str) -> str:
    return f"books/{book_id}/{name}"


# ---------- Source ----------

def save_source(book_id: str, source: SourceDoc) -> None:
    storage.put(_key(book_id, "source.json"),
                source.model_dump_json().encode("utf-8"), "application/json")


def load_source(book_id: str) -> SourceDoc | None:
    key = _key(book_id, "source.json")
    if not storage.exists(key):
        return None
    try:
        return SourceDoc.model_validate_json(storage.get(key))
    except Exception as exc:
        log.warning("source_checkpoint_invalid", book_id=book_id, err=str(exc)[:200])
        return None


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


# ---------- Per-section incremental checkpoints ----------
# Written as each section finishes so a crash mid-content (the longest, most
# expensive stage) only re-spends the unfinished sections on resume.

def save_section(book_id: str, chapter: Chapter) -> None:
    try:
        storage.put(_key(book_id, f"sections/{chapter.index:03d}.json"),
                    chapter.model_dump_json().encode("utf-8"), "application/json")
    except Exception as exc:  # a failed checkpoint must never fail the section
        log.warning("section_checkpoint_save_failed", book_id=book_id,
                    idx=chapter.index, err=str(exc)[:200])


def load_sections(book_id: str) -> list[Chapter]:
    """All per-section checkpoints, ordered by section index."""
    root = storage.local_path(_key(book_id, "sections"))
    if not root.is_dir():
        return []
    out: list[Chapter] = []
    for path in sorted(root.glob("*.json")):
        try:
            out.append(Chapter.model_validate_json(path.read_text()))
        except Exception as exc:
            log.warning("section_checkpoint_invalid", book_id=book_id,
                        file=path.name, err=str(exc)[:200])
    return out


def clear_sections(book_id: str) -> None:
    """Drop per-section checkpoints once consolidated into chapters.json."""
    root = storage.local_path(_key(book_id, "sections"))
    if not root.is_dir():
        return
    for path in root.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            pass


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


# ---------- QA gate result ----------
# Keyed to a hash of the chapters payload, so it self-invalidates whenever any
# chapter changed — a stale gate result can never be replayed onto new content.

def chapters_sha(chapters: list[Chapter]) -> str:
    payload = json.dumps([c.model_dump() for c in chapters],
                         sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def save_qa(book_id: str, chapters: list[Chapter], qa_result: QAResult) -> None:
    try:
        payload = json.dumps({
            "chapters_sha256": chapters_sha(chapters),
            "qa_result": qa_result.model_dump(),
        }).encode("utf-8")
        storage.put(_key(book_id, "qa.json"), payload, "application/json")
    except Exception as exc:
        log.warning("qa_checkpoint_save_failed", book_id=book_id, err=str(exc)[:200])


def load_qa(book_id: str, chapters: list[Chapter]) -> QAResult | None:
    """The persisted gate result — only if it matches these exact chapters."""
    key = _key(book_id, "qa.json")
    if not storage.exists(key):
        return None
    try:
        data = json.loads(storage.get(key))
        if data.get("chapters_sha256") != chapters_sha(chapters):
            return None
        return QAResult.model_validate(data["qa_result"])
    except Exception as exc:
        log.warning("qa_checkpoint_invalid", book_id=book_id, err=str(exc)[:200])
        return None


# ---------- Budget spend (carried across park/resume cycles) ----------

def save_spend(book_id: str, spent_usd: float) -> None:
    try:
        storage.put(_key(book_id, "spend.json"),
                    json.dumps({"spent_usd": round(float(spent_usd), 4)}).encode(),
                    "application/json")
    except Exception as exc:
        log.warning("spend_checkpoint_save_failed", book_id=book_id, err=str(exc)[:200])


def load_spend(book_id: str) -> float:
    key = _key(book_id, "spend.json")
    if not storage.exists(key):
        return 0.0
    try:
        return float(json.loads(storage.get(key)).get("spent_usd", 0.0))
    except Exception as exc:
        log.warning("spend_checkpoint_invalid", book_id=book_id, err=str(exc)[:200])
        return 0.0


# ---------- Final render fit report ----------
# Page-class map of the shipped render, so the final vision QA can rasterize
# the right pages from the stored PDF without a second Chromium render.

def save_fit_report(book_id: str, report: dict | None) -> None:
    if not report:
        return
    try:
        storage.put(_key(book_id, "fit_report.json"),
                    json.dumps(report).encode("utf-8"), "application/json")
    except Exception as exc:
        log.warning("fit_report_save_failed", book_id=book_id, err=str(exc)[:200])


def load_fit_report(book_id: str) -> dict | None:
    key = _key(book_id, "fit_report.json")
    if not storage.exists(key):
        return None
    try:
        return json.loads(storage.get(key))
    except Exception:
        return None


# ---------- Final artifact ----------

def clear_pdf(book_id: str) -> None:
    """Remove the interior PDF so a stale one from a partial prior attempt
    can't shadow the fresh output of the retry."""
    storage.delete(_key(book_id, "interior.pdf"))


# ---------- Full reset (settings changed — old checkpoints no longer apply) ----------

def reset_for_replan(book_id: str, *, keep_source: bool) -> None:
    """Wipe every plan-derived checkpoint before a regenerate with changed
    settings (target_pages, text, toc, density_mode, …). Without this, the
    orchestrator's resume logic would find the OLD bible.json/chapters.json —
    built for the OLD settings — and either reuse a now-wrong plan or corrupt
    it against the new one. `keep_source=False` also clears source.json (the
    source text itself changed, so cached passages are stale too)."""
    if not keep_source:
        storage.delete(_key(book_id, "source.json"))
    storage.delete(_key(book_id, "bible.json"))
    storage.delete(_key(book_id, "chapters.json"))
    storage.delete(_key(book_id, "qa.json"))
    storage.delete(_key(book_id, "images.json"))
    storage.delete(_key(book_id, "fit_report.json"))
    storage.delete(_key(book_id, "render_manifest.json"))
    storage.delete(_key(book_id, "image_quota_park.json"))
    clear_sections(book_id)
    clear_pdf(book_id)
    log.info("checkpoints_reset_for_replan", book_id=book_id, kept_source=keep_source)
