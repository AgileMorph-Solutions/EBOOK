"""Pre-queue readiness gate — verify services before spending generation credits."""
from __future__ import annotations

import time
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from bookforge.api.error_kinds import ClassifiedError, classify
from bookforge.clients import images as img_client
from bookforge.clients import llm
from bookforge.config import get_settings
from bookforge.pipeline import pdf_geometry

log = structlog.get_logger(__name__)

ReasonCode = Literal[
    "ok", "quota_llm", "quota_images", "auth", "network", "rate_limit",
    "layout", "capacity", "request",
]

_HARD_BLOCK_CODES: frozenset[str] = frozenset({
    "quota_llm", "quota_images", "auth", "network", "rate_limit", "layout", "request",
})

_USER_COPY: dict[str, tuple[str, str]] = {
    "quota_llm": (
        "Can't start your book right now",
        "Writing credits are low. Please add credits, then try again.",
    ),
    "quota_images": (
        "Can't start your book right now",
        "Today's image limit is used up. Try again tomorrow.",
    ),
    "auth": (
        "Can't start your book right now",
        "Book generation isn't set up correctly. Please contact support.",
    ),
    "rate_limit": (
        "Can't start your book right now",
        "The writing service is busy. Wait a minute and try again.",
    ),
    "network": (
        "Can't start your book right now",
        "We can't reach the book service right now. Try again shortly.",
    ),
    "layout": (
        "Can't start your book right now",
        "Our book layout system isn't ready. Please contact support.",
    ),
    "capacity": (
        "Another book is generating",
        "Another book is already being generated. Yours will start when a slot opens.",
    ),
    "request": (
        "This book needs different settings",
        "This book request doesn't fit the selected settings. Adjust pages, book type, or content.",
    ),
    "ok": ("Ready to generate", "All services are ready."),
}


class CheckResult(BaseModel):
    ok: bool
    reason_code: ReasonCode | None = None
    detail: str = ""


class ReadinessRequest(BaseModel):
    """Subset of CreateBook fields used for request-sanity checks."""

    topic: str
    text: str | None = None
    youtube_url: str | None = None
    target_pages: int = Field(100, gt=0, le=300)
    book_kind: Literal["practical_guide"] = "practical_guide"
    description: str | None = None
    toc: list[str] | None = None
    section_limit: int | None = None


class ReadinessResult(BaseModel):
    can_generate: bool
    title: str
    message: str
    reason_code: ReasonCode | None = None
    checks: dict[str, CheckResult] = Field(default_factory=dict)


_service_cache: dict[str, Any] = {
    "deadline": 0.0,
    "checks": None,
}


def _user_message(reason_code: str) -> tuple[str, str]:
    return _USER_COPY.get(reason_code, _USER_COPY["network"])


def _classified_to_reason(classified: ClassifiedError) -> ReasonCode:
    kind = classified.kind
    if kind in _HARD_BLOCK_CODES or kind == "capacity":
        return kind  # type: ignore[return-value]
    if kind == "unknown":
        return "network"
    return kind  # type: ignore[return-value]


