"""FastAPI — job submission, status polling, and PDF delivery.

GET  /books                    → list recent jobs (newest first)
POST /books                    → start a pipeline job, returns book_id (topic slug)
GET  /books/{book_id}          → poll status: queued | running | done | failed
GET  /books/{book_id}/download → stream the finished PDF
POST /books/upload             → multipart file upload (alternative to JSON text)
GET  /health                   → liveness probe
GET  /readiness                → pre-queue generation readiness
POST /readiness                → readiness with request sanity checks
GET  /audio-scripts            → list recent audio-script jobs
POST /audio-scripts            → start script+TTS job, returns script_id + link
GET  /audio-scripts/{script_id}→ poll status; transcript + audio_link when done
GET  /audio-scripts/{script_id}/audio → stream narration.mp3

Job state is persisted to {ARTIFACTS_ROOT}/books/{book_id}/status.json so it
survives API restarts and container rebuilds. Audio scripts use
{ARTIFACTS_ROOT}/audio-scripts/{script_id}/.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import BoundedSemaphore, Lock, Thread, current_thread
from typing import Any, Literal

import anthropic
import structlog
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from bookforge.clients import llm, storage
from bookforge.config import get_settings
from bookforge.cost import BudgetExceeded
from bookforge.errors import (
    ContentQAError, EmptyBookError, GeometryError, ImageCoverageError,
    IngestError, JobDeadlineError, PageLimitError, PipelineCancelledError,
    ReadinessError,
)
from bookforge.pipeline import runctx
from bookforge.pipeline.readiness import (
    ReadinessRequest,
    ReadinessResult,
    check_readiness,
    writing_provider_status,
)
from bookforge.pipeline.audio_script import generate_audio_script, resolve_item_count
from bookforge.pipeline.audio_script_stages import AudioScriptStage, stages_payload
from bookforge.pipeline.audio_tts import generate_narration_audio
from bookforge.clients.elevenlabs import ElevenLabsError
from bookforge.schemas import AudioScriptResult

log = structlog.get_logger(__name__)

API_VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    """Recover orphaned jobs and start the background auto-resume loop.

    Jobs interrupted by a restart (queued/running) are re-queued for automatic
    resume — a restart is invisible to the user — unless a job has already been
    resumed too many times (crash-loop guard), in which case it's failed."""
    s = get_settings()
    missing = [
        name for name, val in (
            ("ANTHROPIC_API_KEY", s.anthropic_api_key),
            ("GEMINI_API_KEY", s.gemini_api_key),
        ) if not val
    ]
    if missing:
        log.error("missing_required_keys", missing=missing)

    resumed = failed = 0
    books_dir = _ARTIFACTS / "books"
    if books_dir.is_dir():
        for path in books_dir.glob("*/status.json"):
            book_id = path.parent.name
            state = _read_state(book_id)
            if not state or state.get("status") not in ("queued", "running"):
                continue
            attempts = int(state.get("resume_attempts", 0)) + 1
            if attempts > s.max_resume_attempts or _read_request(book_id) is None:
                _set_state(book_id, status="failed",
                           error=_friendly_error(RuntimeError("restart")),
                           error_detail="interrupted by server restart; not resumable")
                failed += 1
            else:
                # Park it so the resume loop picks it up immediately.
                _set_state(book_id, status="waiting", resume_attempts=attempts,
                           wait_reason="resuming after server restart",
                           next_retry_at=_now_iso())
                resumed += 1
    log.info("startup", version=API_VERSION, artifacts=str(_ARTIFACTS),
             resumed_orphans=resumed, failed_orphans=failed,
             max_concurrent=s.max_concurrent_books)

    resume_task = asyncio.create_task(_resume_loop())
    try:
        yield
    finally:
        resume_task.cancel()
        try:
            await resume_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="BookForge API", version=API_VERSION, lifespan=lifespan)

# Serialize expensive pipeline runs so concurrent submissions can't exhaust
# Chromium/RAM on a single node. Extra jobs stay "queued" until a slot frees.
_pipeline_sem = BoundedSemaphore(max(1, get_settings().max_concurrent_books))

# Resumed (previously parked) jobs may hold at most this many slots, so a
# resume storm during a long provider outage can never starve fresh
# submissions of every slot (observed live: both slots held by quota-loopers).
_resume_slots = BoundedSemaphore(
    max(1, min(get_settings().resume_max_concurrent,
               get_settings().max_concurrent_books)))

# Worker threads by book_id, so the watchdog can tell a live slow job from a
# dead one whose thread is gone.
_active_jobs: dict[str, Thread] = {}
_active_jobs_lock = Lock()

