"""Image-generation client.

Providers:
  * gemini  -> Imagen 4/3   (high quality photoreal)  [default]
  * openai  -> gpt-image-1  (high quality + best in-image text rendering)

Routing: `IMAGE_PRIMARY_PROVIDER` and `IMAGE_PRIMARY_MODEL` select the engine.
`IMAGE_ALLOW_PROVIDER_FALLBACK` controls whether Imagen failures fall back to OpenAI.
Returns raw image bytes; right-sizing/upscaling happens in pipeline.images.
"""
from __future__ import annotations

import base64

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from bookforge.config import settings
from bookforge.cost import budget

log = structlog.get_logger(__name__)

# Rough per-image USD; for the budget guardrail.
_IMG_COST = 0.17

# Aspect -> OpenAI size strings (gpt-image-1 supports these three).
_OPENAI_SIZE = {
    "full_bleed": "1024x1536",  # portrait, matches Letter/A4 page
    "portrait": "1024x1536",
    "square": "1024x1024",
    "wide": "1536x1024",
}
# Aspect -> Gemini Imagen aspect_ratio strings.
_GEMINI_AR = {
    "full_bleed": "3:4",
    "portrait": "3:4",
    "square": "1:1",
    "wide": "4:3",
}

_openai_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set — populate .env")
        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _validate_provider(provider: str) -> None:
    if provider == "gemini" and not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — populate .env")
    if provider == "openai" and not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — populate .env")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _gen_openai(prompt: str, aspect: str, negative_prompt: str = "") -> bytes:
    model = settings.image_primary_model or "gpt-image-1"
    # gpt-image-1 has no negative-prompt slot — fold it into the positive prompt.
    full = f"{prompt} Avoid: {negative_prompt}." if negative_prompt else prompt
    resp = _openai().images.generate(
        model=model,
        prompt=full,
        size=_OPENAI_SIZE.get(aspect, _OPENAI_SIZE["full_bleed"]),
        quality="high",
        n=1,
    )
    return base64.b64decode(resp.data[0].b64_json)


# Best-first Imagen model order; falls through on availability/permission errors.
_IMAGEN_FALLBACKS = [
    "imagen-4.0-generate-001",
    "imagen-4.0-generate-preview-06-06",
    "imagen-3.0-generate-002",
]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _gen_gemini(prompt: str, aspect: str, negative_prompt: str = "") -> bytes:
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — populate .env")
    client = genai.Client(api_key=settings.gemini_api_key)
    # Imagen on the Developer API rejects `negative_prompt` (Enterprise/Vertex
    # only). Fold it into the positive prompt instead.
    full = f"{prompt} Avoid: {negative_prompt}." if negative_prompt else prompt
    cfg = types.GenerateImagesConfig(
        number_of_images=1,
        aspect_ratio=_GEMINI_AR.get(aspect, _GEMINI_AR["full_bleed"]),
    )
    # Try configured model first, then best-known Imagen versions.
    models = [settings.image_primary_model] + [
        m for m in _IMAGEN_FALLBACKS if m != settings.image_primary_model
    ]
    last_err: Exception | None = None
    for model in models:
        try:
            resp = client.models.generate_images(model=model, prompt=full, config=cfg)
            return resp.generated_images[0].image.image_bytes
        except Exception as exc:  # availability / permission / not-found
            last_err = exc
            log.warning("imagen_model_failed", model=model, err=str(exc)[:160])
    raise RuntimeError(f"all Imagen models failed; last error: {last_err}")


def generate(prompt: str, *, aspect: str = "full_bleed", in_image_text: bool = False,
             negative_prompt: str = "") -> bytes:
    """Generate one image. `in_image_text` is informational — gpt-image-1 renders
    text well natively; Imagen should avoid baked-in text."""
    budget.assert_ok()
    provider = settings.image_primary_provider.lower()
    model = settings.image_primary_model
    _validate_provider(provider)
    fallback_used = False

    if provider == "gemini":
        try:
            data = _gen_gemini(prompt, aspect, negative_prompt=negative_prompt)
        except Exception as exc:
            if settings.image_allow_provider_fallback:
                log.warning("gemini_fallback_openai", err=str(exc)[:160])
                _validate_provider("openai")
                data = _gen_openai(prompt, aspect, negative_prompt=negative_prompt)
                provider, model, fallback_used = "openai", settings.image_primary_model, True
            else:
                raise
    else:
        data = _gen_openai(prompt, aspect, negative_prompt=negative_prompt)

    budget.charge(_IMG_COST)
    log.info("image_generated", provider=provider, model=model, fallback_used=fallback_used,
             in_image_text=in_image_text)
    return data