def _check_writing() -> CheckResult:
    s = get_settings()
    if not s.anthropic_api_key:
        return CheckResult(
            ok=False, reason_code="auth",
            detail="ANTHROPIC_API_KEY not configured",
        )
    try:
        llm.ping()
        return CheckResult(ok=True, reason_code="ok", detail="ok")
    except Exception as exc:
        if llm.is_unfunded(exc):
            return CheckResult(
                ok=False, reason_code="quota_llm",
                detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        classified = classify(exc)
        reason = _classified_to_reason(classified)
        return CheckResult(ok=False, reason_code=reason, detail=classified.detail)


def _check_images() -> CheckResult:
    try:
        img_client.ping()
        return CheckResult(ok=True, reason_code="ok", detail="ok")
    except img_client.ImageQuotaError as exc:
        return CheckResult(
            ok=False, reason_code="quota_images",
            detail=str(exc)[:200],
        )
    except img_client.ImageGenerationError as exc:
        detail = str(exc)[:200]
        lower = detail.lower()
        if "paid" in lower or "403" in detail or "permission" in lower:
            reason: ReasonCode = "auth"
        else:
            reason = "quota_images"
        return CheckResult(ok=False, reason_code=reason, detail=detail)
    except RuntimeError as exc:
        msg = str(exc)
        if "not set" in msg.lower():
            return CheckResult(ok=False, reason_code="auth", detail=msg[:200])
        return CheckResult(ok=False, reason_code="network", detail=msg[:200])
    except Exception as exc:
        classified = classify(exc)
        reason = _classified_to_reason(classified)
        return CheckResult(ok=False, reason_code=reason, detail=classified.detail)


def _check_layout() -> CheckResult:
    issues = pdf_geometry.preflight_render_check()
    if issues:
        return CheckResult(
            ok=False, reason_code="layout",
            detail="; ".join(issues[:3]),
        )
    return CheckResult(ok=True, reason_code="ok", detail="ok")


def _check_capacity(active_jobs: int) -> CheckResult:
    s = get_settings()
    if active_jobs >= s.max_concurrent_books:
        return CheckResult(
            ok=False, reason_code="capacity",
            detail=f"active={active_jobs} max={s.max_concurrent_books}",
        )
    return CheckResult(ok=True, reason_code="ok", detail="ok")


def _check_request(req: ReadinessRequest) -> CheckResult:
    """Light request sanity — provider checks are the real gate.

    Title marketing counts (e.g. "365 recipes") are NOT validated here; the
    bible stage sizes the book and reconcile_promise() rewrites mismatched counts.
    """
    text_len = len((req.text or "").strip())

    if req.youtube_url:
        pass  # skip short-source check for YouTube
    elif text_len < 200:
        return CheckResult(
            ok=False, reason_code="request",
            detail=f"source text too short ({text_len} chars)",
        )

    return CheckResult(ok=True, reason_code="ok", detail="ok")


def _request_block_message(_req: ReadinessRequest, check: CheckResult) -> str:
    if check.detail.startswith("source text"):
        return (
            "Your source text is too short to generate a grounded book. "
            "Paste more content into the source notes field."
        )
    return _USER_COPY["request"][1]


def _run_service_checks(active_jobs: int) -> dict[str, CheckResult]:
    return {
        "writing": _check_writing(),
        "images": _check_images(),
        "layout": _check_layout(),
        "capacity": _check_capacity(active_jobs),
    }


def _first_hard_failure(
    checks: dict[str, CheckResult],
    *,
    include_request: ReadinessRequest | None = None,
) -> tuple[ReasonCode | None, str | None]:
    for name in ("writing", "images", "layout", "request"):
        c = checks.get(name)
        if c and not c.ok and c.reason_code in _HARD_BLOCK_CODES:
            if name == "request" and include_request is not None:
                return c.reason_code, _request_block_message(include_request, c)
            return c.reason_code, None
    return None, None


def clear_service_cache() -> None:
    """Drop cached provider pings so the next check hits live APIs."""
    _service_cache["deadline"] = 0.0
    _service_cache["checks"] = None


def require_ready(
    *,
    include_request: ReadinessRequest | None = None,
    active_jobs: int = 0,
    fresh: bool = True,
) -> ReadinessResult:
    """Verify services are live; raise ReadinessError when not.

    `fresh=True` bypasses the TTL cache — use before expensive stages (images)
    so a quota hit mid-run is caught before spending the writing budget again.
    """
    from bookforge.errors import ReadinessError

    if fresh:
        clear_service_cache()
    result = check_readiness(
        include_request=include_request, active_jobs=active_jobs,
    )
    if not result.can_generate:
        raise ReadinessError(result.title, result.message, result.reason_code)
    return result


def check_readiness(
    *,
    include_request: ReadinessRequest | None = None,
    active_jobs: int = 0,
) -> ReadinessResult:
    """Verify services (and optionally the book request) before queueing a job."""
    s = get_settings()
    now = time.monotonic()
    use_cache = include_request is None

    if use_cache and now < _service_cache["deadline"] and _service_cache["checks"]:
        checks = dict(_service_cache["checks"])
    else:
        checks = _run_service_checks(active_jobs)
        if use_cache:
            _service_cache["deadline"] = now + max(5, s.health_ping_ttl_s)
            _service_cache["checks"] = dict(checks)

    if include_request is not None:
        checks["request"] = _check_request(include_request)

    hard_code, custom_msg = _first_hard_failure(
        checks, include_request=include_request,
    )
    can_generate = hard_code is None

    if can_generate:
        title, message = _USER_COPY["ok"]
        reason_code: ReasonCode | None = "ok"
    else:
        assert hard_code is not None
        title, default_msg = _user_message(hard_code)
        message = custom_msg or default_msg
        reason_code = hard_code

    result = ReadinessResult(
        can_generate=can_generate,
        title=title,
        message=message,
        reason_code=reason_code if can_generate else hard_code,
        checks=checks,
    )
    log.info(
        "readiness_check",
        can_generate=can_generate,
        reason_code=result.reason_code,
        checks={k: v.ok for k, v in checks.items()},
    )
    return result


def writing_provider_status() -> tuple[bool | None, str, bool]:
    """Anthropic liveness for /health — (ok, detail, unfunded)."""
    s = get_settings()
    if not s.anthropic_api_key:
        return None, "no ANTHROPIC_API_KEY", False
    check = _check_writing()
    unfunded = check.reason_code == "quota_llm"
    if check.ok:
        return True, "ok", False
    return False, check.detail[:120], unfunded
