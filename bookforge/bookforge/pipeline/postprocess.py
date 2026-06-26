"""De-AI image post-processing.

Research finding (verified): raw diffusion output reads as fake because it is
*too clean* — no sensor noise, smoothed micro-contrast. The fix is to add a
cohesive photographic finish: a filmic color grade, real film grain, and a
subtle vignette, applied identically to every image so the whole book's imagery
looks like one art-directed shoot. Pillow-only (no numpy dependency).
"""
from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter


def _grade(im: Image.Image) -> Image.Image:
    """Subtle filmic grade: gentle S-contrast, slight desaturation, warm split-tone."""
    im = ImageEnhance.Contrast(im).enhance(1.07)
    im = ImageEnhance.Color(im).enhance(0.92)      # pull back AI over-saturation
    im = ImageEnhance.Brightness(im).enhance(0.99)
    r, g, b = im.split()
    r = r.point(lambda v: min(255, int(v + 7)))    # warm highlights
    b = b.point(lambda v: max(0, int(v - 6)))      # cool/teal shadows
    return Image.merge("RGB", (r, g, b))


def _grain(im: Image.Image, strength: float = 0.10) -> Image.Image:
    """Overlay monochrome film grain at low opacity (kills the 'too clean' tell)."""
    noise = Image.effect_noise(im.size, 26).convert("RGB")
    return Image.blend(im, ImageChops.overlay(im, noise), strength)


def _vignette(im: Image.Image, strength: float = 0.22) -> Image.Image:
    """Darken edges slightly — real lenses do this; AI rarely does."""
    w, h = im.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse(
        [-w * 0.12, -h * 0.12, w * 1.12, h * 1.12], fill=255
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(w, h) * 0.16))
    dark = ImageEnhance.Brightness(im).enhance(1 - strength)
    return Image.composite(im, dark, mask)


def finish(data: bytes, *, grain: float = 0.10) -> bytes:
    """Apply the full de-AI finish and return print-quality JPEG bytes."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im = _grade(im)
    im = _vignette(im)
    im = _grain(im, grain)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90, optimize=True, progressive=True)
    return buf.getvalue()
