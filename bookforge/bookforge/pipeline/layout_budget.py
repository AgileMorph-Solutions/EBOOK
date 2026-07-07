"""Pre-layout content trimmer — enforce hard char caps on guide card fields.

Runs after content generation so long LLM output cannot overflow the fixed
card templates. Caps are profile-aware: entry-rich genres (quantified need
lists, long headnotes) get room instead of ellipses — a truncated quantity is
corrupted data, so the QA gate treats any remaining "…" in a required field as
a defect rather than letting it ship silently.
"""
from __future__ import annotations

import structlog

from bookforge.schemas import BookBible, Chapter, UseEntry, profile_of

log = structlog.get_logger(__name__)

# Legacy caps (tips_cards) — unchanged so old books re-trim identically.
_CAPS = {
    "title": 40,
    "intro": 200,
    "step": 120,
    "savings": 80,
    "caution": 80,
    "list_intro": 100,
    "need_item": 40,
    "meta": 40,
    "note": 140,
}


def _caps_for(bible: BookBible | None) -> dict[str, int]:
    if bible is None:
        return _CAPS
    es = profile_of(bible).entry_schema
    caps = dict(_CAPS)
    caps["title"] = max(caps["title"], 48)
    # Headnotes: size to the profile's word range (~8 chars/word + slack).
    caps["intro"] = max(caps["intro"], es.headnote_hi * 8 + 40)
    if es.require_quantities:
        # "1 (15-ounce) can condensed cream of mushroom soup" ≈ 50 chars —
        # quantified items must never be cut mid-amount.
        caps["need_item"] = 90
    if es.max_steps > 5:
        caps["step"] = 170
    # Safety cautions are NEVER truncated (see _trim_entry) — a cut caution is a
    # real safety risk, so no char cap is applied to it at all.
    caps["savings"] = 100
    return caps


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    # Prefer a clean cut at a sentence boundary — a shorter complete headnote
    # is a legitimate edit; a mid-word "…" is corrupted content the QA gate
    # rightly refuses to ship.
    for stop in (". ", "! ", "? "):
        pos = cut.rfind(stop)
        if pos >= max_len // 3:
            return cut[: pos + 1].strip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def _trim_entry(entry: UseEntry, caps: dict[str, int]) -> int:
    n = 0
    orig = (
        entry.title, entry.intro,
        entry.savings or "", entry.caution or "",
    )
    entry.title = _truncate(entry.title, caps["title"])
    entry.intro = _truncate(entry.intro, caps["intro"])
    if entry.savings:
        entry.savings = _truncate(entry.savings, caps["savings"])
    if entry.caution:
        # NEVER truncate a safety caution: a mid-sentence cut (or an appended
        # "…") drops the actual warning. The layout fit engine shrinks type to
        # fit instead. Only normalise whitespace here.
        entry.caution = entry.caution.strip()
    entry.what_you_need = [
        _truncate(x, caps["need_item"]) for x in entry.what_you_need
    ]
    for step in entry.steps:
        step.text = _truncate(step.text, caps["step"])
    entry.serves = _truncate(entry.serves, caps["meta"])
    entry.time = _truncate(entry.time, caps["meta"])
    entry.variation = _truncate(entry.variation, caps["note"])
    entry.note = _truncate(entry.note, caps["note"])
    entry.source_line = _truncate(entry.source_line, caps["note"])
    if not entry.steps and entry.intro:
        entry.intro = _truncate(entry.intro, caps["list_intro"])
    new = (
        entry.title, entry.intro,
        entry.savings or "", entry.caution or "",
    )
    return sum(1 for a, b in zip(orig, new) if a != b)


def trim_how_to(how_to: dict | None) -> dict | None:
    """Cap welcome-page bullets so 4 feature cards always fit one page.

    The bible prompt asks for 10-18-word bullets, but old manifests and drifted
    LLM output can exceed that — the howto page has no unbounded layout, so the
    bullets get the same sentence-boundary trim as card fields (~130 chars ≈ 20
    words), and the lead gets a looser one."""
    if not how_to:
        return how_to
    pts = how_to.get("points")
    if isinstance(pts, list):
        how_to["points"] = [_truncate(str(p), 130) for p in pts]
    if how_to.get("lead"):
        how_to["lead"] = _truncate(str(how_to["lead"]), 300)
    return how_to


def trim_chapter(chapter: Chapter, bible: BookBible | None = None) -> Chapter:
    """Trim all UseEntry fields in a section chapter to layout char caps."""
    caps = _caps_for(bible)
    trimmed = 0
    for block in chapter.blocks:
        for entry in block.entries:
            trimmed += _trim_entry(entry, caps)
    if trimmed:
        log.debug("layout_budget_trimmed", chapter=chapter.index, fields=trimmed)
    return chapter


def trim_chapters(chapters: list[Chapter], bible: BookBible | None = None) -> list[Chapter]:
    return [trim_chapter(ch, bible) for ch in chapters]
