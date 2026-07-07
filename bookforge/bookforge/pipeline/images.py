"""Stage 3 — image generation, style-locked.

For each block with an image brief: build a prompt = brief + global style_suffix,
generate N candidates, score each with a vision judge for relevance+quality, pick
the best, then right-size to exactly the pixels the placed box needs at target DPI
(this is a major lever on final file size).
"""
from __future__ import annotations

import io
from typing import Any

import structlog
from PIL import Image
from pydantic import BaseModel

from bookforge.clients import images as img_client
from bookforge.clients import llm, storage
from bookforge.clients.images import ImageGenerationError, ImageQuotaError
from bookforge.config import settings
from bookforge.layout import image_slots
from bookforge.layout.image_slots import Placement
from bookforge.pipeline import postprocess
from bookforge.schemas import BookBible, Chapter, GeneratedImage

log = structlog.get_logger(__name__)


class _Score(BaseModel):
    relevance: float = 0.6  # 0-1: matches the brief/caption
    aesthetic: float = 0.6  # 0-1: composition, lighting, polish
    has_artifacts: bool = False  # extra fingers, melted shapes
    has_garbled_text: bool = False  # illegible words, signage, labels, watermarks
    nsfw: bool = False


_VISION_PROMPT = """You are a harsh editorial art director evaluating an AI-generated image for a print-published book.
Subject brief: '{brief}'

Score on a 0.0–1.0 scale:
- relevance: Does the image clearly depict the described subject? 1.0=exact match, 0.5=loosely related, 0.0=wrong subject entirely
- aesthetic: Is it print-ready quality? Sharp focus, natural lighting, clean composition. 1.0=publishable, 0.5=acceptable, 0.0=unusable

Flag strictly:
- has_garbled_text: true if ANY text, letters, words, numbers, labels, logos, signs, captions, or watermarks appear ANYWHERE in the image (even partially visible or blurred)
- has_artifacts: true if ANY visual defects — extra/fused/melted limbs, distorted objects, impossible geometry, visible generation seams, blurry focal subject
- nsfw: true if sexually explicit, graphically violent, or otherwise inappropriate for a mainstream book

Be harsh. A score below 0.4 on either axis means the image is unacceptable for publication."""


