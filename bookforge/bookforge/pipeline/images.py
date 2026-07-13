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


_VISION_PROMPT = """Score this image as a hero illustration for: '{brief}'.
Be a harsh art director. Check for:
- relevance to the brief and aesthetic quality
- garbled or misspelled text, labels, signage, logos, or watermarks anywhere in the image
- visual artifacts (melted shapes, extra limbs, obvious AI glitches)
- NSFW content
Set has_garbled_text=true if ANY unwanted or illegible text appears in the image."""


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


# A crop kept fewer pixels than the slot wants: upscaling past this factor
# produces the soft "empty gradient" dividers vision-QA flagged. We cap the
# LANCZOS upscale (CSS covers the rest — same geometry, no fake detail) and
# skip the film-grain pass on upscaled images (grain on soft pixels reads as
# noise, not film).
_MAX_UPSCALE = 1.4
_GRAIN_UPSCALE_LIMIT = 1.15


def _crop_to_box(data: bytes, box_w: int, box_h: int) -> tuple[bytes, int, int]:
    """Center-crop to target aspect, resize toward the box (upscale-capped),
    de-AI finish."""
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
    upscale = box_w / im.width if im.width else 1.0
    if upscale > _MAX_UPSCALE:
        box_w = round(im.width * _MAX_UPSCALE)
        box_h = round(im.height * _MAX_UPSCALE)
        log.info("image_upscale_capped", requested=round(upscale, 2),
                 out_w=box_w, out_h=box_h)
    im = im.resize((box_w, box_h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    if upscale > _GRAIN_UPSCALE_LIMIT:
        finished = postprocess.finish(buf.getvalue(), grain=0.0)
    else:
        finished = postprocess.finish(buf.getvalue())
    out = Image.open(io.BytesIO(finished))
    return finished, out.width, out.height


def _sharpness(data: bytes) -> float:
    """Edge-energy proxy for focus (Pillow-only Laplacian-style measure)."""
    from PIL import ImageFilter, ImageStat

    im = Image.open(io.BytesIO(data)).convert("L")
    im.thumbnail((512, 512))
    edges = im.filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]


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
        prompt = (f"{subject}. {style}, cohesive {palette} palette, textured paper feel, "
                  f"editorial spot illustration.")
    else:
        style = bible.art_style.style_suffix or "bright clean editorial photography"
        era = (bible.art_style.era or "").strip().rstrip(".")
        era_bit = f"{era} feel, " if era else ""
        prompt = (
            f"A high-quality, realistic photograph of {subject}. {style}. "
            f"Natural soft daylight, shallow depth of field with a gently blurred "
            f"background, {palette} color story, real textures, clean and uncluttered "
            f"composition, {era_bit}no people's faces."
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
        prompt += (" Compose with the main subject in the upper two-thirds; "
                   "keep the lower third quiet and uncluttered (a calm dark "
                   "surface or shadow area) so an editorial title card can "
                   "be overlaid there. No steam, smoke, bright highlights, "
                   "or busy detail in the bottom third.")
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
) -> tuple[bytes, float, bool] | None:
    """Generate N candidates, vision-score, return (bytes, rank, flagged).

    When every candidate fails the hard QA filters (garbled text, artifacts,
    blur) but IMAGE_ACCEPT_QA_REJECTS is on, ship the best-scoring non-NSFW
    candidate flagged for review instead of leaving the slot empty."""
    negative = _negative_for(bible)
    subject = brief_prompt or prompt[:120]
    best: tuple[float, bytes, bool] | None = None
    fallback: tuple[float, bytes] | None = None

    for _ in range(max(1, settings.image_candidates_per_page)):
        try:
            cand = img_client.generate(
                prompt, aspect=aspect,
                in_image_text=in_image_text,
                negative_prompt=negative,
            )
        except (img_client.ImageQuotaError, img_client.ImageGenerationError):
            raise  # quota/auth — fail the stage immediately, don't burn every slot
        except Exception as exc:
            if llm.never_swallow(exc):
                raise
            log.warning("image_gen_failed", err=str(exc)[:200])
            continue
        try:
            s = _score(cand, subject)
        except Exception as exc:
            if llm.never_swallow(exc):
                raise
            log.warning("image_score_failed", err=str(exc)[:200])
            if fallback is None or 0.5 > fallback[0]:
                fallback = (0.5, cand)
            continue
        if s.nsfw:
            continue
        rank = 0.6 * s.relevance + 0.4 * s.aesthetic
        flagged = rank < 0.6 or s.has_artifacts or s.has_garbled_text
        if fallback is None or rank > fallback[0]:
            fallback = (rank, cand)

        hard_reject = False
        if s.has_garbled_text and settings.image_reject_text_artifacts:
            log.info("image_rejected_garbled_text", brief=subject[:80])
            hard_reject = True
        elif settings.image_reject_text_artifacts and s.has_artifacts:
            log.info("image_rejected_artifacts", brief=subject[:80])
            hard_reject = True
        else:
            try:
                sharp = _sharpness(cand)
            except Exception:
                sharp = 999.0
            if sharp < 20.0:  # out-of-focus / featureless gradient
                log.info("image_rejected_blurry", brief=subject[:80],
                         sharpness=round(sharp, 1))
                hard_reject = True
        if not hard_reject and (best is None or rank > best[0]):
            best = (rank, cand, flagged)
        # Adaptive candidates: a clean, well-ranked first candidate wins the
        # slot immediately — spending on more candidates buys nothing. Halves
        # typical Imagen usage (~98 → ~64 calls/book, under the 70/day quota).
        if settings.image_adaptive_candidates and best is not None and not best[2]:
            break

    if best is not None:
        return best[1], best[0], best[2]
    if fallback is not None and settings.image_accept_qa_rejects:
        log.warning("image_accepted_qa_fallback", brief=subject[:80],
                    rank=round(fallback[0], 2))
        return fallback[1], fallback[0], True
    return None


def generate_image(
    prompt: str,
    aspect: str,
    key: str,
    *,
    bible: BookBible | None = None,
    brief_prompt: str = "",
    placement: Placement | None = None,
) -> GeneratedImage | None:
    """Generate image(s), center-crop to slot, store, return."""
    page_w, page_h = settings.page_px
    box_w, box_h = image_slots.box_for_brief(aspect, placement=placement)
    box_w = min(box_w, page_w)
    box_h = min(box_h, page_h)

    if bible is not None:
        result = _generate_best(
            prompt, aspect, bible, brief_prompt=brief_prompt or prompt[:120],
        )
        if result is None:
            log.warning("image_gen_failed", key=key)
            return None
        raw, score, flagged = result
    else:
        try:
            raw = img_client.generate(prompt, aspect=aspect, negative_prompt=_NO_TEXT)
            score, flagged = 0.9, False
        except (img_client.ImageQuotaError, img_client.ImageGenerationError):
            raise
        except Exception as exc:
            if llm.never_swallow(exc):
                raise
            log.warning("image_gen_failed", key=key, err=str(exc)[:200])
            return None

    try:
        sized, w, h = _crop_to_box(raw, box_w, box_h)
        storage.put(key, sized, "image/jpeg")
    except Exception as exc:
        # Corrupt candidate or storage blip — skip this image, never sink the
        # book (downstream treats a missing image as an empty slot).
        log.warning("image_store_failed", key=key, err=str(exc)[:200])
        return None
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
) -> GeneratedImage | None:
    """Single-image generation for a content block's image_brief."""
    gi = generate_image(
        _build_prompt(brief, bible, placement=placement),
        brief.aspect, key,
        bible=bible,
        brief_prompt=brief.prompt,
        placement=placement,
    )
    if gi:
        gi.block_ref = key
    return gi


