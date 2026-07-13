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


def test_coverage_shortfall_warns_instead_of_raising(monkeypatch):
    # Always-ship: missing slots are a WARNING (layout renders empty slots
    # cleanly) — never an ImageCoverageError that kills a finished book.
    monkeypatch.setattr(guide_images.settings, "image_require_coverage", 1.0)
    bible = _bible(cover_subject="cover scene")
    ch = Chapter(
        index=1, title="One",
        blocks=[ContentBlock(
            type=BlockType.use_card, heading="H",
            image_brief=ImageBrief(prompt="jar", kind="photo", aspect="square"),
        )],
    )
    warning = guide_images._coverage_warning(bible, [ch], [], cover_key=None)
    assert warning is not None
    assert "0/3" in warning  # cover + card + divider


def test_coverage_warning_none_when_full(monkeypatch):
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
    assert guide_images._coverage_warning(
        bible, [ch], imgs, cover_key="books/x/cover.jpg") is None


def test_sync_card_image_briefs_every_entry():
    from bookforge.pipeline import content_guide
    from bookforge.schemas import EditorialProfile, EntrySchema, SectionPlan, UseEntry

    bible = _bible(
        profile=EditorialProfile(
            genre="cookbook",
            audience="cooks",
            angle="thrifty",
            entry_schema=EntrySchema(entry_noun="recipe"),
            image_density="every_entry",
        ),
        sections=[SectionPlan(index=1, title="S1", full_card_quota=3,
                              image_quota=1)],
    )
    ch = Chapter(
        index=1, title="S1",
        blocks=[
            ContentBlock(
                type=BlockType.use_card, heading=f"R{i}",
                entries=[UseEntry(number=i + 1, title=f"Recipe {i + 1}")],
                image_brief=(ImageBrief(prompt="first", kind="photo", aspect="square")
                             if i == 0 else None),
            )
            for i in range(3)
        ],
    )
    added = content_guide.sync_card_image_briefs([ch], bible)
    assert added == 2
    assert all(b.image_brief for b in ch.blocks if b.type == BlockType.use_card)


def test_sync_card_image_briefs_respects_quota_when_not_every_entry():
    from bookforge.pipeline import content_guide
    from bookforge.schemas import EditorialProfile, EntrySchema, SectionPlan, UseEntry

    bible = _bible(
        profile=EditorialProfile(
            genre="guide",
            audience="readers",
            angle="helpful",
            entry_schema=EntrySchema(entry_noun="tip"),
            image_density="standard",
        ),
        sections=[SectionPlan(index=1, title="S1", full_card_quota=3,
                              image_quota=1)],
    )
    ch = Chapter(
        index=1, title="S1",
        blocks=[
            ContentBlock(
                type=BlockType.use_card,
                entries=[UseEntry(number=i + 1, title=f"Tip {i + 1}")],
            )
            for i in range(3)
        ],
    )
    added = content_guide.sync_card_image_briefs([ch], bible)
    assert added == 1
    briefs = sum(1 for b in ch.blocks if b.image_brief)
    assert briefs == 1
