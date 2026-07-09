"""Image QA fallback + required-coverage gate."""
from __future__ import annotations

import pytest

from bookforge.errors import ImageCoverageError
from bookforge.pipeline import guide_images, images as images_mod
from bookforge.pipeline.images import _Score
from bookforge.schemas import (
    ArtStyle, BlockType, BookBible, Chapter, ContentBlock, GeneratedImage,
    ImageBrief, VoiceStyle,
)


def _bible(**kwargs) -> BookBible:
    defaults = dict(
        title="Test Book", subtitle="Sub", audience="Readers", tone="plain",
        book_kind="practical_guide",
        art_style=ArtStyle(
            medium="photo", palette="warm", lighting="daylight", era="now",
            composition_rules="", negative_prompt="no text",
            style_suffix="clean photo",
        ),
        voice=VoiceStyle(
            narrator_persona="n", tone_register="plain", pov="you",
            pacing="short", vocabulary="simple",
        ),
    )
    defaults.update(kwargs)
    return BookBible(**defaults)


def test_generate_best_accepts_qa_rejected_fallback(monkeypatch):
    """When every candidate fails hard QA, keep the best one flagged."""
    bible = _bible()
    rejected = _Score(relevance=0.8, aesthetic=0.7, has_garbled_text=True)
    cand = b"\xff\xd8\xff" + b"x" * 200

    monkeypatch.setattr(images_mod.settings, "image_candidates_per_page", 1)
    monkeypatch.setattr(images_mod.settings, "image_reject_text_artifacts", True)
    monkeypatch.setattr(images_mod.settings, "image_accept_qa_rejects", True)
    monkeypatch.setattr(images_mod.img_client, "generate", lambda *a, **k: cand)
    monkeypatch.setattr(images_mod, "_score", lambda *a, **k: rejected)
    monkeypatch.setattr(images_mod, "_sharpness", lambda *a, **k: 999.0)

    out = images_mod._generate_best("prompt", "square", bible, brief_prompt="jar")
    assert out is not None
    raw, score, flagged = out
    assert raw == cand
    assert flagged is True


def test_generate_best_returns_none_when_fallback_disabled(monkeypatch):
    bible = _bible()
    rejected = _Score(has_garbled_text=True)
    cand = b"\xff\xd8\xff" + b"x" * 200

    monkeypatch.setattr(images_mod.settings, "image_candidates_per_page", 1)
    monkeypatch.setattr(images_mod.settings, "image_reject_text_artifacts", True)
    monkeypatch.setattr(images_mod.settings, "image_accept_qa_rejects", False)
    monkeypatch.setattr(images_mod.img_client, "generate", lambda *a, **k: cand)
    monkeypatch.setattr(images_mod, "_score", lambda *a, **k: rejected)
    monkeypatch.setattr(images_mod, "_sharpness", lambda *a, **k: 999.0)

    assert images_mod._generate_best("p", "square", bible) is None


def test_assert_image_coverage_raises_when_slots_missing(monkeypatch):
    monkeypatch.setattr(guide_images.settings, "image_require_coverage", 1.0)
    bible = _bible(cover_subject="cover scene")
    ch = Chapter(
        index=1, title="One",
        blocks=[ContentBlock(
            type=BlockType.use_card, heading="H",
            image_brief=ImageBrief(prompt="jar", kind="photo", aspect="square"),
        )],
    )
    with pytest.raises(ImageCoverageError) as exc:
        guide_images._assert_image_coverage(bible, [ch], [], cover_key=None)
    assert exc.value.required == 3  # cover + card + divider
    assert exc.value.got == 0


def test_assert_image_coverage_passes_when_full(monkeypatch):
    monkeypatch.setattr(guide_images.settings, "image_require_coverage", 1.0)
    bible = _bible(cover_subject="cover scene")
    ch = Chapter(
        index=1, title="One",
        blocks=[ContentBlock(
            type=BlockType.use_card, heading="H",
            image_brief=ImageBrief(prompt="jar", kind="photo", aspect="square"),
        )],
    )
    imgs = [
        GeneratedImage(block_ref="1.0", storage_key="k1", width=10, height=10),
        GeneratedImage(block_ref="1.divider", storage_key="k2", width=10, height=10),
    ]
    guide_images._assert_image_coverage(bible, [ch], imgs, cover_key="books/x/cover.jpg")
