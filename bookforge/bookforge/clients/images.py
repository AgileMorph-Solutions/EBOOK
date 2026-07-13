"""Image-generation client.

Providers:
  * gemini  -> Imagen 4/3   (high quality photoreal)  [default]
  * openai  -> gpt-image-1  (high quality + best in-image text rendering)

Routing: `IMAGE_PRIMARY_PROVIDER` and `IMAGE_PRIMARY_MODEL` select the engine.
`IMAGE_ALLOW_PROVIDER_FALLBACK` controls whether gemini failures — INCLUDING
quota exhaustion — fall back to OpenAI (a Gemini daily quota says nothing about
the OpenAI account). Returns raw image bytes; right-sizing happens in
pipeline.images.
"""
from __future__ import annotations

import base64
import time

import structlog
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from bookforge.clients import quota as quota_mod
from bookforge.config import settings
from bookforge.cost import budget
from bookforge.pipeline import runctx


class ImageQuotaError(RuntimeError):
    """The image provider returned a quota / rate-limit (429) error.

    Carries `quota_class` ("daily" | "per_minute" | "unknown") and `wait_until`
    (aware UTC datetime or None) so the API can park until retrying can
    actually succeed instead of burning attempts on a daily quota.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.quota_class, self.wait_until = quota_mod.parse_quota(message)


class ImageGenerationError(RuntimeError):
    """Fatal, non-retryable image generation failure (billing, auth, all models exhausted)."""

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
        _openai_client = OpenAI(api_key=settings.openai_api_key,
                                timeout=settings.image_timeout_s, max_retries=1)
    return _openai_client


def _validate_provider(provider: str) -> None:
    if provider == "gemini" and not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — populate .env")
    if provider == "openai" and not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — populate .env")


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "429" in str(exc) or "resource_exhausted" in s or "quota" in s or "rate limit" in s


def _is_fatal_imagen_error(exc: Exception) -> bool:
    s = str(exc)
    lower = s.lower()
    # 400 INVALID_ARGUMENT — billing / paid-plan errors
    if "400" in s or "invalid_argument" in lower:
        return True
    # 403 permission denied / unauthenticated
    if "403" in s or "permission_denied" in lower or "unauthenticated" in lower:
        return True
    return False


# reraise=True on both generators: callers and the API's failure classifier
# must see the ORIGINAL exception, never an opaque tenacity RetryError.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20),
       retry=retry_if_not_exception_type((ImageQuotaError, ImageGenerationError)),
       reraise=True)
def _gen_openai(prompt: str, aspect: str, negative_prompt: str = "") -> bytes:
    model = "gpt-image-1" if settings.image_primary_provider != "openai" else (
        settings.image_primary_model or "gpt-image-1")
    # gpt-image-1 has no negative-prompt slot — fold it into the positive prompt.
    full = f"{prompt} Avoid: {negative_prompt}." if negative_prompt else prompt
    try:
        resp = _openai().images.generate(
            model=model,
            prompt=full,
            size=_OPENAI_SIZE.get(aspect, _OPENAI_SIZE["full_bleed"]),
            quality="high",
            n=1,
        )
    except Exception as exc:
        if _is_quota_error(exc):
            raise ImageQuotaError(str(exc)) from exc
        raise
    return base64.b64decode(resp.data[0].b64_json)


# Best-first Imagen model order; falls through on availability/permission errors.
_IMAGEN_FALLBACKS = [
    "imagen-4.0-generate-001",
    "imagen-4.0-generate-preview-06-06",
    "imagen-3.0-generate-002",
]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20),
       retry=retry_if_not_exception_type((ImageQuotaError, ImageGenerationError)),
       reraise=True)
def _gen_gemini(prompt: str, aspect: str, negative_prompt: str = "") -> bytes:
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — populate .env")
    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=int(settings.image_timeout_s * 1000)),
    )
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
        except Exception as exc:
            if _is_quota_error(exc):
                # 429 means the API key is exhausted — no point trying other models
                # or letting tenacity retry. Raise so generate() can classify it.
                raise ImageQuotaError(str(exc)) from exc
            if _is_fatal_imagen_error(exc):
                # 400/403 billing or auth — no point trying other models.
                raise ImageGenerationError(str(exc)) from exc
            last_err = exc
            log.warning("imagen_model_failed", model=model, err=str(exc)[:160])
    raise ImageGenerationError(f"all Imagen models failed; last error: {last_err}") from last_err


def ping() -> None:
    """Cheap liveness probe for the configured image provider.

    Raises ImageQuotaError / ImageGenerationError on quota or billing failures.
    """
    probe = (settings.readiness_image_probe or "metadata").lower()
    provider = settings.image_primary_provider.lower()
    _validate_provider(provider)

    if probe == "off":
        return

    if provider == "gemini":
        _ping_gemini(probe)
    else:
        _ping_openai(probe)


def _ping_gemini(probe: str) -> None:
    from google import genai

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — populate .env")
    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.image_primary_model or _IMAGEN_FALLBACKS[0]

    if probe == "generate":
        _gen_gemini("solid color", "square")
        return

    try:
        client.models.get(model=model)
    except Exception as exc:
        if _is_quota_error(exc):
            raise ImageQuotaError(str(exc)) from exc
        if _is_fatal_imagen_error(exc):
            raise ImageGenerationError(str(exc)) from exc
        raise


def _ping_openai(probe: str) -> None:
    if probe == "generate":
        _gen_openai("solid color", "square")
        return
    try:
        _openai().models.list()
    except Exception as exc:
        if _is_quota_error(exc):
            raise ImageQuotaError(str(exc)) from exc
        if "401" in str(exc) or "403" in str(exc):
            raise ImageGenerationError(str(exc)) from exc
        raise


def _openai_fallback_ok() -> bool:
    return bool(settings.image_allow_provider_fallback and settings.openai_api_key)


def generate(prompt: str, *, aspect: str = "full_bleed", in_image_text: bool = False,
             negative_prompt: str = "") -> bytes:
    """Generate one image. `in_image_text` is informational — gpt-image-1 renders
    text well natively; Imagen should avoid baked-in text.

    Gemini failure handling, cheapest exit first:
      per-minute 429 -> one in-stage backoff retry on gemini;
      any quota/fatal/unknown failure -> OpenAI fallback when enabled;
      otherwise raise the classified error for the caller/park logic.
    """
    runctx.checkpoint()
    budget.assert_ok()
    provider = settings.image_primary_provider.lower()
    model = settings.image_primary_model
    _validate_provider(provider)
    fallback_used = False

    if provider == "gemini":
        try:
            data = _gen_gemini(prompt, aspect, negative_prompt=negative_prompt)
        except ImageQuotaError as exc:
            data = None
            if exc.quota_class == quota_mod.PER_MINUTE:
                # A per-minute rate limit clears in seconds — one bounded wait
                # beats parking the whole job for it.
                log.warning("imagen_per_minute_limit_retry", err=str(exc)[:160])
                time.sleep(45)
                runctx.checkpoint()
                try:
                    data = _gen_gemini(prompt, aspect, negative_prompt=negative_prompt)
                except (ImageQuotaError, ImageGenerationError):
                    data = None
            if data is None:
                if _openai_fallback_ok():
                    log.warning("gemini_quota_fallback_openai",
                                quota_class=exc.quota_class, err=str(exc)[:160])
                    data = _gen_openai(prompt, aspect, negative_prompt=negative_prompt)
                    provider, model, fallback_used = "openai", "gpt-image-1", True
                else:
                    raise
        except ImageGenerationError as exc:
            if _openai_fallback_ok():
                log.warning("gemini_fatal_fallback_openai", err=str(exc)[:160])
                data = _gen_openai(prompt, aspect, negative_prompt=negative_prompt)
                provider, model, fallback_used = "openai", "gpt-image-1", True
            else:
                raise
        except Exception as exc:
            if _openai_fallback_ok():
                log.warning("gemini_fallback_openai", err=str(exc)[:160])
                data = _gen_openai(prompt, aspect, negative_prompt=negative_prompt)
                provider, model, fallback_used = "openai", "gpt-image-1", True
            else:
                raise
    else:
        data = _gen_openai(prompt, aspect, negative_prompt=negative_prompt)

    budget.charge(_IMG_COST)
    log.info("image_generated", provider=provider, model=model, fallback_used=fallback_used,
             in_image_text=in_image_text)
    return data