_cors_origins = os.environ.get(
    "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = Lock()

_ARTIFACTS = Path(os.environ.get("BOOKFORGE_LOCAL_STORE", "./artifacts")).resolve()


# --------------------------------------------------------------------------- #
# Job state — persisted to disk so restarts don't lose running/done jobs
# --------------------------------------------------------------------------- #

def _state_path(book_id: str) -> Path:
    return _ARTIFACTS / "books" / book_id / "status.json"


def _write_state(book_id: str, state: dict[str, Any]) -> None:
    path = _state_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace: a crash mid-write must never leave a truncated status.json
    # (readers treat unparseable state as "no job").
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def _read_state(book_id: str) -> dict[str, Any] | None:
    path = _state_path(book_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _set_state(book_id: str, **kwargs: Any) -> None:
    with _lock:
        state = _read_state(book_id) or {}
        state.update(kwargs)
        _write_state(book_id, state)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# The full submission payload (incl. source text/URL) is persisted alongside the
# status so a parked or orphaned job can be re-run later — possibly in a fresh
# process after a restart — without the original in-memory request.

def _request_path(book_id: str) -> Path:
    return _ARTIFACTS / "books" / book_id / "request.json"


def _cancel_path(book_id: str) -> Path:
    return _ARTIFACTS / "books" / book_id / "cancelled.flag"


def _is_cancelled(book_id: str) -> bool:
    return _cancel_path(book_id).exists()


def _request_cancel(book_id: str) -> None:
    path = _cancel_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_now_iso())


def _clear_cancelled(book_id: str) -> None:
    try:
        _cancel_path(book_id).unlink(missing_ok=True)
    except OSError:
        pass


def _write_request(book_id: str, req: "CreateBook") -> None:
    path = _request_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(req.model_dump_json())


def _read_request(book_id: str) -> "CreateBook | None":
    path = _request_path(book_id)
    if not path.exists():
        return None
    try:
        return CreateBook.model_validate_json(path.read_text())
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_book_id(topic: str) -> str:
    """Slug from topic + 6-char random suffix, e.g. 'forgotten-british-meals-a3f2c1'."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def _list_jobs() -> list[dict[str, Any]]:
    """Scan artifact dirs for status.json files, newest first."""
    books_dir = _ARTIFACTS / "books"
    if not books_dir.is_dir():
        return []
    jobs: list[dict[str, Any]] = []
    for path in books_dir.glob("*/status.json"):
        try:
            state = json.loads(path.read_text())
        except Exception:
            continue
        book_id = path.parent.name
        jobs.append({
            "book_id": book_id,
            "topic": state.get("topic", ""),
            "status": state.get("status", "unknown"),
            "created_at": state.get("created_at", ""),
            "target_pages": state.get("target_pages"),
            "chapter_limit": state.get("chapter_limit"),
            "book_kind": state.get("book_kind"),
        })
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs


def _count_active_jobs() -> int:
    return sum(
        1 for j in _list_jobs()
        if j.get("status") in ("queued", "running")
    )


def _to_readiness_request(req: CreateBook) -> ReadinessRequest:
    return ReadinessRequest.model_validate(req.model_dump())


def _assert_can_generate(req: CreateBook | None = None) -> None:
    result = check_readiness(
        include_request=_to_readiness_request(req) if req else None,
        active_jobs=_count_active_jobs(),
    )
    if not result.can_generate:
        raise HTTPException(status_code=503, detail=result.model_dump())


# --------------------------------------------------------------------------- #
# Progress / ETA estimation
# --------------------------------------------------------------------------- #

# Ordered pipeline stages per book kind, with rough wall-clock weights (fraction
# of total run time). These drive the UI progress bar + ETA. Weights need not be
# exact: within-stage progress is time-interpolated, and the ETA self-corrects
# from the actual elapsed pace as the run proceeds.
_STAGE_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    # Guides run QA (and its fix-loop) BEFORE images, so it sits between content
    # and images and carries real weight — the loop can regenerate sections.
    "practical_guide": [
        ("ingest", 0.03), ("bible", 0.07), ("content", 0.42),
        ("qa", 0.13), ("images", 0.30), ("layout", 0.05),
    ],
    "narrative": [
        ("ingest", 0.03), ("bible", 0.07), ("content", 0.40),
        ("images", 0.30), ("qa", 0.15), ("layout", 0.05),
    ],
}


def _normalize_stage(stage: str | None) -> str | None:
    """Collapse the fix-loops' dynamic stage names onto their parent bands so
    the progress bar advances instead of resetting to 0: "revise (N)" → "qa",
    "layout-revise (N)" → "layout"."""
    if stage and stage.startswith("layout-revise"):
        return "layout"
    if stage and stage.startswith("revise"):
        return "qa"
    return stage
# Seed estimate before enough elapsed data exists (base + per target page).
_EXPECTED_BASE_S = 60.0
_EXPECTED_PER_PAGE_S = 3.5


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _with_progress(state: dict[str, Any]) -> dict[str, Any]:
    """Attach progress (0..1), eta_seconds, elapsed_seconds, stage index/total."""
    st = dict(state)
    status = st.get("status")
    stages = _STAGE_WEIGHTS.get(
        st.get("book_kind") or "practical_guide", _STAGE_WEIGHTS["practical_guide"]
    )
    names = [n for n, _ in stages]
    st["stage_total"] = len(names)

    if status == "done":
        st["progress"] = 1.0
        st["eta_seconds"] = 0
        return st
    if status not in ("queued", "running"):
        return st

    now = datetime.now(timezone.utc)
    started = _parse_ts(st.get("started_at"))
    elapsed = (now - started).total_seconds() if started else 0.0
    st["elapsed_seconds"] = round(elapsed)
    expected_total = _EXPECTED_BASE_S + _EXPECTED_PER_PAGE_S * float(
        st.get("target_pages") or 100
    )

    cur = _normalize_stage(st.get("stage"))
    idx = names.index(cur) if cur in names else -1
    st["stage_index"] = idx

    if status == "queued" or idx < 0:
        st["progress"] = 0.0
        st["eta_seconds"] = round(expected_total)
        return st

    w_done = sum(w for _, w in stages[:idx])
    w_cur = stages[idx][1]
    stage_started = _parse_ts(st.get("stage_started_at")) or started or now
    stage_elapsed = (now - stage_started).total_seconds()
    expected_stage = max(1.0, expected_total * w_cur)
    frac = min(0.98, stage_elapsed / expected_stage)
    progress = min(0.99, w_done + w_cur * frac)
    st["progress"] = round(progress, 4)

    # ETA self-corrects from the real pace once we have meaningful progress.
    if progress > 0.02 and elapsed > 0:
        eta = elapsed * (1.0 - progress) / progress
    else:
        eta = max(0.0, expected_total - elapsed)
    st["eta_seconds"] = max(0, round(eta))
    return st


# --------------------------------------------------------------------------- #
# Request schema
# --------------------------------------------------------------------------- #

class CreateBook(BaseModel):
    topic: str
    text: str | None = None
    youtube_url: str | None = None
    target_pages: int = Field(100, gt=0, le=300)
    chapter_limit: int | None = None
    section_limit: int | None = None
    book_kind: Literal["practical_guide"] = "practical_guide"
    # Optional book description (angle, audience, themes) injected into planning prompts.
    description: str | None = None
    # Optional user-supplied table of contents: list of chapter/section titles the
    # planner must follow verbatim. Empty list or None → planner auto-plans.
    toc: list[str] | None = None
    # Optional EditorialProfile field overrides (e.g. {"genre": "cookbook",
    # "audience": "..."}) applied after the planning model derives the profile.
    profile_overrides: dict | None = None

    @model_validator(mode="after")
    def require_source(self) -> "CreateBook":
        # Reject empty/whitespace-only text up front so a guaranteed-to-fail job
        # never gets queued (empty text can't produce a grounded book).
        if self.text is not None and not self.text.strip():
            self.text = None
        if not self.text and not self.youtube_url:
            raise ValueError("Provide either 'text' or 'youtube_url'")
        # Normalize toc: drop blank entries, set to None if empty.
        if self.toc is not None:
            self.toc = [t.strip() for t in self.toc if t.strip()] or None
        return self


def _enqueue_book(
    req: CreateBook,
    background_tasks: BackgroundTasks,
    *,
    source_filename: str | None = None,
) -> dict[str, str]:
    book_id = _make_book_id(req.topic)
    state: dict[str, Any] = {
        "status": "queued",
        "topic": req.topic,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_pages": req.target_pages,
        "chapter_limit": req.chapter_limit,
        "section_limit": req.section_limit,
        "book_kind": req.book_kind,
        "source_type": "youtube" if req.youtube_url else "text",
    }
    if source_filename:
        state["source_filename"] = source_filename
    _set_state(book_id, **state)
    _write_request(book_id, req)  # persisted so parked/orphaned jobs can resume
    background_tasks.add_task(_run_pipeline, book_id, req)
    return {"book_id": book_id, "status": "queued"}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

# Cached provider liveness ping: kept for tests that patch _provider_ok.
_provider_ping_cache: dict[str, Any] = {
    "deadline": 0.0, "ok": None, "detail": "", "unfunded": False,
}


def _provider_ok() -> tuple[bool | None, str, bool]:
    """Whether Anthropic can be reached — delegates to readiness writing check."""
    return writing_provider_status()


@app.get("/readiness", response_model=ReadinessResult)
def get_readiness():
    """Service readiness (writing, images, layout, capacity)."""
    return check_readiness(active_jobs=_count_active_jobs())


@app.post("/readiness", response_model=ReadinessResult)
def post_readiness(req: CreateBook):
    """Readiness including request-sanity checks for the create form."""
    return check_readiness(
        include_request=_to_readiness_request(req),
        active_jobs=_count_active_jobs(),
    )


@app.get("/health")
def health():
    s = get_settings()
    provider_ok, provider_detail, unfunded = _provider_ok()
    readiness = check_readiness(active_jobs=_count_active_jobs())
    return {
        "ok": bool(provider_ok) and bool(s.gemini_api_key) and readiness.can_generate,
        "version": API_VERSION,
        "storage": "s3" if os.environ.get("BOOKFORGE_USE_S3") == "1" else "local",
        "render": "render_service" if os.environ.get("RENDER_URL") else "playwright",
        "anthropic_key": bool(s.anthropic_api_key),
        "gemini_key": bool(s.gemini_api_key),
        "provider_ok": provider_ok,
        "provider_detail": provider_detail,
        "unfunded": unfunded,
        "can_generate": readiness.can_generate,
        "reason_code": readiness.reason_code,
        "max_concurrent_books": s.max_concurrent_books,
    }


@app.get("/books")
def list_books():
    """Recent book jobs, newest first."""
    return {"books": _list_jobs()}


@app.post("/books", status_code=202)
def create_book(req: CreateBook, background_tasks: BackgroundTasks):
    """Submit a book job. Returns immediately with a readable book_id.

    Poll GET /books/{book_id} for progress, then GET /books/{book_id}/download.
    """
    _assert_can_generate(req)
    return _enqueue_book(req, background_tasks)


@app.post("/books/upload", status_code=202)
async def create_book_upload(
    background_tasks: BackgroundTasks,
    topic: str = Form(...),
    file: UploadFile = File(...),
    target_pages: int = Form(100),
    chapter_limit: int | None = Form(None),
    section_limit: int | None = Form(None),
    book_kind: Literal["practical_guide"] = Form("practical_guide"),
    description: str | None = Form(None),
    toc: str | None = Form(None),
):
    """Submit a book job via multipart file upload."""
    if not topic.strip():
        raise HTTPException(status_code=422, detail="topic is required")

    raw = await file.read()
    max_bytes = get_settings().max_source_size_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {get_settings().max_source_size_mb} MB",
        )
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status_code=422, detail="File has no readable text content")

    # toc arrives as a JSON-encoded string list from the multipart form.
    toc_list: list[str] | None = None
    if toc:
        import json as _json
        try:
            toc_list = _json.loads(toc)
        except Exception:
            toc_list = [t.strip() for t in toc.split("\n") if t.strip()]

    req = CreateBook(
        topic=topic.strip(),
        text=text,
        target_pages=target_pages,
        chapter_limit=chapter_limit,
        section_limit=section_limit,
        book_kind=book_kind,
        description=description or None,
        toc=toc_list,
    )
    _assert_can_generate(req)
    return _enqueue_book(
        req,
        background_tasks,
        source_filename=file.filename or "upload.txt",
    )


@app.get("/books/{book_id}")
def get_book(book_id: str):
    """Current job status and, when done, artifact metadata."""
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    req = _read_request(book_id)
    if req is not None:
        if req.description:
            state["description"] = req.description
        if req.toc:
            state["toc"] = req.toc
    return {"book_id": book_id, **_with_progress(state)}


@app.get("/books/{book_id}/download")
def download(book_id: str):
    """Download the finished PDF. Returns 409 if the job isn't done yet."""
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    if state.get("status") != "done":
        raise HTTPException(
            status_code=409,
            detail=f"book not ready — status: {state.get('status')}",
        )
    key = f"books/{book_id}/interior.pdf"
    if not storage.exists(key):
        raise HTTPException(status_code=404, detail="PDF not found in storage")
    # Stream from disk — 60MB-class PDFs must not round-trip through process
    # memory (this process is also the render worker).
    return FileResponse(
        path=storage.local_path(key),
        media_type="application/pdf",
        filename=f"{book_id}.pdf",
    )


@app.post("/books/{book_id}/rerender", status_code=202)
def rerender_book(book_id: str, background_tasks: BackgroundTasks):
    """Re-render a book's PDF from its cached render manifest (zero LLM/image
    cost) and mark it done. The ops path for layout/CSS fixes: a failed-at-layout
    book whose content and images are already stored finishes without
    regenerating anything."""
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    if state.get("status") not in ("failed", "done"):
        raise HTTPException(
            status_code=409,
            detail=f"book is {state.get('status')} — rerender applies to "
                   "failed or done books only",
        )
    try:
        storage.get(f"books/{book_id}/render_manifest.json")
    except Exception:
        raise HTTPException(status_code=409,
                            detail="no render manifest stored for this book")

    def _do_rerender() -> None:
        from bookforge.pipeline import assemble
        # Chromium renders of 300-page books are exactly what the pipeline
        # semaphore exists for — rerenders must not stack on top of running
        # books (that's how the box OOM'd).
        _set_state(book_id, status="queued", stage="layout")
        with _pipeline_sem:
            try:
                _set_state(book_id, status="running", stage="layout",
                           stage_started_at=_now_iso(), heartbeat_at=_now_iso())
                artifact = assemble.rerender(book_id)
                _mark_done(book_id, artifact)
                log.info("rerender_done", book_id=book_id, pages=artifact.page_count)
            except Exception as exc:
                log.exception("rerender_failed", book_id=book_id)
                _fail(book_id, exc, _failure_class(exc))

    background_tasks.add_task(_do_rerender)
    return {"book_id": book_id, "status": "queued", "stage": "layout"}


@app.post("/books/{book_id}/regenerate", status_code=202)
def regenerate_book(book_id: str, background_tasks: BackgroundTasks):
    """Recover a failed job the smart way: reuse whatever work already exists
    instead of starting over from scratch.

    - If a render manifest is stored (failure happened at/after the layout
      stage — content and images are already generated), this is a zero
      LLM/image-cost re-render, identical to POST /rerender.
    - Otherwise (failure happened at ingest/bible/content, before anything
      reusable was cached), falls back to a full pipeline re-run from the
      original request — same params the book was originally submitted with,
      same book_id, so job history and the URL stay the same instead of
      minting a new book.
    """
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    if state.get("status") not in ("failed", "waiting"):
        raise HTTPException(
            status_code=409,
            detail=f"book is {state.get('status')} — regenerate applies to "
                   "failed or waiting books only",
        )

    has_manifest = storage.exists(f"books/{book_id}/render_manifest.json")
    if has_manifest:
        def _do_rerender() -> None:
            from bookforge.pipeline import assemble
            _set_state(book_id, status="queued", stage="layout")
            with _pipeline_sem:
                try:
                    _set_state(book_id, status="running", stage="layout",
                               stage_started_at=_now_iso(), heartbeat_at=_now_iso())
                    artifact = assemble.rerender(book_id)
                    _mark_done(book_id, artifact)
                    log.info("regenerate_rerendered", book_id=book_id,
                             pages=artifact.page_count)
                except Exception as exc:
                    log.exception("regenerate_rerender_failed", book_id=book_id)
                    _fail(book_id, exc, _failure_class(exc))

        background_tasks.add_task(_do_rerender)
        return {"book_id": book_id, "status": "queued", "stage": "layout",
                "mode": "rerender"}

    req = _read_request(book_id)
    if req is None:
        raise HTTPException(
            status_code=409,
            detail="no render manifest or original request stored for this "
                   "book — it cannot be regenerated; create a new book instead",
        )
    _set_state(book_id, status="queued", stage=None, attempts=0,
               resume_attempts=0, quota_parks=0, **_DONE_CLEARS)
    background_tasks.add_task(_run_pipeline, book_id, req)
    return {"book_id": book_id, "status": "queued", "mode": "full_rerun"}


@app.post("/books/{book_id}/cancel", status_code=200)
def cancel_book(book_id: str):
    """Stop a queued, running, or waiting job. Marks it failed — no auto-resume."""
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    if state.get("status") in ("done", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"book is already {state.get('status')}",
        )
    _request_cancel(book_id)
    _set_state(
        book_id,
        status="failed",
        error="Generation was cancelled.",
        error_kind="cancelled",
        error_type="PipelineCancelledError",
        cancelled_at=_now_iso(),
        attempts=_MAX_PARK_ATTEMPTS + 1,
        resume_attempts=get_settings().max_resume_attempts + 1,
    )
    log.info("book_cancelled", book_id=book_id, prior_status=state.get("status"))
    return {"book_id": book_id, "status": "failed", "cancelled": True}


@app.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: str):
    """Archive (delete) a job and its artifacts. 404 if it never existed."""
    if _read_state(book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")
    book_dir = _ARTIFACTS / "books" / book_id
    if book_dir.is_dir():
        shutil.rmtree(book_dir, ignore_errors=True)
    log.info("book_deleted", book_id=book_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Audio scripts — SOP narration + ElevenLabs TTS
# --------------------------------------------------------------------------- #

_AS_DIR = _ARTIFACTS / "audio-scripts"


class ElevenLabsVoiceSettings(BaseModel):
    """Optional overrides for ElevenLabs voice_settings (merged over defaults)."""

    stability: float | None = Field(None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(None, ge=0.0, le=1.0)
    style: float | None = Field(None, ge=0.0, le=1.0)
    use_speaker_boost: bool | None = None
    speed: float | None = Field(None, ge=0.7, le=1.2)


class CreateAudioScript(BaseModel):
    title: str
    category: Literal["psychology", "relationship", "philosophy", "general"] = (
        "psychology"
    )
    item_limit: int | None = Field(
        None, gt=0, le=100,
        description="Exact item count when set; omit to use the number from the title.",
    )
    word_count: int | None = Field(
        None, ge=100, le=500,
        description="Target words per item. Default: SOP 265–270.",
    )
    voice_id: str | None = Field(
        None, description="Override ELEVENLABS_VOICE_ID for this job.",
    )
    model_id: str | None = Field(
        None,
        description="Override ElevenLabs model (default: eleven_multilingual_v2).",
    )
    voice_settings: ElevenLabsVoiceSettings | None = Field(
        None,
        description=(
            "Optional voice_settings overrides. Unset fields keep defaults: "
            "stability=0.40, similarity_boost=0.78, style=0.20, "
            "use_speaker_boost=true, speed=0.95."
        ),
    )
    skip_audio: bool = Field(
        False, description="If true, only generate transcript (no ElevenLabs).",
    )


def _as_state_path(script_id: str) -> Path:
    return _AS_DIR / script_id / "status.json"


def _as_request_path(script_id: str) -> Path:
    return _AS_DIR / script_id / "request.json"


def _as_result_path(script_id: str) -> Path:
    return _AS_DIR / script_id / "result.json"


def _as_audio_path(script_id: str) -> Path:
    return _AS_DIR / script_id / "narration.mp3"


def _write_as_state(script_id: str, state: dict[str, Any]) -> None:
    path = _as_state_path(script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def _read_as_state(script_id: str) -> dict[str, Any] | None:
    path = _as_state_path(script_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _set_as_state(script_id: str, **kwargs: Any) -> None:
    with _lock:
        state = _read_as_state(script_id) or {}
        state.update(kwargs)
        _write_as_state(script_id, state)


def _write_as_request(script_id: str, req: CreateAudioScript) -> None:
    path = _as_request_path(script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(req.model_dump_json())


def _write_as_result(script_id: str, result: AudioScriptResult) -> None:
    path = _as_result_path(script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json())


def _read_as_result(script_id: str) -> AudioScriptResult | None:
    path = _as_result_path(script_id)
    if not path.exists():
        return None
    try:
        return AudioScriptResult.model_validate_json(path.read_text())
    except Exception:
        return None


def _make_script_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
    base = slug or "audio-script"
    candidate = base
    if _read_as_state(candidate) is not None:
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def _audio_script_link(script_id: str) -> str:
    base = get_settings().api_public_base_url.rstrip("/")
    return f"{base}/audio-scripts/{script_id}"


def _audio_file_link(script_id: str) -> str:
    return f"{_audio_script_link(script_id)}/audio"


def _list_audio_scripts() -> list[dict[str, Any]]:
    if not _AS_DIR.is_dir():
        return []
    jobs: list[dict[str, Any]] = []
    for path in _AS_DIR.glob("*/status.json"):
        try:
            state = json.loads(path.read_text())
        except Exception:
            continue
        script_id = path.parent.name
        jobs.append({
            "script_id": script_id,
            "title": state.get("title", ""),
            "status": state.get("status", "unknown"),
            "created_at": state.get("created_at", ""),
            "link": _audio_script_link(script_id),
        })
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs


def _audio_script_response(script_id: str) -> dict[str, Any]:
    state = _read_as_state(script_id)
    if state is None:
        raise HTTPException(status_code=404, detail="script not found")
    resp: dict[str, Any] = {
        "script_id": script_id,
        "link": _audio_script_link(script_id),
        "stages": stages_payload(),
        **state,
    }
    if state.get("status") in ("done", "failed"):
        result = _read_as_result(script_id)
        if result is not None:
            data = result.model_dump()
            if result.audio_key or _as_audio_path(script_id).is_file():
                data["audio_link"] = _audio_file_link(script_id)
            resp.update(data)
    return resp


def _run_audio_script(script_id: str, req: CreateAudioScript) -> None:
    item_count = resolve_item_count(req.title, req.item_limit)
    try:
        _set_as_state(
            script_id,
            status="running",
            stage=AudioScriptStage.BRAINSTORMING,
            started_at=_now_iso(),
            item_count=item_count,
            progress={"done": 0, "total": item_count},
        )

        def on_progress(stage: str, done: int, total: int) -> None:
            _set_as_state(script_id, stage=stage, progress={"done": done, "total": total})

        result = generate_audio_script(
            req.title,
            category=req.category,
            item_count=item_count,
            word_count=req.word_count,
            on_progress=on_progress,
        )
        _write_as_result(script_id, result)

        if not req.skip_audio:
            if not get_settings().elevenlabs_api_key.strip():
                raise ElevenLabsError("ELEVENLABS_API_KEY is not set")
            _set_as_state(
                script_id,
                stage=AudioScriptStage.GENERATING_AUDIO,
                progress={"done": 0, "total": item_count},
            )
            audio_bytes = generate_narration_audio(
                result,
                voice_id=req.voice_id,
                model_id=req.model_id,
                voice_settings=(
                    req.voice_settings.model_dump(exclude_none=True)
                    if req.voice_settings
                    else None
                ),
                on_progress=on_progress,
                stage=AudioScriptStage.GENERATING_AUDIO,
            )
            audio_path = _as_audio_path(script_id)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(audio_bytes)
            audio_key = f"audio-scripts/{script_id}/narration.mp3"
            storage.put(audio_key, audio_bytes, content_type="audio/mpeg")
            result.audio_key = audio_key
            result.audio_size_bytes = len(audio_bytes)
            result.audio_link = _audio_file_link(script_id)
            _write_as_result(script_id, result)

        _set_as_state(
            script_id,
            status="done",
            stage=AudioScriptStage.COMPLETE,
            progress={"done": item_count, "total": item_count},
        )
        log.info("audio_script_job_done", script_id=script_id, items=len(result.items),
                 skip_audio=req.skip_audio)
    except Exception as exc:
        log.exception("audio_script_job_failed", script_id=script_id)
        _set_as_state(
            script_id,
            status="failed",
            stage=AudioScriptStage.FAILED,
            error=_friendly_error(exc),
            error_detail=str(exc)[:500],
        )


@app.get("/audio-scripts")
def list_audio_scripts():
    """Recent audio-script jobs, newest first."""
    return {"scripts": _list_audio_scripts()}


@app.post("/audio-scripts", status_code=202)
def create_audio_script(req: CreateAudioScript, background_tasks: BackgroundTasks):
    """Start an audio narration job. Poll GET /audio-scripts/{script_id}."""
    if not req.title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    # Audio jobs need LLM writing only — not book image/layout readiness.
    provider_ok, provider_detail, _unfunded = writing_provider_status()
    if not provider_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "can_generate": False,
                "title": "Can't start audio script right now",
                "message": provider_detail or "Writing service is unavailable.",
                "reason_code": "quota_llm" if "credit" in (provider_detail or "").lower() else "network",
            },
        )
    if not req.skip_audio and not get_settings().elevenlabs_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail={
                "can_generate": False,
                "title": "ElevenLabs not configured",
                "message": "Set ELEVENLABS_API_KEY, or pass skip_audio=true for transcript only.",
                "reason_code": "auth",
            },
        )
    script_id = _make_script_id(req.title.strip())
    _set_as_state(
        script_id,
        status=AudioScriptStage.QUEUED,
        title=req.title.strip(),
        category=req.category,
        item_limit=req.item_limit,
        word_count=req.word_count,
        skip_audio=req.skip_audio,
        created_at=_now_iso(),
        stage=AudioScriptStage.QUEUED,
    )
    _write_as_request(script_id, req)
    background_tasks.add_task(_run_audio_script, script_id, req)
    return {
        "script_id": script_id,
        "status": "queued",
        "link": _audio_script_link(script_id),
    }


@app.get("/audio-scripts/{script_id}")
def get_audio_script(script_id: str):
    """Poll job status. When done, includes transcript, items, and audio_link."""
    return _audio_script_response(script_id)


@app.get("/audio-scripts/{script_id}/audio")
def download_audio_script_audio(script_id: str):
    """Stream the combined narration MP3."""
    state = _read_as_state(script_id)
    if state is None:
        raise HTTPException(status_code=404, detail="script not found")
    path = _as_audio_path(script_id)
    if not path.is_file():
        key = f"audio-scripts/{script_id}/narration.mp3"
        if storage.exists(key):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(storage.get(key))
        else:
            raise HTTPException(status_code=404, detail="audio not ready")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"{script_id}.mp3",
    )


# --------------------------------------------------------------------------- #
# Background pipeline runner
# --------------------------------------------------------------------------- #

def _friendly_error(exc: Exception) -> str:
    """Map an internal exception to a safe, actionable customer-facing message.

    Never leak raw exception text (paths, memory addresses, provider internals,
    or opaque tenacity RetryError reprs) to the UI — that goes to logs only.
    """
    if isinstance(exc, BudgetExceeded):
        return ("This book reached its generation cost limit before finishing. "
                "Try a shorter book or contact support to raise the limit.")
    if isinstance(exc, ElevenLabsError):
        return ("We couldn't generate the narration audio. Check the ElevenLabs "
                "API key and voice settings, then try again.")
    if isinstance(exc, IngestError):
        if getattr(exc, "reason", "") == "youtube_blocked":
            return ("We couldn't read this YouTube video — YouTube is blocking "
                    "access from our server. Please paste the transcript text "
                    "or upload a file instead.")
        return ("We couldn't read your source material. Please check the link "
                "or paste the text directly, then try again.")
    if isinstance(exc, ReadinessError):
        return str(exc)
    if isinstance(exc, PipelineCancelledError):
        return "Generation was cancelled."
    if isinstance(exc, GeometryError):
        return ("We couldn't produce a clean layout for this book. Our team has "
                "been notified and is looking into it.")
    if isinstance(exc, ContentQAError):
        return ("The generated content didn't meet our quality bar, so we "
                "stopped before producing the book. Please try again — our "
                "team has been notified.")
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return "The book service is misconfigured. Our team has been notified."
    if isinstance(exc, anthropic.RateLimitError):
        return "The service is busy right now. Please try again in a few minutes."
    if isinstance(exc, anthropic.BadRequestError):
        if "credit balance" in str(exc).lower():
            return ("Your AI credit balance is low. Please recharge to continue.")
        return "This request couldn't be processed. Please adjust your input and retry."
    if isinstance(exc, anthropic.APIConnectionError | anthropic.InternalServerError):
        return "The service had a temporary problem. Please try again shortly."
    return "Generation failed due to an unexpected error. Please try again."


# --------------------------------------------------------------------------- #
# Failure handling: classify → retry / park & auto-resume / fail
# --------------------------------------------------------------------------- #

def _failure_class(exc: Exception) -> str:
    """Decide how a pipeline failure is handled.

    - "permanent"            → mark failed (won't self-heal on retry): geometry
                               regressions, bad input.
    - "provider_unavailable" → park & auto-resume: credits/auth/outage, or an
                               empty book (which almost always means the model is
                               down). Also connection/rate/5xx that survived the
                               call-level retries.
    - "transient"            → auto-retry a few times, then park: render/storage/
                               ingest hiccups and anything unclassified.
    """
    if isinstance(exc, (GeometryError, ContentQAError, PipelineCancelledError)):
        return "permanent"
    if isinstance(exc, BudgetExceeded):
        # The cap is per-book and persisted across resumes — retrying re-fails
        # instantly. The orchestrator ships partial work where possible; if this
        # propagates, nothing shippable existed. Fail fast with a clear message.
        return "permanent"
    if isinstance(exc, PageLimitError):
        # Deterministic overshoot that survived every trim pass (and the
        # ship-on-overshoot policy is off) — retrying regenerates the same book.
        return "permanent"
    if isinstance(exc, JobDeadlineError):
        # Wall-clock breach: park with checkpoints intact; resume continues
        # where this attempt stopped.
        return "provider_unavailable"
    if isinstance(exc, ReadinessError):
        if exc.reason_code in ("quota_llm", "quota_images", "rate_limit"):
            return "provider_unavailable"
        return "permanent"
    if isinstance(exc, ImageCoverageError):
        return "provider_unavailable"
    from bookforge.clients import images as img_client
    if isinstance(exc, (img_client.ImageQuotaError, img_client.ImageGenerationError)):
        return "provider_unavailable"
    if llm.is_provider_unavailable(exc) or isinstance(exc, EmptyBookError):
        return "provider_unavailable"
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError,
                        anthropic.RateLimitError, anthropic.InternalServerError,
                        anthropic.OverloadedError)):
        return "provider_unavailable"
    if isinstance(exc, IngestError):
        # Ingest failures (bad source, missing captions, blocked IP) are not
        # self-healing — fail fast instead of looping indefinitely.
        return "permanent"
    return "transient"


