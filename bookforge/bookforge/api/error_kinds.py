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

    # ---------- Image provider quota (fast-path — ImageQuotaError is explicit) ----------
    if cls_name == "ImageQuotaError":
        return ClassifiedError(
            kind="quota_images",
            message="We've hit today's image generation limit. Please try again tomorrow.",
            detail=detail,
        )

    # ---------- Anthropic (LLM / text generation) ----------
    if module.startswith("anthropic"):
        if "credit balance is too low" in lower or "billing" in lower:
            return ClassifiedError(
                kind="quota_llm",
                message="We've run out of AI writing credits. Please try again later.",
                detail=detail,
            )
        if cls_name == "RateLimitError" or "rate limit" in lower or " 429" in msg:
            return ClassifiedError(
                kind="rate_limit",
                message="The AI writing service is busy right now. Please wait a minute and try again.",
                detail=detail,
            )
        if cls_name in ("AuthenticationError", "PermissionDeniedError") or "invalid_api_key" in lower:
            return ClassifiedError(
                kind="auth",
                message="The AI service couldn't be reached due to a configuration issue. Please contact support.",
                detail=detail,
            )

    # ---------- OpenAI (image generation fallback) ----------
    if module.startswith("openai"):
        if cls_name == "RateLimitError" or "quota" in lower or "insufficient_quota" in lower:
            return ClassifiedError(
                kind="quota_images",
                message="We've hit today's image generation limit. Please try again tomorrow.",
                detail=detail,
            )
        if cls_name in ("AuthenticationError", "PermissionDeniedError"):
            return ClassifiedError(
                kind="auth",
                message="The image service couldn't be reached due to a configuration issue. Please contact support.",
                detail=detail,
            )

    # ---------- Google / Gemini (Imagen) ----------
    if "google" in module or "genai" in module or "google-generativeai" in module:
        if "resource_exhausted" in lower or " 429" in msg or "quota" in lower:
            return ClassifiedError(
                kind="quota_images",
                message="We've hit today's image generation limit. Please try again tomorrow.",
                detail=detail,
            )

    # ---------- Generic message-based fallbacks ----------
    if "credit balance is too low" in lower or "insufficient_quota" in lower:
        return ClassifiedError(
            kind="quota_llm",
            message="We've run out of AI writing credits. Please try again later.",
            detail=detail,
        )
    if "resource_exhausted" in lower or ("quota" in lower and "exceed" in lower):
        return ClassifiedError(
            kind="quota_images",
            message="We've hit today's image generation limit. Please try again tomorrow.",
            detail=detail,
        )
    if " 429" in msg or "rate limit" in lower:
        return ClassifiedError(
            kind="rate_limit",
            message="The AI service is busy right now. Please wait a minute and try again.",
            detail=detail,
        )
    if "connection" in lower and ("refused" in lower or "reset" in lower or "timeout" in lower):
        return ClassifiedError(
            kind="network",
            message="Couldn't reach the AI service. Please check your connection and try again.",
            detail=detail,
        )

    # ---------- Legacy-string pattern-match fallback ----------
    if "retryerror" in lower:
        if "badrequesterror" in lower or "invalidrequesterror" in lower:
            return ClassifiedError(
                kind="quota_llm",
                message="We've run out of AI writing credits. Please try again later.",
                detail=detail,
            )
        if "ratelimiterror" in lower:
            return ClassifiedError(
                kind="rate_limit",
                message="The AI service is busy right now. Please wait a minute and try again.",
                detail=detail,
            )
        return ClassifiedError(
            kind="unknown",
            message="Something went wrong during generation. Please try again.",
            detail=detail,
        )

    # ---------- Unknown ----------
    return ClassifiedError(
        kind="unknown",
        message="Something went wrong. Please try again.",
        detail=detail,
    )
