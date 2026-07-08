"""FastAPI — job submission, status polling, and PDF delivery.

GET  /books                    → list recent jobs (newest first)
POST /books                    → start a pipeline job, returns book_id (topic slug)
GET  /books/{book_id}          → poll status: queued | running | done | failed
GET  /books/{book_id}/download → stream the finished PDF
POST /books/upload             → multipart file upload (alternative to JSON text)
GET  /health                   → liveness probe
GET  /readiness                → pre-queue generation readiness
POST /readiness                → readiness with request sanity checks

Job state is persisted to {ARTIFACTS_ROOT}/books/{book_id}/status.json so it
survives API restarts and container rebuilds.
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
from threading import BoundedSemaphore, Lock
from typing import Any, Literal

import anthropic
import structlog
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from bookforge.clients import llm, storage
from bookforge.config import get_settings
from bookforge.errors import (
    ContentQAError, EmptyBookError, GeometryError, IngestError, ReadinessError,
)
from bookforge.pipeline.readiness import (
    ReadinessRequest,
    ReadinessResult,
    check_readiness,
    writing_provider_status,
)

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
    path.write_text(json.dumps(state))


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
    book_kind: Literal["practical_guide", "narrative"] = "practical_guide"
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
    book_kind: Literal["practical_guide", "narrative"] = Form("practical_guide"),
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
    try:
        data = storage.get(f"books/{book_id}/interior.pdf")
    except Exception:
        raise HTTPException(status_code=404, detail="PDF not found in storage")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{book_id}.pdf"'},
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
        try:
            _set_state(book_id, status="running", stage="layout",
                       stage_started_at=_now_iso())
            artifact = assemble.rerender(book_id)
            _set_state(book_id, status="done", artifact=artifact.model_dump(),
                       error=None, error_type=None, error_detail=None)
            log.info("rerender_done", book_id=book_id, pages=artifact.page_count)
        except Exception as exc:
            log.exception("rerender_failed", book_id=book_id)
            _fail(book_id, exc, _failure_class(exc))

    background_tasks.add_task(_do_rerender)
    return {"book_id": book_id, "status": "running", "stage": "layout"}


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
            try:
                _set_state(book_id, status="running", stage="layout",
                           stage_started_at=_now_iso())
                artifact = assemble.rerender(book_id)
                _set_state(book_id, status="done", artifact=artifact.model_dump(),
                           error=None, error_type=None, error_detail=None)
                log.info("regenerate_rerendered", book_id=book_id,
                         pages=artifact.page_count)
            except Exception as exc:
                log.exception("regenerate_rerender_failed", book_id=book_id)
                _fail(book_id, exc, _failure_class(exc))

        background_tasks.add_task(_do_rerender)
        return {"book_id": book_id, "status": "running", "stage": "layout",
                "mode": "rerender"}

    req = _read_request(book_id)
    if req is None:
        raise HTTPException(
            status_code=409,
            detail="no render manifest or original request stored for this "
                   "book — it cannot be regenerated; create a new book instead",
        )
    _set_state(book_id, status="queued", stage=None, error=None,
               error_type=None, error_detail=None, attempts=0,
               resume_attempts=0)
    background_tasks.add_task(_run_pipeline, book_id, req)
    return {"book_id": book_id, "status": "queued", "mode": "full_rerun"}


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
# Background pipeline runner
# --------------------------------------------------------------------------- #

def _friendly_error(exc: Exception) -> str:
    """Map an internal exception to a safe, actionable customer-facing message.

    Never leak raw exception text (paths, memory addresses, provider internals,
    or opaque tenacity RetryError reprs) to the UI — that goes to logs only.
    """
    from bookforge.cost import BudgetExceeded

    if isinstance(exc, BudgetExceeded):
        return ("This book reached its generation cost limit before finishing. "
                "Try a shorter book or contact support to raise the limit.")
    if isinstance(exc, ReadinessError):
        return str(exc)
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
    if isinstance(exc, (GeometryError, ContentQAError, ReadinessError)):
        return "permanent"
    if llm.is_provider_unavailable(exc) or isinstance(exc, EmptyBookError):
        return "provider_unavailable"
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError,
                        anthropic.RateLimitError, anthropic.InternalServerError)):
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
    return "service"


def _waiting_message(reason: str) -> str:
    if reason == "unfunded":
        return ("Paused: your AI credit balance is low. Please recharge to "
                "continue — your book will finish automatically once credits are "
                "added, no need to resubmit.")
    if reason == "invalid_key":
        return ("Paused: the AI service key is invalid or revoked. Once it's "
                "fixed, your book will finish automatically.")
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
    loops when the provider issue doesn't self-heal."""
    from bookforge.api.error_kinds import classify as _classify_error

    state = _read_state(book_id) or {}
    attempts = int(state.get("attempts", 0)) + 1

    if attempts > _MAX_PARK_ATTEMPTS:
        classified = _classify_error(exc)
        log.warning("job_park_cap_exceeded", book_id=book_id, attempts=attempts,
                    failure_class=cls, error_kind=classified.kind)
        _fail(book_id, exc, "permanent")
        return

    delay = _backoff_seconds(attempts)
    next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    reason = _wait_reason(exc)
    classified = _classify_error(exc)
    _set_state(book_id, status="waiting", attempts=attempts,
               wait_reason=reason, unfunded=(reason == "unfunded"),
               next_retry_at=next_at, error=_waiting_message(reason),
               error_kind=classified.kind,
               error_type=type(exc).__name__, error_detail=str(exc)[:2000])
    _operator_alert(book_id, reason, exc)
    log.warning("job_parked", book_id=book_id, attempts=attempts,
                retry_in_s=delay, failure_class=cls, wait_reason=reason,
                error_kind=classified.kind)


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


def _run_pipeline(book_id: str, req: CreateBook) -> None:
    def on_stage(stage: str) -> None:
        _set_state(book_id, stage=stage, stage_started_at=_now_iso())

    # Block here (still "queued") until a pipeline slot is free.
    with _pipeline_sem:
        _set_state(book_id, status="running", started_at=_now_iso())
        from bookforge.pipeline.orchestrator import run_book

        inline_retries = get_settings().max_job_retries
        attempt = 0
        while True:
            try:
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
                _set_state(book_id, status="done", artifact=artifact.model_dump(),
                           error=None, error_type=None, error_detail=None)
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


# --------------------------------------------------------------------------- #
# Background auto-resume loop — re-runs parked ("waiting") jobs on schedule so a
# provider outage (credits/auth) self-heals the moment it's fixed.
# --------------------------------------------------------------------------- #

def _resume_due(book_id: str) -> bool:
    state = _read_state(book_id)
    if not state or state.get("status") != "waiting":
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
    _set_state(book_id, status="queued")  # claim it so the next tick skips it
    _run_pipeline(book_id, req)


async def _resume_loop() -> None:
    interval = max(5, get_settings().resume_poll_interval_s)
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(interval)
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