def _wait_reason(exc: Exception) -> str:
    """Fine-grained reason for a parked job (drives the message + UI styling)."""
    if llm.is_unfunded(exc):
        return "unfunded"
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return "invalid_key"
    if isinstance(exc, JobDeadlineError):
        return "timeout"
    if getattr(exc, "quota_class", "") == "daily":
        return "image_quota_daily"
    return "service"


def _waiting_message(reason: str) -> str:
    if reason == "unfunded":
        return ("Paused: your AI credit balance is low. Please recharge to "
                "continue — your book will finish automatically once credits are "
                "added, no need to resubmit.")
    if reason == "invalid_key":
        return ("Paused: the AI service key is invalid or revoked. Once it's "
                "fixed, your book will finish automatically.")
    if reason == "timeout":
        return ("Your book is taking longer than expected, so we've paused to "
                "keep things healthy. Progress is saved — it will continue "
                "automatically, no action needed.")
    if reason == "image_quota_daily":
        return ("Paused: today's image allowance is used up. Your book will "
                "finish automatically when it resets — no action needed.")
    return ("We're preparing your book — this is taking a little longer than "
            "usual. It will finish automatically, no action needed.")


def _backoff_seconds(attempts: int) -> int:
    """Backoff for the Nth park attempt (1-based); the last value repeats."""
    schedule = [int(x) for x in
                get_settings().provider_retry_backoff_s.split(",") if x.strip()]
    if not schedule:
        schedule = [1800]
    return schedule[min(max(attempts, 1), len(schedule)) - 1]


