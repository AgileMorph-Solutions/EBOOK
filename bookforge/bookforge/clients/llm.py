"""Anthropic Claude client with structured-output + budget tracking.

`structured()` forces the model to return JSON matching a Pydantic schema by
exposing it as a single tool and requiring that tool call. This is how we get
validated data (BookBible, Chapter, claim verdicts) instead of parsing prose.
"""
from __future__ import annotations

import json
from typing import TypeVar

import anthropic
import structlog
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bookforge.config import settings
from bookforge.cost import budget

log = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

# Only transient failures are worth retrying. A 400 BadRequestError (bad prompt,
# oversized request, "credit balance too low"), 401/403 auth errors, and 404s are
# deterministic — retrying them just wastes ~1-2min of backoff and then, without
# reraise, hides the real message inside an opaque RetryError. `reraise=True`
# below propagates the ORIGINAL exception so callers/UX see the actual cause.
_RETRYABLE = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,  # 5xx
)

_llm_retry = retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(4),
    wait=wait_exponential(min=2, max=30),
    reraise=True,
)


def is_unfunded(exc: BaseException) -> bool:
    """True when the Anthropic account is out of credit ("credit balance too
    low"). Distinct from an invalid key — this one is fixed by adding funds."""
    return (isinstance(exc, anthropic.BadRequestError)
            and "credit balance" in str(exc).lower())


def is_provider_unavailable(exc: BaseException) -> bool:
    """True for errors that fail identically on EVERY model — credits, invalid
    key, revoked access. Cascading to another model is pointless for these; they
    must bubble up so the job parks and auto-resumes once the account is fixed."""
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return True
    if is_unfunded(exc):
        return True
    return False


def _model_chain(primary: str) -> list[str]:
    """Primary model followed by configured fallbacks (deduped, order-preserving)."""
    chain = [primary]
    for m in settings.content_model_fallbacks.split(","):
        m = m.strip()
        if m and m not in chain:
            chain.append(m)
    return chain

# Approx per-MTok USD; used for the per-book budget guardrail, not billing.
# Sticker prices (Sonnet 5 has intro pricing of 2.0/10.0 through 2026-08-31;
# we budget at sticker so the guardrail stays valid when the intro lapses).
_PRICE = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set — populate .env")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _track(model: str, usage) -> None:
    pin, pout = _PRICE.get(model, _PRICE["claude-opus-4-8"])
    cost = (usage.input_tokens * pin + usage.output_tokens * pout) / 1_000_000
    budget.charge(cost)


def ping() -> None:
    """Cheap liveness probe (1 output token). Raises on the real problem —
    invalid key, no credits, or connectivity — so /health can report whether we
    can actually generate, not just whether a key string is present."""
    _get_client().messages.create(
        model=settings.content_model, max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )


def _cascade(model: str | None, call):
    """Run `call(model)` down the model chain. Transient errors are retried
    per-model by `_llm_retry` inside `call`; a MODEL-SPECIFIC failure (not-found,
    overload, non-credit 400, or a bad structured response) falls through to the
    next model. A provider-wide failure (credits/auth) is re-raised immediately
    so the job parks instead of pointlessly retrying every model."""
    chain = _model_chain(model or settings.content_model)
    last_err: Exception | None = None
    for i, m in enumerate(chain):
        try:
            return call(m)
        except Exception as exc:  # noqa: BLE001 — classify then decide
            if is_provider_unavailable(exc):
                raise
            last_err = exc
            if i + 1 < len(chain):
                log.warning("model_fallback", failed_model=m, next_model=chain[i + 1],
                            err=str(exc)[:200])
    assert last_err is not None
    raise last_err


@_llm_retry
def _complete_once(
    prompt: str, *, system: str, model: str, max_tokens: int,
    temperature: float | None,
) -> str:
    budget.assert_ok()
    kwargs = {} if temperature is None else {"temperature": temperature}
    resp = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    _track(model, resp.usage)
    return "".join(b.text for b in resp.content if b.type == "text")


def complete(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 8000,
    temperature: float | None = None,
) -> str:
    """Plain text completion, with transient retry + model-fallback cascade.
    `temperature` is omitted unless explicitly set — newer models (e.g. Opus 4.8)
    reject the deprecated parameter."""
    return _cascade(model, lambda m: _complete_once(
        prompt, system=system, model=m, max_tokens=max_tokens, temperature=temperature))


@_llm_retry
def _structured_once(
    prompt: str, schema: type[T], *, system: str, model: str, max_tokens: int,
    temperature: float | None,
) -> T:
    budget.assert_ok()
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    kwargs = {} if temperature is None else {"temperature": temperature}
    resp = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit"},
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    _track(model, resp.usage)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "emit":
            return schema.model_validate(block.input)
    raise ValueError(f"Model did not return a valid {schema.__name__}: {resp.content!r}")


def structured(
    prompt: str,
    schema: type[T],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 8000,
    temperature: float | None = None,
) -> T:
    """Force the model to emit JSON validated against `schema`, with transient
    retry + model-fallback cascade."""
    return _cascade(model, lambda m: _structured_once(
        prompt, schema, system=system, model=m, max_tokens=max_tokens,
        temperature=temperature))


@_llm_retry
def vision_json(prompt: str, image_bytes: bytes, schema: type[T], *, media_type="image/jpeg") -> T:
    """Vision call (image QA / scoring) returning validated JSON."""
    import base64

    model = settings.vision_qa_model
    budget.assert_ok()
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    resp = _get_client().messages.create(
        model=model,
        max_tokens=2000,
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type,
                    "data": base64.b64encode(image_bytes).decode(),
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    _track(model, resp.usage)
    for block in resp.content:
        if block.type == "tool_use":
            return schema.model_validate(block.input)
    raise ValueError("Vision model returned no structured output")
