"""Classify provider quota errors and compute when retrying can actually work.

Grounded on the live Imagen 429 payload:
  "Quota exceeded for metric: generativelanguage.googleapis.com/
   predict_requests_per_model_per_day_paid_tier_1, limit: 70, ..."

A DAILY quota resets at midnight in Google's billing timezone (Pacific) — the
old fixed 60..1800s park backoff could never outlive it, so jobs burned all
their park attempts on guaranteed-futile resumes. A PER-MINUTE limit clears in
seconds and deserves a short in-stage retry instead of parking at all.
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bookforge.config import settings

DAILY = "daily"
PER_MINUTE = "per_minute"
UNKNOWN = "unknown"

_RE_DAILY = re.compile(r"per[_\s-]?day", re.IGNORECASE)
_RE_MINUTE = re.compile(r"per[_\s-]?minute", re.IGNORECASE)
# google.rpc.RetryInfo style: "retryDelay": "13s"
_RE_RETRY_DELAY = re.compile(r"retry[_\s]?delay['\"]?\s*[:=]\s*['\"]?(\d+)", re.IGNORECASE)


def next_daily_reset(now: datetime | None = None) -> datetime:
    """Next midnight in QUOTA_RESET_TZ plus 5-20 min of jitter, as aware UTC."""
    tz = ZoneInfo(settings.quota_reset_tz)
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    reset = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                    microsecond=0)
    reset += timedelta(minutes=random.uniform(5, 20))
    return reset.astimezone(timezone.utc)


def parse_quota(text: str) -> tuple[str, datetime | None]:
    """Classify a quota error message -> (quota_class, wait_until).

    daily      -> wait_until = next quota reset (park until then)
    per_minute -> wait_until = now + max(retryDelay, 60s)
    unknown    -> (UNKNOWN, None): caller falls back to the standard backoff
    """
    if _RE_DAILY.search(text):
        return DAILY, next_daily_reset()
    delay_match = _RE_RETRY_DELAY.search(text)
    if _RE_MINUTE.search(text) or delay_match:
        delay_s = max(60, int(delay_match.group(1))) if delay_match else 60
        return PER_MINUTE, datetime.now(timezone.utc) + timedelta(seconds=delay_s)
    return UNKNOWN, None