def generate_cover(subject: str, bible: BookBible, key: str) -> GeneratedImage | None:
    """Cover top-band photo (title overlaid in HTML — no text baked in)."""
    style = bible.art_style.style_suffix or "bright clean editorial photography"
    era = (bible.art_style.era or "").strip().rstrip(".")
    era_bit = f", {era} feel" if era else ""
    prompt = (f"A high-quality, realistic photograph for a book cover background: "
              f"{subject}. {style}. Soft daylight, {bible.art_style.palette} color story, "
              f"clean composition with empty space for a title, inviting{era_bit}. {_NO_TEXT}.")
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

        result = _generate_best(
            prompt, brief.aspect, bible,
            brief_prompt=brief.prompt,
            in_image_text=brief.needs_in_image_text,
        )
        if result is None:
            log.warning("no_usable_image", chapter=chapter.index, block=bi)
            continue

        raw, score, flagged = result
        key = f"books/{bible.title[:32]}/ch{chapter.index}_b{bi}.jpg".replace(" ", "_")
        try:
            sized, w, h = _rightsize(raw, box_w, box_h)
            storage.put(key, sized, "image/jpeg")
        except Exception as exc:
            log.warning("image_store_failed", key=key, err=str(exc)[:200])
            continue
        out.append(GeneratedImage(
            block_ref=f"{chapter.index}.{bi}", storage_key=key,
            width=w, height=h, score=score,
            flagged_for_review=flagged,
        ))
    log.info("images_done", chapter=chapter.index, count=len(out))
    return out
