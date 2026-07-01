"""API upload endpoint tests — no pipeline integration."""
from __future__ import annotations

import os
from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("BOOKFORGE_LOCAL_STORE", "/tmp/bookforge-test-artifacts")

from bookforge.api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_pipeline():
    with patch("bookforge.api.main._run_pipeline"):
        yield


def test_upload_valid_file():
    data = {
        "topic": "Test Upload Book",
        "target_pages": "50",
        "book_kind": "practical_guide",
    }
    files = {"file": ("source.txt", BytesIO(b"Hello world source text."), "text/plain")}
    res = client.post("/books/upload", data=data, files=files)
    assert res.status_code == 202
    body = res.json()
    assert "book_id" in body
    assert body["status"] == "queued"


def test_upload_empty_file_422():
    data = {"topic": "Empty File Test"}
    files = {"file": ("empty.txt", BytesIO(b""), "text/plain")}
    res = client.post("/books/upload", data=data, files=files)
    assert res.status_code == 422


def test_upload_missing_topic_422():
    files = {"file": ("source.txt", BytesIO(b"content"), "text/plain")}
    res = client.post("/books/upload", data={}, files=files)
    assert res.status_code == 422


def test_upload_oversize_413(monkeypatch):
    monkeypatch.setenv("MAX_SOURCE_SIZE_MB", "0")
    from bookforge.config import get_settings

    get_settings.cache_clear()

    data = {"topic": "Big File"}
    files = {"file": ("big.txt", BytesIO(b"x" * 1024), "text/plain")}
    res = client.post("/books/upload", data=data, files=files)
    assert res.status_code == 413

    get_settings.cache_clear()
