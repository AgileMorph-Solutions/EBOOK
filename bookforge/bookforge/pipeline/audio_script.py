"""Audio narration script generation (brainstorm → refine → parallel write).

Produces retention-focused conversational scripts: N items at a target word
band each, formatted as a transcript for ElevenLabs narration.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
import structlog
from pydantic import BaseModel, Field, field_validator

from bookforge.clients import llm
from bookforge.config import settings
from bookforge.pipeline.content_guardrails import parse_promised_count
from bookforge.pipeline.pools import ctx_pool
from bookforge.pipeline import audio_script_prompts as prompts
from bookforge.pipeline.audio_script_stages import AudioScriptStage
from bookforge.schemas import AudioScriptItem, AudioScriptResult

log = structlog.get_logger(__name__)

# SOP default band when API omits word_count
_SOP_WORD_MIN = 265
_SOP_WORD_MAX = 270
_SOP_WORD_TARGET = 267
_PER_ITEM_WORD_MIN = 100
_PER_ITEM_WORD_MAX = 500
# Generic titles (no count, no duration, no explicit cap): cap total length and
# size 2–4 substantial sections so scripts are not thin ~150-word teasers.
_GENERIC_SECTION_TARGET = 300
_GENERIC_MIN_SECTIONS = 2
_GENERIC_MAX_SECTIONS = 4
_GENERIC_MIN_PER_ITEM = 200
# Spoken narration pacing used to size titles like "3 Hours of …"
_DURATION_WORDS_PER_MINUTE = 130
# Extra headroom so TTS + slight underwrites still meet the title runtime
# (10–15 min over a 3-hour title is acceptable; under-running is not).
_DURATION_WORD_BUFFER = 1.10
# Duration-derived item counts may exceed AUDIO_SCRIPT_MAX_ITEMS so long
# runtimes stay near the promised listening time at SOP density.
_DURATION_ITEM_CAP = 500

# Amounts may be whole or decimal hours ("1.5 Hours", "2.25 hrs"). Digits-only
# would match the trailing "5" in "1.5 Hours" as a fake 5-hour runtime.
_TITLE_DURATION_RE = re.compile(
    r"\b(?P<amount>\d{1,3}(?:\.\d{1,2})?)\s*(?P<unit>hours?|hrs?|minutes?|mins?)\b",
    re.IGNORECASE,
)

_SPELLED_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_SPELLED_DURATION_RE = re.compile(
    r"\b(?P<word>" + "|".join(_SPELLED_ONES) + r")\s*"
    r"(?P<unit>hours?|hrs?|minutes?|mins?)\b",
    re.IGNORECASE,
)

ProgressFn = Callable[[str, int, int], None]


class _ConceptList(BaseModel):
    concepts: list[str] = Field(
        description=(
            "Distinct concept titles, 2–6 words each, sentence case "
            "(first word capital only)."
        ),
    )

    @field_validator("concepts", mode="before")
    @classmethod
    def _coerce_concepts(cls, v):
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except ValueError:
                pass
            return [line.strip() for line in v.splitlines() if line.strip()]
        return v


class _ItemDraft(BaseModel):
    title: str
    paragraphs: list[str] = Field(
        description="Body paragraphs; no heading line in paragraphs.",
    )

    @field_validator("paragraphs", mode="before")
    @classmethod
    def _coerce_paragraphs(cls, v):
        if isinstance(v, str):
            parts = [p.strip() for p in v.split("\n\n") if p.strip()]
            return parts or [v.strip()]
        return v


def is_generic_audio_title(title: str) -> bool:
    """True when the title has no promised count and no duration phrase."""
    return (
        _resolve_promised_item_count(title) is None
        and parse_title_duration_minutes(title) is None
    )


def duration_word_budget(minutes: int) -> int:
    """Total words to generate for a duration title (includes TTS headroom)."""
    return int(minutes * _DURATION_WORDS_PER_MINUTE * _DURATION_WORD_BUFFER)


def is_duration_audio_title(title: str) -> bool:
    return parse_title_duration_minutes(title) is not None


def _derive_item_count_from_word_budget(
    budget: int,
    *,
    cap: int | None = None,
    generic: bool = False,
) -> int:
    """Section count from an overall word budget.

    Generic titles use fewer, meatier sections (~300 w/section, 2–4 items) so
    the script fills out the topic instead of many thin ~150-word slices.
    Duration/count titles use SOP density (~267 w/section).
    """
    if generic:
        derived = round(budget / _GENERIC_SECTION_TARGET)
        return max(
            _GENERIC_MIN_SECTIONS,
            min(_GENERIC_MAX_SECTIONS, derived),
        )
    derived = round(budget / _SOP_WORD_TARGET)
    max_items = cap if cap is not None else settings.audio_script_max_items
    return max(1, min(derived, max_items))


def resolve_item_count(
    title: str,
    item_limit: int | None = None,
    *,
    overall_word_limit: int | None = None,
) -> int:
    """Resolve how many items to write.

    When ``item_limit`` is set, it is the exact count (capped by max).
    Otherwise prefer a real marketing count from the title (e.g. ``100 ways``).
    Duration phrases like ``3 Hours of …`` are *not* treated as item counts —
    they size the script via an overall word budget and a derived item count
    at SOP density (~267 words/item at 130 wpm).
    Generic titles (no count, no duration) derive sections from the overall
    word budget (default ``AUDIO_SCRIPT_GENERIC_DEFAULT_WORDS``).
    """
    if item_limit is not None and item_limit > 0:
        return max(1, min(item_limit, settings.audio_script_max_items))
    promised = _resolve_promised_item_count(title)
    if promised is not None:
        return max(1, min(promised, settings.audio_script_max_items))
    duration_minutes = parse_title_duration_minutes(title)
    if duration_minutes is not None:
        budget = duration_word_budget(duration_minutes)
        derived = round(budget / _SOP_WORD_TARGET)
        cap = max(settings.audio_script_max_items, _DURATION_ITEM_CAP)
        return max(1, min(derived, cap))
    if is_generic_audio_title(title):
        budget = (
            overall_word_limit
            if overall_word_limit is not None and overall_word_limit > 0
            else settings.audio_script_generic_default_words
        )
        return _derive_item_count_from_word_budget(budget, generic=True)
    return max(1, settings.audio_script_max_items)


def parse_title_duration_minutes(title: str) -> int | None:
    """Extract a requested runtime from titles like ``3 Hours`` / ``1.5 Hours``."""
    text = title or ""
    match = _TITLE_DURATION_RE.search(text)
    if match:
        amount = float(match.group("amount"))
        unit = match.group("unit").lower()
    else:
        spelled = _SPELLED_DURATION_RE.search(text)
        if not spelled:
            return None
        amount = float(_SPELLED_ONES[spelled.group("word").lower()])
        unit = spelled.group("unit").lower()
    if amount <= 0:
        return None
    if unit.startswith(("hour", "hr")):
        minutes = amount * 60
    else:
        minutes = amount
    return max(1, int(round(minutes)))


def _resolve_promised_item_count(title: str) -> int | None:
    """Return a title count unless it is actually a duration phrase.

    ``3 Hours of … Secrets`` would otherwise parse as ``3`` via the secrets
    noun. ``1.5 Hours of … Tricks`` would otherwise parse the trailing ``5``
    as five tricks. Suppress any marketing-count digit that overlaps the
    duration amount token.
    """
    promised = parse_promised_count(title, "")
    if promised is None:
        return None
    text = title or ""
    digit_duration = _TITLE_DURATION_RE.search(text)
    if digit_duration is not None:
        amount_start, amount_end = digit_duration.span("amount")
        # Any \bdigits\b whose span overlaps the duration amount belongs to
        # the runtime phrase, not an item count.
        for m in re.finditer(r"\b(\d{1,4})\b", text):
            if int(m.group(1)) != promised:
                continue
            if m.start() < amount_end and m.end() > amount_start:
                return None
            break
        return promised
    spelled = _SPELLED_DURATION_RE.search(text)
    if spelled and _SPELLED_ONES[spelled.group("word").lower()] == promised:
        return None
    return promised


def resolve_overall_word_limit(
    title: str,
    overall_word_limit: int | None = None,
) -> int | None:
    """Return explicit overall limit or derive one from title duration/generic."""
    if overall_word_limit is not None and overall_word_limit > 0:
        return int(overall_word_limit)
    duration_minutes = parse_title_duration_minutes(title)
    if duration_minutes is not None:
        return duration_word_budget(duration_minutes)
    if is_generic_audio_title(title):
        return settings.audio_script_generic_default_words
    return None


def _format_duration_label(minutes: float) -> str:
    total_seconds = int(round(minutes * 60))
    m, s = divmod(total_seconds, 60)
    return f"{m}:{s:02d}"


def estimate_duration_fit(
    title: str,
    overall_word_limit: int | None = None,
) -> dict[str, int | float | str | bool | None]:
    """Compare title duration vs an explicit overall word cap.

    Conflict when the title promises a runtime and the cap is too low to reach
    it at ``_DURATION_WORDS_PER_MINUTE``.
    """
    duration_minutes = parse_title_duration_minutes(title)
    cap = (
        int(overall_word_limit)
        if overall_word_limit is not None and overall_word_limit > 0
        else None
    )
    words_needed = (
        duration_minutes * _DURATION_WORDS_PER_MINUTE
        if duration_minutes is not None
        else None
    )
    generation_budget = (
        duration_word_budget(duration_minutes)
        if duration_minutes is not None
        else None
    )
    estimated_minutes_from_cap = (
        cap / _DURATION_WORDS_PER_MINUTE if cap is not None else None
    )

    conflict = False
    reason = ""
    if (
        duration_minutes is not None
        and cap is not None
        and words_needed is not None
        and cap < words_needed
    ):
        conflict = True
        title_label = _format_duration_label(duration_minutes)
        cap_label = _format_duration_label(estimated_minutes_from_cap or 0)
        reason = (
            f"Title promises ~{title_label} of narration (~{words_needed} words "
            f"at {_DURATION_WORDS_PER_MINUTE} wpm), but your word cap is {cap} "
            f"(~{cap_label})."
        )

    return {
        "title_duration_minutes": duration_minutes,
        "words_needed": words_needed,
        "generation_word_budget": generation_budget,
        "overall_word_limit": cap,
        "estimated_minutes_from_cap": estimated_minutes_from_cap,
        "words_per_minute": _DURATION_WORDS_PER_MINUTE,
        "conflict": conflict,
        "reason": reason,
    }


def resolve_word_band(word_count: int | None = None) -> tuple[int, int, int]:
    """Return (word_min, word_max, target) for prompts and rewrite checks.

    None → SOP 265–270 (target 267).
    N → N±5.
    """
    if word_count is None:
        return _SOP_WORD_MIN, _SOP_WORD_MAX, _SOP_WORD_TARGET
    target = int(word_count)
    return max(1, target - 5), target + 5, target


def resolve_effective_word_count(
    item_count: int,
    word_count: int | None = None,
    overall_word_limit: int | None = None,
    *,
    min_per_item: int | None = None,
    title: str | None = None,
) -> int | None:
    """Resolve per-item word target from ``word_count`` and/or overall budget.

    Returns ``None`` when neither is set (caller uses SOP band via
    ``resolve_word_band``). When both are set and
    ``word_count * item_count`` exceeds the overall budget, the overall
    budget wins (per-item target = overall // item_count, clamped 100–500).

    Duration titles without an explicit ``word_count`` use the full SOP band
    (265–270) whenever it fits the generation budget — floor-division per-item
    targets (e.g. 265) caused systematic under-runs vs promised runtime.
    """
    n = max(1, int(item_count))
    floor = (
        min_per_item
        if min_per_item is not None
        else _PER_ITEM_WORD_MIN
    )
    derived: int | None = None
    if overall_word_limit is not None and overall_word_limit > 0:
        derived = max(
            floor,
            min(_PER_ITEM_WORD_MAX, overall_word_limit // n),
        )

    if word_count is not None:
        if (
            derived is not None
            and overall_word_limit is not None
            and word_count * n > overall_word_limit
        ):
            return derived
        return int(word_count)

    if (
        derived is not None
        and title
        and is_duration_audio_title(title)
        and n * _SOP_WORD_TARGET <= overall_word_limit
    ):
        return None

    return derived


def generic_min_per_item(title: str, overall_word_limit: int | None) -> int | None:
    """Higher per-section floor for default-sized generic titles."""
    if overall_word_limit is not None:
        return None
    if is_generic_audio_title(title):
        return _GENERIC_MIN_PER_ITEM
    return None


def word_count(text: str) -> int:
    return len(text.split())


def to_sentence_case(title: str) -> str:
    """Sentence case: first alphabetic character upper, all others lower.

    Example: 'Foot In The Door Escalation' → 'Foot in the door escalation'
    """
    text = (title or "").strip()
    if not text:
        return text
    lower = text.lower()
    for i, ch in enumerate(lower):
        if ch.isalpha():
            return lower[:i] + ch.upper() + lower[i + 1 :]
    return lower


def body_from_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(p.strip() for p in paragraphs if p.strip())


def format_transcript(items: list[AudioScriptItem]) -> str:
    """Full narration transcript: title, blank line, paragraphs, blank between items."""
    blocks: list[str] = []
    for item in items:
        blocks.append(f"{item.title.strip()}\n\n{item.body.strip()}")
    return "\n\n".join(blocks)


def _trim_concepts(concepts: list[str], count: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in concepts:
        normalized = to_sentence_case(c)
        key = normalized.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(normalized)
        if len(out) >= count:
            break
    return out


def _brainstorm(
    title: str,
    count: int,
    category: str,
    feedback: str | None = None,
    *,
    target_duration_minutes: int | None = None,
    overall_word_limit: int | None = None,
) -> list[str]:
    result = llm.structured(
        prompts.brainstorm_user(
            title, count, feedback=feedback,
            target_duration_minutes=target_duration_minutes,
            overall_word_limit=overall_word_limit,
        ),
        _ConceptList,
        system=prompts.brainstorm_system(category),
        model=settings.content_model,
        max_tokens=8000,
    )
    concepts = _trim_concepts(result.concepts, count)
    if len(concepts) < count:
        log.warning("brainstorm_short", got=len(concepts), want=count)
    return concepts


def _refine(
    title: str,
    concepts: list[str],
    count: int,
    category: str,
    feedback: str | None = None,
    *,
    target_duration_minutes: int | None = None,
    overall_word_limit: int | None = None,
) -> list[str]:
    result = llm.structured(
        prompts.refine_user(
            title, concepts, count, feedback=feedback,
            target_duration_minutes=target_duration_minutes,
            overall_word_limit=overall_word_limit,
        ),
        _ConceptList,
        system=prompts.refine_system(category),
        model=settings.content_model,
        max_tokens=8000,
    )
    refined = _trim_concepts(result.concepts, count)
    if len(refined) < count:
        for c in concepts:
            if len(refined) >= count:
                break
            normalized = to_sentence_case(c)
            if normalized.lower() not in {x.lower() for x in refined}:
                refined.append(normalized)
    return [to_sentence_case(c) for c in refined[:count]]


def _write_item(
    title: str,
    concept_title: str,
    number: int,
    total: int,
    category: str,
    *,
    word_min: int,
    word_max: int,
    feedback: str | None = None,
    duration_context: str = "",
) -> AudioScriptItem:
    max_rewrites = 3 if duration_context else 2
    draft = llm.structured(
        prompts.item_user(
            title, concept_title, number, total,
            word_min=word_min, word_max=word_max,
            feedback=feedback,
            duration_context=duration_context,
        ),
        _ItemDraft,
        system=prompts.item_system(
            category, word_min=word_min, word_max=word_max,
            duration_context=duration_context,
        ),
        model=settings.draft_model,
        max_tokens=2000,
    )
    body = body_from_paragraphs(draft.paragraphs)
    wc = word_count(body)

    for attempt in range(max_rewrites):
        if word_min <= wc <= word_max:
            break
        log.info(
            "item_rewrite",
            concept=concept_title,
            words=wc,
            word_min=word_min,
            word_max=word_max,
            attempt=attempt + 1,
        )
        draft = llm.structured(
            prompts.item_rewrite_user(
                title, concept_title, draft.paragraphs, wc,
                word_min=word_min, word_max=word_max,
                feedback=feedback,
                duration_context=duration_context,
            ),
            _ItemDraft,
            system=prompts.item_system(
                category, word_min=word_min, word_max=word_max,
                duration_context=duration_context,
            ),
            model=settings.draft_model,
            max_tokens=2000,
        )
        body = body_from_paragraphs(draft.paragraphs)
        wc = word_count(body)

    item_title = to_sentence_case(draft.title.strip() or concept_title)
    return AudioScriptItem(
        number=number,
        title=item_title,
        body=body,
        word_count=wc,
    )


def generate_audio_script(
    title: str,
    *,
    category: str = "psychology",
    item_count: int | None = None,
    word_count: int | None = None,
    overall_word_limit: int | None = None,
    feedback: str | None = None,
    on_progress: ProgressFn | None = None,
) -> AudioScriptResult:
    """Run brainstorm → refine → write and return a transcript result."""
    resolved_overall = resolve_overall_word_limit(title, overall_word_limit)
    count = (
        item_count
        if item_count is not None
        else resolve_item_count(title, overall_word_limit=resolved_overall)
    )
    effective_word_count = resolve_effective_word_count(
        count,
        word_count=word_count,
        overall_word_limit=resolved_overall,
        min_per_item=generic_min_per_item(title, overall_word_limit),
        title=title,
    )
    word_min, word_max, word_target = resolve_word_band(effective_word_count)
    target_duration_minutes = parse_title_duration_minutes(title)
    duration_context = prompts.duration_context_block(
        title,
        target_duration_minutes=target_duration_minutes,
        overall_word_limit=resolved_overall,
        item_count=count,
    )
    revision = (feedback or "").strip() or None
    log.info(
        "audio_script_start",
        title=title,
        count=count,
        category=category,
        word_min=word_min,
        word_max=word_max,
        word_target=word_target,
        overall_word_limit=resolved_overall,
        target_duration_minutes=target_duration_minutes,
        has_feedback=bool(revision),
    )

    def _progress(stage: str, done: int, total: int) -> None:
        if on_progress:
            on_progress(stage, done, total)

    _progress(AudioScriptStage.BRAINSTORMING, 0, count)
    concepts = _brainstorm(
        title, count, category, feedback=revision,
        target_duration_minutes=target_duration_minutes,
        overall_word_limit=resolved_overall,
    )
    if len(concepts) < count:
        raise ValueError(
            f"Brainstorm returned only {len(concepts)} concepts; need {count}"
        )

    _progress(AudioScriptStage.REFINING_LIST, 0, count)
    concepts = _refine(
        title, concepts, count, category, feedback=revision,
        target_duration_minutes=target_duration_minutes,
        overall_word_limit=resolved_overall,
    )

    _progress(AudioScriptStage.WRITING_ITEMS, 0, count)
    items: list[AudioScriptItem | None] = [None] * count
    done_lock = threading.Lock()
    done_count = 0

    def _write_one(idx: int, concept: str) -> None:
        nonlocal done_count
        item = _write_item(
            title, concept, idx + 1, count, category,
            word_min=word_min, word_max=word_max,
            feedback=revision,
            duration_context=duration_context,
        )
        items[idx] = item
        with done_lock:
            done_count += 1
            _progress(AudioScriptStage.WRITING_ITEMS, done_count, count)

    with ctx_pool(settings.audio_script_item_workers, "as") as pool:
        futures = [
            pool.submit(_write_one, i, concepts[i])
            for i in range(count)
        ]
        for fut in futures:
            fut.result()

    final_items = [it for it in items if it is not None]
    final_items.sort(key=lambda x: x.number)
    transcript = format_transcript(final_items)
    total_words = sum(it.word_count for it in final_items)

    log.info(
        "audio_script_done",
        title=title,
        items=len(final_items),
        total_words=total_words,
    )
    return AudioScriptResult(
        title=title,
        category=category,
        items=final_items,
        transcript=transcript,
        total_words=total_words,
    )
