"""FastAPI — job submission, status polling, and PDF delivery.

GET  /books                    → list recent jobs (newest first)
POST /books                    → start a pipeline job, returns book_id (topic slug)
GET  /books/{book_id}          → poll status: queued | running | done | failed
GET  /books/{book_id}/download → stream the finished PDF
GET  /health                   → liveness probe

Job state is persisted to {ARTIFACTS_ROOT}/books/{book_id}/status.json so it
survives API restarts and container rebuilds.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, model_validator

from bookforge.clients import storage

app = FastAPI(title="BookForge API", version="0.2.0")

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
        })
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs


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


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/books")
def list_books():
    """Recent book jobs, newest first."""
    return {"books": _list_jobs()}


@app.post("/books", status_code=202)
def create_book(req: CreateBook, background_tasks: BackgroundTasks):
    """Submit a book job. Returns immediately with a readable book_id.

    Poll GET /books/{book_id} for progress, then GET /books/{book_id}/download.
    """
    book_id = _make_book_id(req.topic)
    _set_state(
        book_id,
        status="queued",
        topic=req.topic,
        created_at=datetime.now(timezone.utc).isoformat(),
        target_pages=req.target_pages,
        chapter_limit=req.chapter_limit,
        section_limit=req.section_limit,
        book_kind=req.book_kind,
        source_type="youtube" if req.youtube_url else "text",
    )
    background_tasks.add_task(_run_pipeline, book_id, req)
    return {"book_id": book_id, "status": "queued"}


@app.get("/books/{book_id}")
def get_book(book_id: str):
    """Current job status and, when done, artifact metadata."""
    state = _read_state(book_id)
    if state is None:
        raise HTTPException(status_code=404, detail="book not found")
    return {"book_id": book_id, **state}


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


# --------------------------------------------------------------------------- #
# Background pipeline runner
# --------------------------------------------------------------------------- #

def _run_pipeline(book_id: str, req: CreateBook) -> None:
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
        )
        _set_state(book_id, status="done", artifact=artifact.model_dump())
    except Exception as exc:
        _set_state(book_id, status="failed", error=str(exc))
