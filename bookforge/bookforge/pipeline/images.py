"""Stage 3 — image generation, style-locked.

For each block with an image brief: build a prompt = brief + global style_suffix,
generate N candidates, score each with a vision judge for relevance+quality, pick
the best, then right-size to exactly the pixels the placed box needs at target DPI
(this is a major lever on final file size).
"""
from __future__ import annotations

import io

import structlog
from PIL import Image
from pydantic import BaseModel

from bookforge.clients import images as img_client
from bookforge.clients import llm, storage
from bookforge.config import settings
from bookforge.layout import image_slots
from bookforge.layout.image_slots import Placement
from bookforge.pipeline import postprocess
from bookforge.schemas import BookBible, Chapter, GeneratedImage

log = structlog.get_logger(__name__)


class _Score(BaseModel):
    relevance: float = 0.6  # 0-1: matches the brief/caption
    aesthetic: float = 0.6  # 0-1: composition, lighting, polish
    has_artifacts: bool = False  # garbled text, extra fingers, melted shapes
    nsfw: bool = False


def _sniff_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"  # gpt-image-1 default


def _score(image_bytes: bytes, brief_prompt: str) -> _Score:
    return llm.vision_json(
        f"Score this image as a hero illustration for: '{brief_prompt}'. "
        "Be a harsh art director.",
        image_bytes, _Score, media_type=_sniff_media_type(image_bytes),
    )


def _crop_to_box(data: bytes, box_w: int, box_h: int) -> tuple[bytes, int, int]:
    """Center-crop to target aspect, resize to exact box, de-AI finish."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    target_ar = box_w / box_h
    src_ar = w / h
    if src_ar > target_ar:
        new_w = int(h * target_ar)
        left = (w - new_w) // 2
        im = im.crop((left, 0, left + new_w, h))
    elif src_ar < target_ar:
        new_h = int(w / target_ar)
        top = (h - new_h) // 2
        im = im.crop((0, top, w, top + new_h))
    im = im.resize((box_w, box_h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    finished = postprocess.finish(buf.getvalue())
    out = Image.open(io.BytesIO(finished))
    return finished, out.width, out.height


def _rightsize(data: bytes, max_w: int, max_h: int) -> tuple[bytes, int, int]:
    """Downsample to the placed box at target DPI (narrative path — preserves aspect)."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    finished = postprocess.finish(buf.getvalue())
    out = Image.open(io.BytesIO(finished))
    return finished, out.width, out.height


# Hard negation embedded in the positive prompt (Imagen dropped negative_prompt on
# some versions) — kills the #1 illustration tell: garbled in-image text/labels.
_NO_TEXT = ("absolutely no text, no words, no letters, no labels, no captions, "
            "no logos, no watermark, no signage anywhere in the image")


def _negation(bible) -> str:
    """Fold the book's negative_prompt into the POSITIVE prompt. Imagen ignores a
    separate negative_prompt param on the versions we use, so the only reliable
    way to suppress (e.g.) insects/pests/clutter is to state the exclusion in the
    main prompt — otherwise a strong subject cue ('Roach Prevention') wins."""
    neg = (bible.art_style.negative_prompt or "").strip().rstrip(".")
    return f" Strictly exclude from the image: {neg}." if neg else ""


def _build_prompt(brief, bible) -> str:
    """Wrap the subject in a research-backed art-direction recipe.

    photo: describe the *capture* (camera/lens/film/light) + food styling; keep it
    ordinary and real — no 'beautiful/award-winning' booster words (refuted).
    illustration: a single locked illustration style for cohesion across the set.
    """
    subject = brief.prompt.strip().rstrip(".")
    palette = bible.art_style.palette
    neg = _negation(bible)
    if brief.kind == "illustration":
        style = bible.art_style.style_suffix or "hand-painted vintage gouache illustration"
        prompt = (f"{subject}. {style}, cohesive {palette} palette, textured paper feel, "
                  f"editorial spot illustration.")
    else:
        # photoreal hero — generic, driven by the book's art style (any topic)
        style = bible.art_style.style_suffix or "bright clean editorial photography"
        prompt = (
            f"A high-quality, realistic photograph of {subject}. {style}. "
            f"Natural soft daylight, shallow depth of field with a gently blurred "
            f"background, {palette} color story, real textures, clean and uncluttered "
            f"composition, modern and professional, no people's faces."
        )
    if brief.needs_in_image_text and brief.caption:
        prompt = (f'Render the words "{brief.caption}" clearly and legibly in '
                  f"a single tasteful caption area. {prompt}")
    else:
        prompt = f"{prompt} {_NO_TEXT}."
    prompt = f"{prompt}{neg}"
    # Compose for editorial overlay: keep the lower third visually calm so a
    # title card can sit there without fighting the subject. Avoid bright
    # highlights, smoke, or busy detail in the bottom band.
    if brief.aspect in ("full_bleed", "portrait"):
        prompt += (" Compose with the main subject in the upper two-thirds; "
                   "keep the lower third quiet and uncluttered (a calm dark "
                   "surface or shadow area) so an editorial title card can "
                   "be overlaid there. No steam, smoke, bright highlights, "
                   "or busy detail in the bottom third.")
    return prompt


