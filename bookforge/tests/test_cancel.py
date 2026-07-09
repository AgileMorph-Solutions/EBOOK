"""Cancel endpoint and pipeline cancellation."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("BOOKFORGE_LOCAL_STORE", "/tmp/bookforge-test-artifacts")

from bookforge.api.main import app, _cancel_path, _read_state  # noqa: E402

client = TestClient(app)


@pytest.fixture
def book_id(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKFORGE_LOCAL_STORE", str(tmp_path))
    from bookforge.api import main as main_mod

    main_mod._ARTIFACTS = tmp_path
    bid = "test-cancel-abc123"
    book_dir = tmp_path / "books" / bid
    book_dir.mkdir(parents=True)
    (book_dir / "status.json").write_text(json.dumps({
        "status": "running",
        "topic": "Test",
        "stage": "content",
    }))
    (book_dir / "request.json").write_text(json.dumps({
        "topic": "Test",
        "text": "x" * 500,
        "target_pages": 5,
        "book_kind": "practical_guide",
    }))
    return bid


def test_cancel_running_book(book_id):
    res = client.post(f"/books/{book_id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "failed"
    state = _read_state(book_id)
    assert state["status"] == "failed"
    assert state["error_kind"] == "cancelled"
    assert _cancel_path(book_id).exists()


def test_cancel_already_failed(book_id):
    client.post(f"/books/{book_id}/cancel")
    res = client.post(f"/books/{book_id}/cancel")
    assert res.status_code == 409
