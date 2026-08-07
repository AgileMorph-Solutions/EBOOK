"""Anthropic Claude client with structured-output + budget tracking.

`structured()` forces the model to return JSON matching a Pydantic schema by
exposing it as a single tool and requiring that tool call. This is how we get
validated data (BookBible, Chapter, claim verdicts) instead of parsing prose.

Robustness contract (per call, cheapest first):
  transient retry (same model) -> truncation bump / validation feedback retry
  (same model) -> model cascade (next fallback model) -> caller-level
  degradation or park. Tenacity is the SINGLE retry owner — the SDK's internal
  retry is disabled — and every request is bounded by LLM_TIMEOUT_S, so a hung
  call can never wedge a job for the SDK's default 10 minutes.
"""
from __future__ import annotations

import threading
from typing import TypeVar

import anthropic
import httpx
import structlog
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type

from bookforge.config import settings
from bookforge.cost import budget
from bookforge.pipeline import runctx

log = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

# Only transient failures are worth retrying. A 400 BadRequestError (bad prompt,
# oversized request, "credit balance too low"), 401/403 auth errors, and 404s are
# deterministic — retrying them just wastes ~1-2min of backoff and then, without
# reraise, hides the real message inside an opaque RetryError. `reraise=True`
# below propagates the ORIGINAL exception so callers/UX see the actual cause.
_RETRYABLE_TRANSIENT = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,  # 5xx incl. 503/504
)
# 529 "overloaded" is platform-wide pressure, not a model-specific fault: retry
# it on the SAME model with a longer backoff instead of instantly shedding
# quality down the fallback cascade (Opus -> Sonnet -> Haiku).
_RETRYABLE_OVERLOAD = (anthropic.OverloadedError,)
_RETRYABLE = _RETRYABLE_TRANSIENT + _RETRYABLE_OVERLOAD

_MAX_TOKENS_CEILING = 16000


def _retry_wait(retry_state) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    if isinstance(exc, _RETRYABLE_OVERLOAD):
        return min(120.0, 10.0 * (2 ** (attempt - 1)))
    return min(30.0, 2.0 * (2 ** (attempt - 1)))


def _retry_stop(retry_state) -> bool:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    limit = 5 if isinstance(exc, _RETRYABLE_OVERLOAD) else 4
    return retry_state.attempt_number >= limit


_llm_retry = retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=_retry_stop,
    wait=_retry_wait,
    reraise=True,
)

# Process-wide ceiling on concurrent in-flight requests so per-stage worker
# pools (sections/images/QA) compose with MAX_CONCURRENT_BOOKS without
# stampeding the API with 429s.
_inflight = threading.BoundedSemaphore(max(1, settings.llm_max_inflight))


class _TruncatedOutput(Exception):
    """Internal: response hit max_tokens. Deliberately NOT retryable by
    tenacity and NOT a ValueError — handled once by the same-model bump in
    `_structured_attempt` before the failure may cascade to the next model."""


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


def never_swallow(exc: BaseException) -> bool:
    """True for exceptions that degrade-don't-die handlers must RE-RAISE instead
    of skipping the current item: provider-wide failures (job parks), budget
    exhaustion (orchestrator ships partial work), and the cooperative
    cancel/deadline signals raised from runctx between calls — swallowing those
    would let a cancelled or overdue job keep burning its remaining items.

    A RateLimitError that reaches here has already exhausted `_cascade`'s full
    model fallback chain (each model retried 4x by tenacity) — every model is
    rate-limited, not just one. That's a job-level condition, not a one-off
    item failure, so it must surface (park + auto-resume) instead of silently
    dropping the item."""
    from bookforge.cost import BudgetExceeded
    from bookforge.errors import JobDeadlineError, PipelineCancelledError

    if isinstance(exc, anthropic.RateLimitError):
        return True
    return is_provider_unavailable(exc) or isinstance(
        exc, (BudgetExceeded, JobDeadlineError, PipelineCancelledError))


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
        _client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            max_retries=0,  # tenacity owns retries; no hidden SDK double-retry
            timeout=httpx.Timeout(settings.llm_timeout_s, connect=5.0),
        )
    return _client


