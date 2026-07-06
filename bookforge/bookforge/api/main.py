"""FastAPI — job submission, status polling, and PDF delivery.

GET  /books                    → list recent jobs (newest first)
POST /books                    → start a pipeline job, returns book_id (topic slug)
GET  /books/{book_id}          → poll status: queued | running | done | failed
GET  /books/{book_id}/download → stream the finished PDF
POST /books/upload             → multipart file upload (alternative to JSON text)
GET  /health                   → liveness probe

Job state is persisted to {ARTIFACTS_ROOT}/books/{book_id}/status.json so it
survives API restarts and container rebuilds.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Literal

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.orm import Session

from bookforge.api.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from bookforge.api.error_kinds import classify as classify_pipeline_error
from bookforge.clients import storage
from bookforge.config import get_settings
from bookforge.db import get_db
from bookforge.models import BookRecord, User

log = structlog.get_logger(__name__)

API_VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    """Fail fast on missing keys and recover jobs orphaned by a prior crash."""
    s = get_settings()
    missing = [
        name for name, val in (
            ("ANTHROPIC_API_KEY", s.anthropic_api_key),
            ("GEMINI_API_KEY", s.gemini_api_key),
        ) if not val
    ]
    if missing:
        log.error("missing_required_keys", missing=missing)

    recovered = 0
    books_dir = _ARTIFACTS / "books"
    if books_dir.is_dir():
        for path in books_dir.glob("*/status.json"):
            state = _read_state(path.parent.name)
            if state and state.get("status") in ("queued", "running"):
                _set_state(path.parent.name, status="failed",
                           error="interrupted by server restart")
                recovered += 1
    log.info("startup", version=API_VERSION, artifacts=str(_ARTIFACTS),
             recovered_orphans=recovered, max_concurrent=s.max_concurrent_books)
    yield


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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_book_id(topic: str) -> str:
    """Slug from topic + 6-char random suffix, e.g. 'forgotten-british-meals-a3f2c1'."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def _list_jobs(allowed_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Scan artifact dirs for status.json files, newest first.

    If ``allowed_ids`` is provided, restrict the listing to those book_ids.
    """
    books_dir = _ARTIFACTS / "books"
    if not books_dir.is_dir():
        return []
    jobs: list[dict[str, Any]] = []
    for path in books_dir.glob("*/status.json"):
        book_id = path.parent.name
        if allowed_ids is not None and book_id not in allowed_ids:
            continue
        try:
            state = json.loads(path.read_text())
        except Exception:
            continue
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


def _assert_can_access(db: Session, user: User, book_id: str) -> BookRecord | None:
    """Return the BookRecord if the user may access it, else raise.

    A missing record is treated as legacy — allowed only for admins so we don't
    accidentally leak books that pre-date auth.
    """
    record = db.get(BookRecord, book_id)
    if record is None:
        if not user.is_admin:
            raise HTTPException(status_code=404, detail="book not found")
        return None
    if record.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    return record


# --------------------------------------------------------------------------- #
# Request schema
# --------------------------------------------------------------------------- #

class CreateBook(BaseModel):
    topic: str
    text: str | None = None
    youtube_url: str | None = None
    target_pages: int = 100
    chapter_limit: int | None = None
    section_limit: int | None = None
    book_kind: Literal["practical_guide", "narrative"] = "practical_guide"

    @model_validator(mode="after")
    def require_source(self) -> "CreateBook":
        if not self.text and not self.youtube_url:
            raise ValueError("Provide either 'text' or 'youtube_url'")
        return self


def _normalize_email(value: str) -> str:
    v = (value or "").strip().lower()
    # Lightweight check — full RFC validation would reject dev-only TLDs like `.local`.
    if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
        raise ValueError("must be a valid email address")
    return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _normalize_email(v)


class RegisterRequest(BaseModel):
    email: str
    password: str
    is_admin: bool = False

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("password")
    @classmethod
    def _v_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("must be at least 8 characters")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(id=str(user.id), email=user.email, is_admin=user.is_admin)


def _enqueue_book(
    req: CreateBook,
    background_tasks: BackgroundTasks,
    user: User,
    db: Session,
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
        "owner_id": str(user.id),
    }
    if source_filename:
        state["source_filename"] = source_filename
    _set_state(book_id, **state)

    # Persist ownership (skipped for the anonymous admin used when AUTH_ENABLED=false).
    if get_settings().auth_enabled:
        db.add(BookRecord(book_id=book_id, user_id=user.id, topic=req.topic))
        db.commit()

    background_tasks.add_task(_run_pipeline, book_id, req)
    return {"book_id": book_id, "status": "queued"}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    s = get_settings()
    return {
        "ok": bool(s.anthropic_api_key and s.gemini_api_key),
        "version": API_VERSION,
        "storage": "s3" if os.environ.get("BOOKFORGE_USE_S3") == "1" else "local",
        "render": "render_service" if os.environ.get("RENDER_URL") else "playwright",
        "anthropic_key": bool(s.anthropic_api_key),
        "gemini_key": bool(s.gemini_api_key),
        "max_concurrent_books": s.max_concurrent_books,
    }


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).one_or_none()
    if user is None or not user.is_active or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return TokenResponse(access_token=create_access_token(user))


@app.post("/auth/register", response_model=UserOut, status_code=201)
def register(
    req: RegisterRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin-only: create a new user."""
    if db.query(User).filter(User.email == req.email).one_or_none():
        raise HTTPException(status_code=409, detail="email already registered")
    user = User(email=req.email, hashed_password=hash_password(req.password), is_admin=req.is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.from_user(user)


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.from_user(user)


@app.get("/books")
def list_books(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Recent book jobs, newest first (filtered to the current user unless admin)."""
    if user.is_admin:
        return {"books": _list_jobs()}
    if not get_settings().auth_enabled:
        return {"books": _list_jobs()}
    owned = {r.book_id for r in db.query(BookRecord.book_id).filter(BookRecord.user_id == user.id)}
    return {"books": _list_jobs(allowed_ids=owned)}


@app.post("/books", status_code=202)
def create_book(
    req: CreateBook,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a book job. Returns immediately with a readable book_id."""
    return _enqueue_book(req, background_tasks, user, db)


@app.post("/books/upload", status_code=202)
async def create_book_upload(
    background_tasks: BackgroundTasks,
    topic: str = Form(...),
    file: UploadFile = File(...),
    target_pages: int = Form(100),
    chapter_limit: int | None = Form(None),
    section_limit: int | None = Form(None),
    book_kind: Literal["practical_guide", "narrative"] = Form("practical_guide"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
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

    req = CreateBook(
        topic=topic.strip(),
        text=text,
        target_pages=target_pages,
        chapter_limit=chapter_limit,
        section_limit=section_limit,
        book_kind=book_kind,
    )
    return _enqueue_book(
        req,
        background_tasks,
        user,
        db,
        source_filename=file.filename or "upload.txt",
    )


def _reclassify_legacy_error(state: dict[str, Any]) -> dict[str, Any]:
    """Older failed jobs stored only the raw `error` string. Re-classify on read
    so the UI shows the same friendly message without needing to re-run them."""
    if state.get("status") != "failed" or state.get("error_kind"):
        return state
    raw = state.get("error") or ""
    if not raw:
        return state
    classified = classify_pipeline_error(Exception(raw))
    return {**state, "error": classified.message, "error_kind": classified.kind,
            "error_detail": classified.detail or raw}


@app.get("/books/{book_id}")
def get_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current job status and, when done, artifact metadata."""
    _assert_can_access(db, user, book_id)
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    return {"book_id": book_id, **_reclassify_legacy_error(state)}


@app.get("/books/{book_id}/download")
def download(
    book_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download the finished PDF. Returns 409 if the job isn't done yet."""
    _assert_can_access(db, user, book_id)
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


@app.delete("/books/{book_id}", status_code=204)
def delete_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Archive (delete) a job and its artifacts. 404 if it never existed."""
    record = _assert_can_access(db, user, book_id)
    if _read_state(book_id) is None and record is None:
        raise HTTPException(status_code=404, detail="book not found")
    book_dir = _ARTIFACTS / "books" / book_id
    if book_dir.is_dir():
        shutil.rmtree(book_dir, ignore_errors=True)
    if record is not None:
        db.delete(record)
        db.commit()
    log.info("book_deleted", book_id=book_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Background pipeline runner
# --------------------------------------------------------------------------- #

def _run_pipeline(book_id: str, req: CreateBook) -> None:
    def on_stage(stage: str) -> None:
        _set_state(book_id, stage=stage)

    # Block here (still "queued") until a pipeline slot is free.
    with _pipeline_sem:
        _set_state(book_id, status="running")
        try:
            from bookforge.pipeline.orchestrator import run_book

            artifact = run_book(
                book_id,
                req.topic,
                text=req.text,
                youtube_url=req.youtube_url,
                target_pages=req.target_pages,
                chapter_limit=req.chapter_limit,
                section_limit=req.section_limit,
                book_kind=req.book_kind,
                on_stage=on_stage,
            )
            _set_state(book_id, status="done", artifact=artifact.model_dump())
        except Exception as exc:
            log.exception("pipeline_failed", book_id=book_id)
            classified = classify_pipeline_error(exc)
            _set_state(
                book_id,
                status="failed",
                error=classified.message,
                error_kind=classified.kind,
                error_detail=classified.detail,
            )