def _operator_alert(book_id: str, cls: str, exc: Exception) -> None:
    """Loud log + optional webhook so an operator notices credits/render issues."""
    detail = f"{type(exc).__name__}: {str(exc)[:300]}"
    log.error("operator_alert", book_id=book_id, failure_class=cls, detail=detail)
    url = get_settings().operator_alert_webhook
    if not url:
        return
    try:
        import urllib.request
        payload = json.dumps({"text": f"[BookForge] {cls} on {book_id}: {detail}"})
        req = urllib.request.Request(
            url, data=payload.encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc2:  # never let alerting break the pipeline
        log.warning("operator_alert_webhook_failed", err=str(exc2)[:200])


_MAX_PARK_ATTEMPTS = 6  # after this many park cycles, fail the job permanently


def _park(book_id: str, exc: Exception, cls: str) -> None:
    """Keep the job alive as 'waiting'; the resume loop will re-run it later.
    The wait_reason distinguishes the unfunded/out-of-credits case so the UI can
    show a clear 'unfunded' notice instead of a generic 'preparing' message.
    After _MAX_PARK_ATTEMPTS park cycles the job is failed to prevent infinite
    loops when the provider issue doesn't self-heal.

    Quota-aware: an exception carrying `wait_until` (e.g. a DAILY image quota
    that resets at midnight Pacific) is parked until that horizon instead of the
    generic backoff, and daily-quota parks don't consume the attempt cap — the
    old fixed 60..1800s schedule could never outlive a 24h quota, so jobs burned
    all their attempts on guaranteed-futile resumes."""
    from bookforge.api.error_kinds import classify as _classify_error

    state = _read_state(book_id) or {}
    now = datetime.now(timezone.utc)
    wait_until = getattr(exc, "wait_until", None)
    quota_parks = int(state.get("quota_parks", 0))
    daily_quota = getattr(exc, "quota_class", "") == "daily"
    if daily_quota:
        quota_parks += 1
        # Safety valve: if the quota keeps reporting "daily" past its own park
        # budget (misclassified / never actually resets), fall back to normal
        # attempt accounting so the job can't park forever.
        if quota_parks > get_settings().image_quota_max_parks + 1:
            daily_quota = False

    attempts = int(state.get("attempts", 0)) + (0 if daily_quota else 1)

    if attempts > _MAX_PARK_ATTEMPTS:
        classified = _classify_error(exc)
        log.warning("job_park_cap_exceeded", book_id=book_id, attempts=attempts,
                    failure_class=cls, error_kind=classified.kind)
        _fail(book_id, exc, "permanent")
        return

    if daily_quota and wait_until is not None:
        next_dt = wait_until
    else:
        next_dt = now + timedelta(seconds=_backoff_seconds(attempts))
        # A short provider-suggested wait (per-minute limits) may exceed the
        # early backoff steps — honor whichever is later.
        if wait_until is not None and wait_until > next_dt:
            next_dt = wait_until
    delay = max(0, int((next_dt - now).total_seconds()))
    reason = _wait_reason(exc)
    classified = _classify_error(exc)
    _set_state(book_id, status="waiting", attempts=attempts,
               quota_parks=quota_parks,
               wait_reason=reason, unfunded=(reason == "unfunded"),
               next_retry_at=next_dt.isoformat(), error=_waiting_message(reason),
               error_kind=classified.kind,
               error_type=type(exc).__name__, error_detail=str(exc)[:2000])
    _operator_alert(book_id, reason, exc)
    log.warning("job_parked", book_id=book_id, attempts=attempts,
                quota_parks=quota_parks, retry_in_s=delay, failure_class=cls,
                wait_reason=reason, error_kind=classified.kind)


def _fail(book_id: str, exc: Exception, cls: str) -> None:
    from bookforge.api.error_kinds import classify as _classify_error

    classified = _classify_error(exc)
    # Use the classifier's friendly message for quota/auth kinds; fall back to
    # the legacy _friendly_error for domain-specific messages (geometry, etc.).
    if classified.kind in ("quota_llm", "quota_images", "rate_limit", "auth"):
        friendly = classified.message
    else:
        friendly = _friendly_error(exc)
    error_kind = classified.kind
    if isinstance(exc, ReadinessError) and exc.reason_code:
        error_kind = exc.reason_code
    _set_state(book_id, status="failed", error=friendly,
               error_kind=error_kind,
               error_type=type(exc).__name__, error_detail=str(exc)[:2000])
    if cls == "permanent":
        _operator_alert(book_id, cls, exc)


# Error/wait metadata left over from parked or retried attempts; cleared the
# moment a job reaches done so a finished book never shows stale failure state.
_DONE_CLEARS: dict[str, Any] = {
    "error": None, "error_type": None, "error_detail": None, "error_kind": None,
    "wait_reason": None, "unfunded": None, "next_retry_at": None,
}


def _mark_done(book_id: str, artifact) -> None:
    warnings = list(getattr(artifact, "warnings", []) or [])
    degraded = bool(getattr(artifact, "degraded", False))
    _set_state(book_id, status="done", artifact=artifact.model_dump(),
               done_at=_now_iso(), warnings=warnings or None,
               degraded=degraded or None, **_DONE_CLEARS)
    if warnings:
        # The book SHIPPED (never-fail policy) but with degradations — make
        # sure an operator sees what quality was traded to get it out.
        _operator_alert(book_id, "degraded_ship",
                        RuntimeError("; ".join(warnings[:3])))


def _defer_resume(book_id: str) -> None:
    """No slot free for a resumed job: stay 'waiting' and try again next tick.
    Deliberately does NOT touch `attempts` — waiting for capacity is not a
    failure."""
    next_at = (datetime.now(timezone.utc)
               + timedelta(seconds=get_settings().resume_poll_interval_s)).isoformat()
    _set_state(book_id, status="waiting", next_retry_at=next_at)


def _run_pipeline(book_id: str, req: CreateBook, *, resumed: bool = False) -> None:
    if resumed:
        # Resumed jobs must not block a thread on the semaphore (that's how
        # parked jobs starved fresh submissions): take a reserved resume slot
        # AND a pipeline slot non-blockingly, or go back to waiting.
        if not _resume_slots.acquire(blocking=False):
            _defer_resume(book_id)
            return
        try:
            if not _pipeline_sem.acquire(blocking=False):
                _defer_resume(book_id)
                return
            try:
                _execute_pipeline(book_id, req)
            finally:
                _pipeline_sem.release()
        finally:
            _resume_slots.release()
        return

    # Fresh submission: block here (still "queued") until a slot is free.
    with _pipeline_sem:
        _execute_pipeline(book_id, req)


def _execute_pipeline(book_id: str, req: CreateBook) -> None:
    def on_stage(stage: str) -> None:
        if _is_cancelled(book_id):
            raise PipelineCancelledError(f"cancelled at stage {stage}")
        from bookforge.cost import budget as _budget
        _set_state(book_id, stage=stage, stage_started_at=_now_iso(),
                   heartbeat_at=_now_iso(), cost_usd=round(_budget.spent, 2))

    if _is_cancelled(book_id):
        _fail(book_id, PipelineCancelledError("cancelled before start"), "permanent")
        return
    _set_state(book_id, status="running", started_at=_now_iso(),
               heartbeat_at=_now_iso())
    with _active_jobs_lock:
        _active_jobs[book_id] = current_thread()
    try:
        from bookforge.pipeline.orchestrator import run_book

        inline_retries = get_settings().max_job_retries
        attempt = 0
        while True:
            if _is_cancelled(book_id):
                _fail(book_id, PipelineCancelledError("cancelled"), "permanent")
                return
            # Fresh cooperative deadline per attempt; checked (with the cancel
            # flag + heartbeat) at every LLM/image call boundary via runctx.
            run = runctx.JobRun(
                book_id,
                deadline_ts=time.monotonic() + get_settings().job_timeout_minutes * 60,
                cancel_cb=lambda: _is_cancelled(book_id),
                heartbeat_cb=lambda: _set_state(book_id, heartbeat_at=_now_iso()),
            )
            try:
                with runctx.bind(run):
                    artifact = run_book(
                        book_id, req.topic,
                        text=req.text, youtube_url=req.youtube_url,
                        target_pages=req.target_pages,
                        chapter_limit=req.chapter_limit,
                        section_limit=req.section_limit,
                        book_kind=req.book_kind,
                        description=req.description,
                        toc=req.toc,
                        profile_overrides=req.profile_overrides,
                        on_stage=on_stage,
                    )
                if _is_cancelled(book_id):
                    _fail(book_id, PipelineCancelledError("cancelled"), "permanent")
                    return
                _mark_done(book_id, artifact)
                return
            except Exception as exc:
                cls = _failure_class(exc)
                log.exception("pipeline_failed", book_id=book_id, failure_class=cls,
                              attempt=attempt, error_type=type(exc).__name__)
                if cls == "transient" and attempt < inline_retries:
                    attempt += 1
                    continue  # quick in-slot retry before giving up
                if cls == "permanent":
                    _fail(book_id, exc, cls)
                else:  # provider_unavailable, or transient that exhausted retries
                    _park(book_id, exc, cls)
                return
    finally:
        with _active_jobs_lock:
            _active_jobs.pop(book_id, None)


# --------------------------------------------------------------------------- #
# Background auto-resume loop — re-runs parked ("waiting") jobs on schedule so a
# provider outage (credits/auth) self-heals the moment it's fixed.
# --------------------------------------------------------------------------- #

def _resume_due(book_id: str) -> bool:
    state = _read_state(book_id)
    if not state or state.get("status") != "waiting":
        return False
    if _is_cancelled(book_id):
        return False
    nxt = _parse_ts(state.get("next_retry_at"))
    return nxt is None or nxt <= datetime.now(timezone.utc)


def _resume_one(book_id: str) -> None:
    req = _read_request(book_id)
    if req is None:
        _set_state(book_id, status="failed",
                   error="Generation failed due to an unexpected error. Please try again.",
                   error_detail="cannot resume: request payload missing")
        return
    # Push next_retry_at forward so the next tick doesn't double-claim while
    # this attempt is acquiring slots. Status stays "waiting" until a slot is
    # actually held — a merely-waiting job must not count as active capacity.
    _defer_resume(book_id)
    _run_pipeline(book_id, req, resumed=True)


def _watchdog_tick() -> None:
    """Re-park 'running' jobs whose worker thread is gone (stale heartbeat and
    no live thread) — e.g. after a partial recovery from an executor death.
    Never touches a job whose thread is alive: the cooperative deadline
    (JOB_TIMEOUT_MINUTES) handles live-but-slow, and killing threads is unsafe."""
    stale_after = timedelta(minutes=get_settings().heartbeat_stale_minutes)
    now = datetime.now(timezone.utc)
    books_dir = _ARTIFACTS / "books"
    if not books_dir.is_dir():
        return
    for path in books_dir.glob("*/status.json"):
        book_id = path.parent.name
        state = _read_state(book_id)
        if not state or state.get("status") != "running":
            continue
        beat = _parse_ts(state.get("heartbeat_at")) or _parse_ts(state.get("started_at"))
        if beat is None or now - beat < stale_after:
            continue
        with _active_jobs_lock:
            thread = _active_jobs.get(book_id)
        if thread is not None and thread.is_alive():
            if not state.get("watchdog_alerted"):
                _set_state(book_id, watchdog_alerted=True)
                _operator_alert(book_id, "stale_heartbeat", RuntimeError(
                    f"running with no heartbeat since {state.get('heartbeat_at')}"))
            continue
        log.warning("watchdog_reparked", book_id=book_id,
                    last_heartbeat=state.get("heartbeat_at"))
        _set_state(book_id, status="waiting", wait_reason="watchdog",
                   next_retry_at=_now_iso(),
                   error=_waiting_message("timeout"))


async def _resume_loop() -> None:
    interval = max(5, get_settings().resume_poll_interval_s)
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(interval)
            _watchdog_tick()
            books_dir = _ARTIFACTS / "books"
            if not books_dir.is_dir():
                continue
            for path in books_dir.glob("*/status.json"):
                book_id = path.parent.name
                if _resume_due(book_id):
                    log.info("job_resuming", book_id=book_id)
                    # Run off the event loop so a slow pipeline doesn't block polling.
                    loop.run_in_executor(None, _resume_one, book_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # a bad tick must never kill the loop
            log.exception("resume_loop_tick_failed")
