"""Image quota errors must abort the batch, not silently skip slots."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bookforge.clients import images as img_client
from bookforge.pipeline.images import _generate_best
from bookforge.schemas import BookBible


def test_generate_best_propagates_quota():
    bible = MagicMock(spec=BookBible)
    bible.art_style = MagicMock()
    bible.art_style.negative_prompt = ""
    bible.voice = MagicMock()
    bible.voice.banned_patterns = []
    with patch("bookforge.pipeline.images.settings") as s:
        s.image_candidates_per_page = 1
        s.image_reject_text_artifacts = True
        s.image_accept_qa_rejects = True
        with patch(
            "bookforge.pipeline.images.img_client.generate",
            side_effect=img_client.ImageQuotaError("429 RESOURCE_EXHAUSTED"),
        ):
            with pytest.raises(img_client.ImageQuotaError):
                _generate_best("a photo", "square", bible)


def test_gen_gemini_falls_through_empty_response_without_crashing(monkeypatch):
    """Regression test for a live incident: the SDK can return a response with
    generated_images=None/empty WITHOUT raising (e.g. a filtered/blocked
    prompt) — indexing that directly crashed with a bare "'NoneType' object
    is not subscriptable" that matched neither the quota nor fatal-error
    patterns, silently breaking the fallback chain. It must now fall through
    to the next model cleanly instead."""
    monkeypatch.setattr(img_client.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(img_client.settings, "image_primary_model", "imagen-4.0-generate-001")
    monkeypatch.setattr(img_client.settings, "image_timeout_s", 30)

    empty_resp = MagicMock(generated_images=None)
    good_image = MagicMock()
    good_image.image.image_bytes = b"real-bytes"
    good_resp = MagicMock(generated_images=[good_image])

    fake_client = MagicMock()
    fake_client.models.generate_images.side_effect = [empty_resp, good_resp]

    with patch("google.genai.Client", return_value=fake_client):
        result = img_client._gen_gemini("solid color", "square")
    assert result == b"real-bytes"
    assert fake_client.models.generate_images.call_count == 2


def test_imagen_fallback_list_has_no_stale_model_ids():
    """The previous fallback list included two model ids that no longer exist
    on the API (404 NOT_FOUND on every call), which silently broke the whole
    fallback chain down to a single working model. Guard against reintroducing
    dead entries — every id must at least look like a real Imagen 4.x id."""
    assert img_client._IMAGEN_FALLBACKS == [
        "imagen-4.0-generate-001",
        "imagen-4.0-ultra-generate-001",
        "imagen-4.0-fast-generate-001",
    ]
