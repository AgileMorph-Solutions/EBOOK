"""Voice model API tests — Postgres/SQLite via dependency override."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("BOOKFORGE_LOCAL_STORE", "/tmp/bookforge-test-artifacts")

from bookforge.api.main import app  # noqa: E402
from bookforge.db import Base, get_db  # noqa: E402
from bookforge.models import VoiceModel  # noqa: E402

_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.create_all(_engine)
    session = _TestSession()
    try:
        def override_get_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(_engine)


@pytest.fixture
def client():
    return TestClient(app)


def test_list_voice_models_empty(client):
    res = client.get("/voice-models")
    assert res.status_code == 200
    assert res.json()["voice_models"] == []


def test_create_and_list_voice_model(client):
    create = client.post(
        "/voice-models",
        json={
            "name": "Test Narrator",
            "voice_id": "abc123voice",
            "model_id": "eleven_multilingual_v2",
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert body["name"] == "Test Narrator"
    assert body["voice_id"] == "abc123voice"

    listed = client.get("/voice-models")
    assert listed.status_code == 200
    models = listed.json()["voice_models"]
    assert len(models) == 1
    assert models[0]["name"] == "Test Narrator"


def test_create_default_clears_other_defaults(client):
    first = client.post(
        "/voice-models",
        json={"name": "First", "voice_id": "v1", "is_default": True},
    ).json()
    second = client.post(
        "/voice-models",
        json={"name": "Second", "voice_id": "v2", "is_default": True},
    ).json()
    assert second["is_default"] is True

    models = client.get("/voice-models").json()["voice_models"]
    by_id = {m["id"]: m for m in models}
    assert by_id[first["id"]]["is_default"] is False
    assert by_id[second["id"]]["is_default"] is True


def test_delete_voice_model(client):
    created = client.post(
        "/voice-models",
        json={"name": "Delete Me", "voice_id": "del-voice"},
    ).json()
    res = client.delete(f"/voice-models/{created['id']}")
    assert res.status_code == 204
    assert client.get("/voice-models").json()["voice_models"] == []


def test_delete_voice_model_not_found(client):
    res = client.delete(f"/voice-models/{uuid.uuid4()}")
    assert res.status_code == 404


def test_create_rejects_blank_name(client):
    res = client.post(
        "/voice-models",
        json={"name": "   ", "voice_id": "x"},
    )
    assert res.status_code == 422