def _track(model: str, usage) -> None:
    pin, pout = _PRICE.get(model, _PRICE["claude-opus-4-8"])
    # Cached prefix tokens are billed at 1.25x (write) / 0.1x (read) of the
    # input price and are NOT included in usage.input_tokens.
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (usage.input_tokens * pin + usage.output_tokens * pout
            + cache_write * pin * 1.25 + cache_read * pin * 0.10) / 1_000_000
    budget.charge(cost)
    if cache_write or cache_read:
        log.debug("llm_cache_usage", model=model,
                  cache_write=cache_write, cache_read=cache_read)


def _system_param(system: str | list[dict]):
    """Pass a plain string through untouched. A list of system blocks gets a
    cache_control breakpoint on its LAST block (callers put the stable prefix
    in the list; the tools + system prefix is then cached across calls)."""
    if not system:
        return anthropic.NOT_GIVEN
    if isinstance(system, str):
        return system
    blocks = [dict(b) for b in system]
    if settings.prompt_cache_enabled and blocks:
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return blocks


def ping() -> None:
    """Cheap liveness probe (1 output token). Raises on the real problem —
    invalid key, no credits, or connectivity — so /health can report whether we
    can actually generate, not just whether a key string is present."""
    _get_client().messages.create(
        model=settings.content_model, max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )


def warm_cache(schema: type[BaseModel], system: str | list[dict],
               model: str | None = None) -> None:
    """Write the tools+system prompt-cache entry with a 1-token call, so a
    parallel fan-out that shares this prefix reads the cache instead of every
    worker writing it (N identical in-flight requests all miss). Best-effort:
    a failed warm-up costs nothing but the cache-miss it tried to avoid.
    No tool_choice on purpose — the cache hierarchy (tools -> system) is
    unaffected by it, and a forced tool call can't run at max_tokens=1."""
    if not settings.prompt_cache_enabled or not isinstance(system, list):
        return
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    m = model or settings.content_model
    try:
        runctx.checkpoint()
        budget.assert_ok()
        with _inflight:
            resp = _get_client().messages.create(
                model=m, max_tokens=1,
                system=_system_param(system),
                tools=[tool],
                messages=[{"role": "user", "content": "ok"}],
            )
        _track(m, resp.usage)
        log.info("prompt_cache_warmed",
                 written=getattr(resp.usage, "cache_creation_input_tokens", 0),
                 read=getattr(resp.usage, "cache_read_input_tokens", 0))
    except Exception as exc:  # noqa: BLE001 — warm-up must never block work
        if never_swallow(exc):
            raise
        log.warning("cache_warmup_failed", err=str(exc)[:160])


def _cascade(model: str | None, call):
    """Run `call(model)` down the model chain. Transient errors are retried
    per-model by `_llm_retry` inside `call`; a MODEL-SPECIFIC failure (not-found,
    persistent overload, non-credit 400, or a bad structured response) falls
    through to the next model. A provider-wide failure (credits/auth) is
    re-raised immediately so the job parks instead of pointlessly retrying
    every model."""
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
    prompt: str, *, system: str | list[dict], model: str, max_tokens: int,
    temperature: float | None,
) -> tuple[str, str | None]:
    runctx.checkpoint()
    budget.assert_ok()
    kwargs = {} if temperature is None else {"temperature": temperature}
    with _inflight:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_param(system),
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
    _track(model, resp.usage)
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, resp.stop_reason


def _complete_attempt(
    prompt: str, *, system: str | list[dict], model: str, max_tokens: int,
    temperature: float | None,
) -> str:
    text, stop = _complete_once(
        prompt, system=system, model=model, max_tokens=max_tokens,
        temperature=temperature)
    if stop == "max_tokens" and max_tokens < _MAX_TOKENS_CEILING:
        bumped = min(max(int(max_tokens * 1.5), max_tokens + 1000), _MAX_TOKENS_CEILING)
        log.warning("llm_truncated_retry", model=model,
                    max_tokens=max_tokens, bumped=bumped)
        text, stop = _complete_once(
            prompt, system=system, model=model, max_tokens=bumped,
            temperature=temperature)
        if stop == "max_tokens":
            # Best effort: prose callers tolerate a trimmed tail; log it loudly.
            log.warning("llm_still_truncated", model=model, max_tokens=bumped)
    return text


