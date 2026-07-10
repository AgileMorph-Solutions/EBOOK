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
