"""Pre-layout content trimmer — enforce hard char caps on guide card fields.

Runs after content generation so long LLM output cannot overflow the fixed
senior card templates. Deterministic truncate first; no extra LLM call.
"""
from __future__ import annotations

import structlog

from bookforge.schemas import Chapter, UseEntry

log = structlog.get_logger(__name__)

_CAPS = {
    "title": 40,
    "intro": 200,
    "step": 120,
    "savings": 80,
    "caution": 80,
    "list_intro": 100,
    "need_item": 40,
}


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def _trim_entry(entry: UseEntry) -> int:
    n = 0
    orig = (
        entry.title, entry.intro,
        entry.savings or "", entry.caution or "",
    )
    entry.title = _truncate(entry.title, _CAPS["title"])
    entry.intro = _truncate(entry.intro, _CAPS["intro"])
    if entry.savings:
        entry.savings = _truncate(entry.savings, _CAPS["savings"])
    if entry.caution:
        entry.caution = _truncate(entry.caution, _CAPS["caution"])
    entry.what_you_need = [
        _truncate(x, _CAPS["need_item"]) for x in entry.what_you_need
    ]
    for step in entry.steps:
        step.text = _truncate(step.text, _CAPS["step"])
    if not entry.steps and entry.intro:
        entry.intro = _truncate(entry.intro, _CAPS["list_intro"])
    new = (
        entry.title, entry.intro,
        entry.savings or "", entry.caution or "",
    )
    return sum(1 for a, b in zip(orig, new) if a != b)


def trim_chapter(chapter: Chapter) -> Chapter:
    """Trim all UseEntry fields in a section chapter to layout char caps."""
    trimmed = 0
    for block in chapter.blocks:
        for entry in block.entries:
            trimmed += _trim_entry(entry)
    if trimmed:
        log.debug("layout_budget_trimmed", chapter=chapter.index, fields=trimmed)
    return chapter


def trim_chapters(chapters: list[Chapter]) -> list[Chapter]:
    return [trim_chapter(ch) for ch in chapters]
