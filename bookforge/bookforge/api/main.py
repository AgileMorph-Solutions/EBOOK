"""FastAPI — job submission + status for the internal tool / Next.js UI."""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from bookforge.clients import storage

app = FastAPI(title="BookForge API", version="0.1.0")


class CreateBook(BaseModel):
    topic: str
    text: str | None = None
    youtube_url: str | None = None
    target_pages: int = 100
    chapter_limit: int | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/books")
def create_book(req: CreateBook):
    from bookforge.workflow.tasks import run_book

    book_id = uuid.uuid4().hex[:12]
    task = run_book.delay(
        book_id, req.topic, text=req.text, youtube_url=req.youtube_url,
        target_pages=req.target_pages, chapter_limit=req.chapter_limit,
    )
    return {"book_id": book_id, "task_id": task.id, "status": "queued"}


@app.get("/tasks/{task_id}")
def task_status(task_id: str):
    from bookforge.workflow.celery_app import app as celery_app

    res = celery_app.AsyncResult(task_id)
    return {"task_id": task_id, "state": res.state,
            "result": res.result if res.ready() else None}


@app.get("/books/{book_id}/interior.pdf")
def download(book_id: str):
    from fastapi.responses import Response

    try:
        data = storage.get(f"books/{book_id}/interior.pdf")
    except Exception:
        raise HTTPException(404, "not found")
    return Response(content=data, media_type="application/pdf")