def complete(
    prompt: str,
    *,
    system: str | list[dict] = "",
    model: str | None = None,
    max_tokens: int = 8000,
    temperature: float | None = None,
) -> str:
    """Plain text completion, with transient retry + truncation bump + model
    fallback cascade. `temperature` is omitted unless explicitly set — newer
    models (e.g. Opus 4.8) reject the deprecated parameter."""
    return _cascade(model, lambda m: _complete_attempt(
        prompt, system=system, model=m, max_tokens=max_tokens, temperature=temperature))


@_llm_retry
def _structured_once(
    prompt: str, schema: type[T], *, system: str | list[dict], model: str,
    max_tokens: int, temperature: float | None,
) -> T:
    runctx.checkpoint()
    budget.assert_ok()
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    kwargs = {} if temperature is None else {"temperature": temperature}
    with _inflight:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_param(system),
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit"},
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
    _track(model, resp.usage)
    if resp.stop_reason == "max_tokens":
        # A truncated tool_use is unusable JSON — flag it for the bump-retry
        # instead of letting it surface as an opaque ValidationError.
        raise _TruncatedOutput(
            f"{schema.__name__} output hit max_tokens={max_tokens}")
    for block in resp.content:
        if block.type == "tool_use" and block.name == "emit":
            return schema.model_validate(block.input)
    raise ValueError(f"Model did not return a valid {schema.__name__}: {resp.content!r}")


def _structured_attempt(
    prompt: str, schema: type[T], *, system: str | list[dict], model: str,
    max_tokens: int, temperature: float | None,
) -> T:
    """One model's attempt: transient retries happen inside `_structured_once`;
    on top of that, a single same-model max_tokens bump for truncation and a
    single same-model retry-with-feedback for invalid output — both cheaper and
    more likely to succeed than re-asking a weaker model the same thing."""
    try:
        return _structured_once(
            prompt, schema, system=system, model=model, max_tokens=max_tokens,
            temperature=temperature)
    except _TruncatedOutput:
        bumped = min(max(int(max_tokens * 1.5), max_tokens + 1000), _MAX_TOKENS_CEILING)
        if bumped <= max_tokens:
            raise ValueError(
                f"{schema.__name__} output truncated at the "
                f"{max_tokens}-token ceiling") from None
        log.warning("llm_truncated_retry", model=model, schema=schema.__name__,
                    max_tokens=max_tokens, bumped=bumped)
        try:
            return _structured_once(
                prompt, schema, system=system, model=model, max_tokens=bumped,
                temperature=temperature)
        except _TruncatedOutput as exc:
            raise ValueError(
                f"{schema.__name__} output truncated even at "
                f"max_tokens={bumped}") from exc
    except (ValidationError, ValueError) as exc:
        log.warning("llm_validation_retry", model=model, schema=schema.__name__,
                    err=str(exc)[:200])
        feedback = (
            f"\n\nYour previous response was rejected: {str(exc)[:600]}\n"
            f"Call the emit tool again with a COMPLETE, valid "
            f"{schema.__name__} object.")
        return _structured_once(
            prompt + feedback, schema, system=system, model=model,
            max_tokens=max_tokens, temperature=temperature)


def structured(
    prompt: str,
    schema: type[T],
    *,
    system: str | list[dict] = "",
    model: str | None = None,
    max_tokens: int = 8000,
    temperature: float | None = None,
) -> T:
    """Force the model to emit JSON validated against `schema`, with transient
    retry + truncation/validation repair + model-fallback cascade."""
    return _cascade(model, lambda m: _structured_attempt(
        prompt, schema, system=system, model=m, max_tokens=max_tokens,
        temperature=temperature))


@_llm_retry
def vision_json(prompt: str, image_bytes: bytes, schema: type[T], *, media_type="image/jpeg") -> T:
    """Vision call (image QA / scoring) returning validated JSON."""
    import base64

    model = settings.vision_qa_model
    runctx.checkpoint()
    budget.assert_ok()
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    with _inflight:
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