def _negative_for(bible) -> str:
    parts: list[str] = []
    neg = (bible.art_style.negative_prompt or "").strip()
    if neg:
        parts.append(neg)
    banned = [b for b in (bible.voice.banned_patterns or []) if b]
    if banned:
        parts.append("clichés to avoid: " + ", ".join(banned))
    return "; ".join(parts)


# Placed-box pixel targets per aspect at 300 DPI (rough page boxes).
_ASPECT_BOX = {
    "full_bleed": (1850, 2775),   # full page bleed
    "portrait":   (1600, 2200),
    "wide":       (1850, 1400),   # feature band photo strip
    "square":     (1600, 1600),
}


def generate_image(
    prompt: str,
    aspect: str,
    key: str,
    *,
    placement: Placement | None = None,
) -> GeneratedImage | None:
    """Generate ONE image (lean guide path), center-crop to slot, store, return."""
    page_w, page_h = settings.page_px
    box_w, box_h = image_slots.box_for_brief(aspect, placement=placement)
    box_w = min(box_w, page_w)
    box_h = min(box_h, page_h)
    try:
        raw = img_client.generate(prompt, aspect=aspect)
    except Exception as exc:
        log.warning("image_gen_failed", key=key, err=str(exc)[:200])
        return None
    sized, w, h = _crop_to_box(raw, box_w, box_h)
    storage.put(key, sized, "image/jpeg")
    return GeneratedImage(block_ref=key, storage_key=key, width=w, height=h, score=0.9)


def generate_for_brief(
    brief,
    bible: BookBible,
    key: str,
    *,
    placement: Placement | None = None,
) -> GeneratedImage | None:
    """Lean single-image generation for a content block's image_brief."""
    gi = generate_image(
        _build_prompt(brief, bible), brief.aspect, key, placement=placement,
    )
    if gi:
        gi.block_ref = key
    return gi


def generate_cover(subject: str, bible: BookBible, key: str) -> GeneratedImage | None:
    """Cover top-band photo (title overlaid in HTML — no text baked in)."""
    style = bible.art_style.style_suffix or "bright clean editorial photography"
    prompt = (f"A high-quality, realistic photograph for a book cover background: "
              f"{subject}. {style}. Soft daylight, {bible.art_style.palette} color story, "
              f"clean composition with empty space for a title, modern and inviting. {_NO_TEXT}.")
    return generate_image(prompt, "wide", key, placement="cover")


def generate_for_chapter(chapter: Chapter, bible: BookBible) -> list[GeneratedImage]:
    out: list[GeneratedImage] = []
    page_w, page_h = settings.page_px  # full-bleed fallback at 300 DPI
    negative = _negative_for(bible)

    for bi, block in enumerate(chapter.blocks):
        if not block.image_brief:
            continue
        brief = block.image_brief
        prompt = _build_prompt(brief, bible)
        box_w, box_h = _ASPECT_BOX.get(brief.aspect, (page_w, page_h))

        # Generate candidates, hard-reject only NSFW; among the rest keep the
        # highest-ranked even if the scorer flags minor artifacts (so a page is
        # never left imageless over a strict score). Low scores -> review flag.
        best: tuple[float, bytes, bool] | None = None  # (rank, bytes, artifacts)
        for _ in range(max(1, settings.image_candidates_per_page)):
            try:
                cand = img_client.generate(
                    prompt, aspect=brief.aspect,
                    in_image_text=brief.needs_in_image_text,
                    negative_prompt=negative,
                )
            except Exception as exc:
                log.warning("image_gen_failed", err=str(exc)[:200])
                continue
            try:
                s = _score(cand, brief.prompt)
            except Exception as exc:
                log.warning("image_score_failed", err=str(exc)[:200])
                # scoring is non-critical — keep the image, flag for review
                best = (0.5, cand, True)
                continue
            if s.nsfw:
                continue
            rank = 0.6 * s.relevance + 0.4 * s.aesthetic
            if best is None or rank > best[0]:
                best = (rank, cand, s.has_artifacts)

        if best is None:
            log.warning("no_usable_image", chapter=chapter.index, block=bi)
            continue

        sized, w, h = _rightsize(best[1], box_w, box_h)
        key = f"books/{bible.title[:32]}/ch{chapter.index}_b{bi}.jpg".replace(" ", "_")
        storage.put(key, sized, "image/jpeg")
        out.append(GeneratedImage(
            block_ref=f"{chapter.index}.{bi}", storage_key=key,
            width=w, height=h, score=best[0],
            flagged_for_review=best[0] < 0.6 or best[2],
        ))
    log.info("images_done", chapter=chapter.index, count=len(out))
    return out
