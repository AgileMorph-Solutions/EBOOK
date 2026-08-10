"""Plan scope detection — user-provided structure that must not be silently dropped."""
from __future__ import annotations

import re

from pydantic import BaseModel

from bookforge.pipeline.content_guardrails import count_toc_sections, parse_promised_count

_BULLET_RE = re.compile(r"^(\-|\*|•|\d+\.)\s+\S")
_CHAPTER_HDR_RE = re.compile(r"^chapter\s+\d+\s*:", re.IGNORECASE)


class PlanScope(BaseModel):
    min_sections: int = 0
    min_entries: int = 0
    locked_toc: bool = False
    source_entry_floor: int | None = None
    detected_sections: int = 0
    detected_entries: int | None = None


class PlanScopeSummary(BaseModel):
    sections: int = 0
    entries: int | None = None
    locked_toc: bool = False


def _count_source_bullets(text: str) -> int:
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _BULLET_RE.match(s):
            count += 1
    return count


def _count_source_chapters(text: str) -> int:
    return sum(1 for line in text.splitlines() if _CHAPTER_HDR_RE.match(line.strip()))


def build_plan_scope(
    *,
    topic: str,
    description: str | None = None,
    text: str | None = None,
    toc: list[str] | None = None,
    accepted_promised_count: int | None = None,
) -> PlanScope:
    """Derive structural locks from the user's request (all book kinds)."""
    promised = (
        accepted_promised_count
        or parse_promised_count(topic, description or "")
        or parse_promised_count(topic, "")
    )
    body = (text or "").strip()
    if not promised and body:
        promised = parse_promised_count("", body)
    bullets = _count_source_bullets(body) if body else 0
    src_chapters = _count_source_chapters(body) if body else 0
    toc_len = count_toc_sections(toc)

    min_sections = toc_len
    locked_toc = toc_len > 0
    if not min_sections and src_chapters > 0:
        min_sections = src_chapters

    source_floor: int | None = None
    if bullets >= 3:
        source_floor = bullets
    elif toc_len > 0:
        source_floor = max(toc_len * 3, bullets)

    min_entries = promised or 0
    if accepted_promised_count and accepted_promised_count > 0:
        min_entries = accepted_promised_count
    elif promised and promised > 0:
        min_entries = promised
    elif source_floor and source_floor > min_entries:
        min_entries = source_floor

    detected_entries = promised or (source_floor if source_floor else None)

    return PlanScope(
        min_sections=min_sections,
        min_entries=min_entries,
        locked_toc=locked_toc,
        source_entry_floor=source_floor,
        detected_sections=min_sections or src_chapters,
        detected_entries=detected_entries,
    )


def scope_summary(scope: PlanScope) -> PlanScopeSummary:
    return PlanScopeSummary(
        sections=scope.min_sections or scope.detected_sections,
        entries=scope.detected_entries or (
            scope.min_entries if scope.min_entries > 0 else None
        ),
        locked_toc=scope.locked_toc,
    )
