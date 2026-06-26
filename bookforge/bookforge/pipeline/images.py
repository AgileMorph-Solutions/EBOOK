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


def _rightsize(data: bytes, max_w: int, max_h: int) -> tuple[bytes, int, int]:
    """Downsample to the placed box at target DPI, apply the de-AI photographic
    finish (grade + grain + vignette), and recompress for print."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    finished = postprocess.finish(buf.getvalue())  # cohesive filmic look
    out = Image.open(io.BytesIO(finished))
    return finished, out.width, out.height


# Hard negation embedded in the positive prompt (Imagen dropped negative_prompt on
# some versions) — kills the #1 illustration tell: garbled in-image text/labels.
_NO_TEXT = ("absolutely no text, no words, no letters, no labels, no captions, "
            "no logos, no watermark, no signage anywhere in the image")


def _build_prompt(brief, bible) -> str:
    """Wrap the subject in a research-backed art-direction recipe.

    photo: describe the *capture* (camera/lens/film/light) + food styling; keep it
    ordinary and real — no 'beautiful/award-winning' booster words (refuted).
    illustration: a single locked illustration style for cohesion across the set.
    """
    subject = brief.prompt.strip().rstrip(".")
    palette = bible.art_style.palette
    if brief.kind == "illustration":
        style = bible.art_style.style_suffix or "hand-painted vintage gouache illustration"
        prompt = (f"{subject}. {style}, cohesive {palette} palette, textured paper feel, "
                  f"editorial spot illustration.")
    else:
        prompt = (
            f"Editorial food photograph of {subject}. Shot on a 50mm lens, natural soft "
            f"window light from the side, shallow depth of field with a softly blurred "
            f"background, subtle film grain, professional food styling on a matte ceramic "
            f"surface, {palette} color story, realistic textures and crumbs, unretouched, "
            f"documentary cookbook style."
        )
    if brief.needs_in_image_text and brief.caption:
        prompt = (f'Render the words "{brief.caption}" clearly and legibly in '
                  f"a single tasteful caption area. {prompt}")
    else:
        prompt = f"{prompt} {_NO_TEXT}."
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


# Placed-box pixel targets per aspect at 300 DPI (rough cookbook-page boxes).
_ASPECT_BOX = {
    "full_bleed": (1850, 2775),   # full page bleed
    "portrait":   (1600, 2200),
    "wide":       (1850, 1400),   # feature band photo strip
    "square":     (1600, 1600),
}


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
