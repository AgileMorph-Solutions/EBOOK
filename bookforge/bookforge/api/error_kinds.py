"""Classify pipeline exceptions into user-friendly (kind, message) pairs.

Inspects the root cause (walking ExceptionGroup and __cause__/__context__
chains) and returns a short, actionable message plus a machine-readable `kind`
the UI can key on. All tenacity retries in the codebase use `reraise=True`, so
raw provider exceptions arrive directly — never wrapped in RetryError.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassifiedError:
    kind: str            # "quota_llm" | "quota_images" | "rate_limit" | "auth" | "network" | "budget" | "source_unavailable" | "unknown"
    message: str         # short user-facing sentence
    detail: str = ""     # trimmed original error text (for the "details" disclosure)


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap ExceptionGroup and __cause__/__context__ chains."""
    seen: set[int] = set()
    cur: BaseException = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
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
    # Domain exceptions carry intent directly — classify them from the TOP-level
    # exception before root-cause walking, so the chained original error (e.g.
    # the google 429 behind an ImageQuotaError) can't shadow them.
    top_name = type(exc).__name__
    top_msg = str(exc)
    top_detail = (top_msg[:500] + "…") if len(top_msg) > 500 else top_msg
    if top_name == "PageLimitError":
        pages = getattr(exc, "pages", "?")
        lo = getattr(exc, "lo", "?")
        hi = getattr(exc, "hi", "?")
        target = getattr(exc, "target", "?")
        return ClassifiedError(
            kind="page_limit",
            message=(
                f"This book came out to {pages} pages but needed {lo}–{hi} "
                f"(target {target}). Planning or content did not produce "
                f"enough material for the page goal."
            ),
            detail=top_detail,
        )
    if top_name == "EmptyBookError":
        return ClassifiedError(
            kind="empty_book",
            message=(
                "No chapters were generated, so there is nothing to ship. "
                "This is often caused by an oversized single section or a "
                "temporary writing-service outage."
            ),
            detail=top_detail,
        )
    if top_name == "PlanInfeasibleError":
        return ClassifiedError(
            kind="request",
            message=top_msg or (
                "This book plan can't honor the promised item count at the "
                "selected page target. Raise pages or accept a lower count."
            ),
            detail=top_detail,
        )
    if top_name == "BudgetExceeded":
        return ClassifiedError(
            kind="budget",
            message="This book reached its generation cost limit before finishing. "
                    "Try a shorter book or contact support to raise the limit.",
            detail=top_detail,
        )
    if top_name == "IngestError":
        return ClassifiedError(
            kind="source_unavailable",
            message="We couldn't read your source material. Please check the "
                    "link or paste the text directly, then try again.",
            detail=top_detail,
        )
    if top_name == "JobDeadlineError":
        return ClassifiedError(
            kind="timeout",
            message="Your book is taking longer than expected — it's been "
                    "paused with progress saved and will continue automatically.",
            detail=top_detail,
        )

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
            message="Your book has used up today's image allowance. Try again tomorrow and it'll pick up where it left off.",
            detail=detail,
        )

    # ---------- Fatal image generation failure (billing, auth, all models exhausted) ----------
    if cls_name == "ImageGenerationError":
        if "paid plans" in lower or "invalid_argument" in lower:
            return ClassifiedError(
                kind="auth",
                message="Image creation isn't available on the current plan. Please contact support and we'll get it sorted.",
                detail=detail,
            )
        return ClassifiedError(
            kind="quota_images",
            message="BookForge couldn't create images for your book right now. Please try again — if it keeps failing, contact support.",
            detail=detail,
        )

    # ---------- Anthropic (LLM / text generation) ----------
    if module.startswith("anthropic"):
        if "credit balance is too low" in lower or "billing" in lower:
            return ClassifiedError(
                kind="quota_llm",
                message="BookForge has temporarily run out of writing credits. Please try again in a little while.",
                detail=detail,
            )
        if cls_name == "RateLimitError" or "rate limit" in lower or " 429" in msg:
            return ClassifiedError(
                kind="rate_limit",
                message="The writing service is a bit busy right now. Wait a minute, then click Retry.",
                detail=detail,
            )
        if cls_name in ("AuthenticationError", "PermissionDeniedError") or "invalid_api_key" in lower:
            return ClassifiedError(
                kind="auth",
                message="BookForge couldn't connect to the writing service. Please contact support and we'll fix it quickly.",
                detail=detail,
            )

    # ---------- OpenAI (image generation fallback) ----------
    if module.startswith("openai"):
        if cls_name == "RateLimitError" or "quota" in lower or "insufficient_quota" in lower:
            return ClassifiedError(
                kind="quota_images",
                message="Your book has used up today's image allowance. Try again tomorrow and it'll pick up where it left off.",
                detail=detail,
            )
        if cls_name in ("AuthenticationError", "PermissionDeniedError"):
            return ClassifiedError(
                kind="auth",
                message="BookForge couldn't connect to the image service. Please contact support and we'll fix it quickly.",
                detail=detail,
            )

    # ---------- Google / Gemini (Imagen) ----------
    if "google" in module or "genai" in module or "google-generativeai" in module:
        if "resource_exhausted" in lower or " 429" in msg or "quota" in lower:
            return ClassifiedError(
                kind="quota_images",
                message="Your book has used up today's image allowance. Try again tomorrow and it'll pick up where it left off.",
                detail=detail,
            )

    # ---------- Required image slots not filled ----------
    if cls_name == "ImageCoverageError":
        return ClassifiedError(
            kind="quota_images",
            message="BookForge couldn't fill every photo slot for your book. "
                    "It will retry automatically — if it keeps failing, try again later "
                    "or contact support.",
            detail=detail,
        )

    # ---------- Generic message-based fallbacks ----------
    if "credit balance is too low" in lower or "insufficient_quota" in lower:
        return ClassifiedError(
            kind="quota_llm",
            message="BookForge has temporarily run out of writing credits. Please try again in a little while.",
            detail=detail,
        )
    if "resource_exhausted" in lower or ("quota" in lower and "exceed" in lower):
        return ClassifiedError(
            kind="quota_images",
            message="Your book has used up today's image allowance. Try again tomorrow and it'll pick up where it left off.",
            detail=detail,
        )
    if " 429" in msg or "rate limit" in lower:
        return ClassifiedError(
            kind="rate_limit",
            message="The service is a bit busy right now. Wait a minute, then click Retry.",
            detail=detail,
        )
    if "connection" in lower and ("refused" in lower or "reset" in lower or "timeout" in lower):
        return ClassifiedError(
            kind="network",
            message="BookForge lost its connection mid-way. Check your internet, then try again.",
            detail=detail,
        )

    # ---------- Unknown ----------
    return ClassifiedError(
        kind="unknown",
        message="Something unexpected happened. Please try again — if it keeps failing, contact support.",
        detail=detail,
    )
