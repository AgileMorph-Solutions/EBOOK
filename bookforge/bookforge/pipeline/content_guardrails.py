"""Content coherence guardrails for practical guides.

Keeps marketing counts in titles/subtitles aligned with generated prose and
prevents inflated tip totals in section intros and headings.
"""
from __future__ import annotations

import re

from bookforge.schemas import BookBible, Chapter, SectionPlan

_COUNT_RE = re.compile(
    r"\b(\d{2,4})\b(?:\s+\w+){0,4}\s*(?:\+?\s*)?(?:ways?|tips?|uses?|tricks?|methods?|ideas?|hacks?)\b",
    re.IGNORECASE,
)
_INFLATED_RE = re.compile(
    r"\b(?:over|more than|nearly|almost|about|around|up to)\s+(\d{2,4})\b",
    re.IGNORECASE,
)
_BARE_TOTAL_RE = re.compile(
    r"\b(\d{2,4})\s+(?:numbered\s+)?(?:tips?|ways?|uses?)\b",
    re.IGNORECASE,
)


def parse_promised_count(title: str, subtitle: str) -> int | None:
    """Extract a marketing tip count from title or subtitle (e.g. '250 ways')."""
    for text in (subtitle, title):
        m = _COUNT_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def planned_use_count(sections: list[SectionPlan]) -> int:
    """Sum of full cards + quick uses the book will actually generate."""
    return sum(s.full_card_quota + s.list_quota for s in sections)


def _promised(bible: BookBible) -> int:
    if bible.promised_uses is not None:
        return bible.promised_uses
    return parse_promised_count(bible.title, bible.subtitle) or planned_use_count(bible.sections)


def _planned(bible: BookBible) -> int:
    return planned_use_count(bible.sections)


def coherence_rules(bible: BookBible) -> str:
    """Prompt block injected into practical-guide writers."""
    promised = _promised(bible)
    planned = _planned(bible)
    return (
        f'BOOK COHERENCE: This book is titled "{bible.title}" with subtitle '
        f'"{bible.subtitle}". It will contain exactly {planned} numbered tips. '
        f"The marketed count is {promised}. "
        f"Never claim a different total in prose (e.g. do not say '700 ways', "
        f"'800 tips', or 'over 500 uses'). "
        f"Section headings must match their topic. "
        f"Use correct spelling for household product names and chemical names "
        f"(e.g. hydrogen peroxide, baking soda). No invented words."
    )


def _collect_text(chapters: list[Chapter]) -> list[tuple[str, str]]:
    """Return (location, text) pairs from chapter blocks."""
    out: list[tuple[str, str]] = []
    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            loc = f"ch{ch.index}.{bi}({block.type.value})"
            if block.text:
                out.append((loc, block.text))
            if block.heading:
                out.append((loc, block.heading))
            for entry in block.entries or []:
                for attr in ("title", "intro", "savings"):
                    t = getattr(entry, attr, "") or ""
                    if t:
                        out.append((loc, t))
    return out


def _is_drift(count: int, promised: int, planned: int) -> bool:
    """True if count is wildly off the promised or planned totals."""
    if count == promised:
        return False
    anchor = planned
    if abs(planned - promised) / max(promised, 1) <= 0.25:
        anchor = promised
    if count == anchor:
        return False
    if count > anchor * 1.15:
        return True
    if count < anchor * 0.5 and count >= 20:
        return True
    return False


def scan_chapters_for_count_drift(
    chapters: list[Chapter], bible: BookBible,
) -> list[str]:
    """Post-write warnings when prose mentions inflated tip totals."""
    promised = _promised(bible)
    planned = _planned(bible)
    issues: list[str] = []

    for loc, text in _collect_text(chapters):
        for pat in (_COUNT_RE, _INFLATED_RE, _BARE_TOTAL_RE):
            for m in pat.finditer(text):
                count = int(m.group(1))
                if _is_drift(count, promised, planned):
                    issues.append(
                        f"{loc}: mentions {count} tips/ways but book promises "
                        f"{promised} (planned {planned}): {text[:80]!r}"
                    )
    return issues