def _sniff_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _score(image_bytes: bytes, brief_prompt: str) -> _Score:
    return llm.vision_json(
        _VISION_PROMPT.format(brief=brief_prompt),
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
    """Fold the book's negative_prompt into the POSITIVE prompt."""
    neg = (bible.art_style.negative_prompt or "").strip().rstrip(".")
    return f" Strictly exclude from the image: {neg}." if neg else ""


def _baked_text_allowed(brief: Any) -> bool:
    """Imagen cannot spell reliably — only OpenAI may bake text into pixels."""
    if not getattr(brief, "needs_in_image_text", False):
        return False
    if settings.image_primary_provider.lower() != "openai":
        log.warning(
            "in_image_text_skipped",
            reason="Imagen cannot render legible text; use HTML overlay instead",
            caption=getattr(brief, "caption", ""),
        )
        return False
    return bool(getattr(brief, "caption", ""))


def _build_prompt(brief, bible, *, placement: Placement | None = None) -> str:
    """Wrap the subject in a research-backed art-direction recipe."""
    subject = brief.prompt.strip().rstrip(".")
    palette = bible.art_style.palette
    neg = _negation(bible)
    if brief.kind == "illustration":
        style = bible.art_style.style_suffix or "hand-painted vintage gouache illustration"
        prompt = (
            f"{subject}. {style}, cohesive {palette} palette, textured paper feel, "
            f"editorial spot illustration. Print-ready quality, sharp lines, "
            f"no blurry areas, no visual defects."
        )
    else:
        style = bible.art_style.style_suffix or "bright clean editorial photography"
        prompt = (
            f"A high-quality, realistic photograph of {subject}. {style}. "
            f"Natural soft daylight, shallow depth of field with a gently blurred "
            f"background, {palette} color story, real textures, clean and uncluttered "
            f"composition, modern and professional, no people's faces. "
            f"Crisp fine detail, high dynamic range, professional magazine quality. "
            f"Sharp focus on the subject, print-ready quality, no visual artifacts. "
            f"No low-resolution, oversaturated, or plastic-looking surfaces."
        )
    if _baked_text_allowed(brief):
        prompt = (f'Render the words "{brief.caption}" clearly and legibly in '
                  f"a single tasteful caption area. {prompt}")
    else:
        prompt = f"{prompt} {_NO_TEXT}."
    prompt = f"{prompt}{neg}"
    if placement == "divider":
        prompt += (
            " Shot as a tall vertical portrait for a magazine right-panel strip. "
            "Place the main subject dead center with generous margin on all four sides; "
            "the entire scene must fit inside the frame with nothing cut off at the edges. "
            "Even lighting from top to bottom; avoid wide panoramic shelf shots."
        )
    elif brief.aspect in ("full_bleed", "portrait"):
        prompt += (
            " Compose with the main subject in the upper two-thirds. "
            "The lower third MUST be a dark, even, low-detail zone — a calm shadow, "
            "plain dark surface, or soft out-of-focus area with no bright highlights, "
            "steam, smoke, or busy detail — because an editorial title card with white "
            "text will be overlaid there. Keep the bottom third quiet and legible."
        )
    return prompt


def _negative_for(bible) -> str:
    parts: list[str] = [_NO_TEXT]
    neg = (bible.art_style.negative_prompt or "").strip()
    if neg:
        parts.append(neg)
    banned = [b for b in (bible.voice.banned_patterns or []) if b]
    if banned:
        parts.append("clichés to avoid: " + ", ".join(banned))
    return "; ".join(parts)


# Placed-box pixel targets per aspect at 300 DPI (rough page boxes).
_ASPECT_BOX = {
    "full_bleed": (1850, 2775),
    "portrait":   (1600, 2200),
    "wide":       (1850, 1400),
    "square":     (1600, 1600),
}


def _generate_best(
    prompt: str,
    aspect: str,
    bible: BookBible,
    *,
    brief_prompt: str = "",
    in_image_text: bool = False,
) -> tuple[bytes, float, bool]:
    """Generate N candidates, vision-score, return (bytes, rank, flagged)."""
    negative = _negative_for(bible)
    subject = brief_prompt or prompt[:120]
    best: tuple[float, bytes, bool] | None = None
    best_rejected: tuple[float, bytes, bool] | None = None  # fallback when all candidates are flagged

    for _ in range(max(1, settings.image_candidates_per_page)):
        try:
            cand = img_client.generate(
                prompt, aspect=aspect,
                in_image_text=in_image_text,
                negative_prompt=negative,
            )
        except ImageQuotaError:
            raise  # propagate immediately — quota won't recover mid-run
        except ImageGenerationError:
            raise  # fatal provider error — don't retry other candidates
        except Exception as exc:
            log.warning("image_gen_failed", err=str(exc)[:200])
            continue
        try:
            s = _score(cand, subject)
        except Exception as exc:
            log.warning("image_score_failed", err=str(exc)[:200])
            best = (0.5, cand, True)
            continue
        if s.nsfw:
            continue
        if s.has_garbled_text and settings.image_reject_text_artifacts:
            log.info("image_rejected_garbled_text", brief=subject[:80])
            rank = 0.6 * s.relevance + 0.4 * s.aesthetic
            if best_rejected is None or rank > best_rejected[0]:
                best_rejected = (rank, cand, True)
            continue
        if settings.image_reject_text_artifacts and s.has_artifacts:
            log.info("image_rejected_artifacts", brief=subject[:80])
            rank = 0.6 * s.relevance + 0.4 * s.aesthetic
            if best_rejected is None or rank > best_rejected[0]:
                best_rejected = (rank, cand, True)
            continue
        rank = 0.6 * s.relevance + 0.4 * s.aesthetic
        if rank < 0.35:
            log.info("image_rejected_low_score", brief=subject[:80], rank=round(rank, 2))
            continue
        flagged = rank < 0.6 or s.has_artifacts or s.has_garbled_text
        if best is None or rank > best[0]:
            best = (rank, cand, flagged)

    if best is None:
        if best_rejected is not None:
            log.warning("image_using_rejected_fallback", brief=subject[:80],
                        rank=round(best_rejected[0], 2))
            best = best_rejected
        else:
            raise ImageGenerationError(f"all image candidates failed for brief: {subject[:80]}")
    return best[1], best[0], best[2]


def generate_image(
    prompt: str,
    aspect: str,
    key: str,
    *,
    bible: BookBible | None = None,
    brief_prompt: str = "",
    placement: Placement | None = None,
) -> GeneratedImage:
    """Generate image(s), center-crop to slot, store, return."""
    page_w, page_h = settings.page_px
    box_w, box_h = image_slots.box_for_brief(aspect, placement=placement)
    box_w = min(box_w, page_w)
    box_h = min(box_h, page_h)

    if bible is not None:
        raw, score, flagged = _generate_best(
            prompt, aspect, bible, brief_prompt=brief_prompt or prompt[:120],
        )
    else:
        try:
            raw = img_client.generate(prompt, aspect=aspect, negative_prompt=_NO_TEXT)
            score, flagged = 0.9, False
        except (ImageQuotaError, ImageGenerationError):
            raise
        except Exception as exc:
            raise ImageGenerationError(f"image generation failed for {key}: {exc}") from exc

    sized, w, h = _crop_to_box(raw, box_w, box_h)
    storage.put(key, sized, "image/jpeg")
    return GeneratedImage(
        block_ref=key, storage_key=key, width=w, height=h,
        score=score, flagged_for_review=flagged,
    )


def generate_for_brief(
    brief,
    bible: BookBible,
    key: str,
    *,
    placement: Placement | None = None,
) -> GeneratedImage:
    """Single-image generation for a content block's image_brief."""
    gi = generate_image(
        _build_prompt(brief, bible, placement=placement),
        brief.aspect, key,
        bible=bible,
        brief_prompt=brief.prompt,
        placement=placement,
    )
    gi.block_ref = key
    return gi


def generate_cover(subject: str, bible: BookBible, key: str) -> GeneratedImage:
    """Cover top-band photo (title overlaid in HTML — no text baked in)."""
    style = bible.art_style.style_suffix or "bright clean editorial photography"
    prompt = (
        f"A high-quality, realistic photograph for a book cover background: "
        f"{subject}. {style}. Soft daylight, {bible.art_style.palette} color story, "
        f"clean composition with generous empty space at the top for a title overlay, "
        f"modern and inviting, print-ready, sharp focus, no people. {_NO_TEXT}."
    )
    return generate_image(prompt, "wide", key, bible=bible, brief_prompt=subject,
                          placement="cover")


def generate_for_chapter(chapter: Chapter, bible: BookBible) -> list[GeneratedImage]:
    out: list[GeneratedImage] = []
    page_w, page_h = settings.page_px

    for bi, block in enumerate(chapter.blocks):
        if not block.image_brief:
            continue
        brief = block.image_brief
        prompt = _build_prompt(brief, bible)
        box_w, box_h = _ASPECT_BOX.get(brief.aspect, (page_w, page_h))

        raw, score, flagged = _generate_best(
            prompt, brief.aspect, bible,
            brief_prompt=brief.prompt,
            in_image_text=brief.needs_in_image_text,
        )
        sized, w, h = _rightsize(raw, box_w, box_h)
        key = f"books/{bible.title[:32]}/ch{chapter.index}_b{bi}.jpg".replace(" ", "_")
        storage.put(key, sized, "image/jpeg")
        out.append(GeneratedImage(
            block_ref=f"{chapter.index}.{bi}", storage_key=key,
            width=w, height=h, score=score,
            flagged_for_review=flagged,
        ))
    log.info("images_done", chapter=chapter.index, count=len(out))
    return out
