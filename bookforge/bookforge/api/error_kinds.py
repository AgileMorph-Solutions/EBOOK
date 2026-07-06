"""Classify pipeline exceptions into user-friendly (kind, message) pairs.

The pipeline wraps LLM calls in `tenacity.retry`, so raw exceptions arrive as
`RetryError[<Future ... raised OriginalError>]`. That string is useless to the
user. This module unwraps the retry, inspects the underlying error, and returns
a short, actionable message plus a machine-readable `kind` the UI can key on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassifiedError:
    kind: str            # "quota_llm" | "quota_images" | "rate_limit" | "auth" | "network" | "unknown"
    message: str         # short user-facing sentence
    detail: str = ""     # trimmed original error text (for the "details" disclosure)


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap tenacity.RetryError, ExceptionGroup, __cause__/__context__ chains."""
    seen: set[int] = set()
    cur: BaseException = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        # tenacity: RetryError.last_attempt is a concurrent.futures.Future
        last_attempt = getattr(cur, "last_attempt", None)
        if last_attempt is not None:
            try:
                inner = last_attempt.exception()
                if inner is not None:
                    cur = inner
                    continue
            except Exception:
                pass
        # ExceptionGroup / async
        exceptions = getattr(cur, "exceptions", None)
        if isinstance(exceptions, (list, tuple)) and exceptions:
            cur = exceptions[0]
            continue
        # PEP 3134 chains
        nxt = cur.__cause__ or cur.__context__
        if nxt is not None and id(nxt) not in seen:
            cur = nxt
            continue
        break
    return cur


def classify(exc: BaseException) -> ClassifiedError:
    root = _root_cause(exc)
    cls_name = type(root).__name__
    module = type(root).__module__ or ""
    msg = str(root)
    lower = msg.lower()
    detail = (msg[:500] + "…") if len(msg) > 500 else msg

    # ---------- Anthropic (LLM content generation) ----------
    if module.startswith("anthropic"):
        if "credit balance is too low" in lower or "billing" in lower:
            return ClassifiedError(
                kind="quota_llm",
                message=(
                    "Anthropic credit balance is too low to generate this book. "
                    "Top up at console.anthropic.com/settings/billing, then retry."
                ),
                detail=detail,
            )
        if cls_name == "RateLimitError" or "rate limit" in lower or " 429" in msg:
            return ClassifiedError(
                kind="rate_limit",
                message="Anthropic rate limit reached. Wait a moment and retry.",
                detail=detail,
            )
        if cls_name in ("AuthenticationError", "PermissionDeniedError") or "invalid_api_key" in lower:
            return ClassifiedError(
                kind="auth",
                message="Anthropic API key is invalid or unauthorized. Check ANTHROPIC_API_KEY.",
                detail=detail,
            )

    # ---------- OpenAI (image gen / content fallback) ----------
    if module.startswith("openai"):
        if cls_name == "RateLimitError" or "quota" in lower or "insufficient_quota" in lower:
            return ClassifiedError(
                kind="quota_llm",
                message=(
                    "OpenAI quota exhausted. Check your plan & billing at "
                    "platform.openai.com/account/billing, then retry."
                ),
                detail=detail,
            )
        if cls_name in ("AuthenticationError", "PermissionDeniedError"):
            return ClassifiedError(
                kind="auth",
                message="OpenAI API key is invalid or unauthorized. Check OPENAI_API_KEY.",
                detail=detail,
            )

    # ---------- Google / Gemini (Imagen) ----------
    if "google" in module or "genai" in module or "google-generativeai" in module:
        if "resource_exhausted" in lower or " 429" in msg or "quota" in lower:
            return ClassifiedError(
                kind="quota_images",
                message=(
                    "Gemini/Imagen quota exhausted. Wait for daily reset, upgrade the plan, "
                    "or enable IMAGE_ALLOW_PROVIDER_FALLBACK with an OPENAI_API_KEY."
                ),
                detail=detail,
            )

    # ---------- Generic message-based fallbacks (works even when the SDK wraps differently) ----------
    if "credit balance is too low" in lower or "insufficient_quota" in lower:
        return ClassifiedError(
            kind="quota_llm",
            message="LLM provider credit balance is too low. Top up billing and retry.",
            detail=detail,
        )
    if "resource_exhausted" in lower or "quota" in lower and "exceed" in lower:
        return ClassifiedError(
            kind="quota_images",
            message="Image generation quota exhausted. Wait for reset or upgrade the plan.",
            detail=detail,
        )
    if " 429" in msg or "rate limit" in lower:
        return ClassifiedError(
            kind="rate_limit",
            message="Upstream provider rate limit hit. Wait a moment and retry.",
            detail=detail,
        )
    if "connection" in lower and ("refused" in lower or "reset" in lower or "timeout" in lower):
        return ClassifiedError(
            kind="network",
            message="Network error reaching an upstream service. Check connectivity and retry.",
            detail=detail,
        )

    # ---------- Legacy-string pattern-match fallback ----------
    # Older status.json rows only saved the RetryError wrapper (real cause lost).
    # Give the user a useful hint tied to the wrapped exception class name.
    if "retryerror" in lower:
        if "badrequesterror" in lower or "invalidrequesterror" in lower:
            return ClassifiedError(
                kind="quota_llm",
                message=(
                    "Pipeline gave up after retrying the LLM. The upstream 400 errors are "
                    "almost always caused by exhausted Anthropic credits. "
                    "Check console.anthropic.com/settings/billing and retry."
                ),
                detail=detail,
            )
        if "ratelimiterror" in lower:
            return ClassifiedError(
                kind="rate_limit",
                message="Pipeline gave up after repeated rate-limit responses. Wait a bit and retry.",
                detail=detail,
            )
        return ClassifiedError(
            kind="unknown",
            message="Pipeline gave up after retrying an upstream call. Check provider quotas/keys and retry.",
            detail=detail,
        )

    # ---------- Unknown ----------
    return ClassifiedError(
        kind="unknown",
        message=msg if msg else f"{cls_name}: pipeline failed",
        detail=detail,
    )
