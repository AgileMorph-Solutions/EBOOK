"""Audio narration script generation (brainstorm → refine → parallel write).

Produces retention-focused conversational scripts: N items at a target word
band each, formatted as a transcript for ElevenLabs narration.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Literal

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

Category = Literal["psychology", "relationship", "philosophy", "general"]

# SOP default band when API omits word_count
_SOP_WORD_MIN = 265
_SOP_WORD_MAX = 270
_SOP_WORD_TARGET = 267

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
        description="4–5 body paragraphs; no heading line in paragraphs.",
    )

    @field_validator("paragraphs", mode="before")
    @classmethod
    def _coerce_paragraphs(cls, v):
        if isinstance(v, str):
            parts = [p.strip() for p in v.split("\n\n") if p.strip()]
            return parts or [v.strip()]
        return v


def resolve_item_count(title: str, item_limit: int | None = None) -> int:
    """Resolve how many items to write.

    When ``item_limit`` is set, it is the exact count (capped by max).
    Otherwise the count comes from the number promised in the title.
    """
    if item_limit is not None and item_limit > 0:
        return max(1, min(item_limit, settings.audio_script_max_items))
    promised = parse_promised_count(title, "") or settings.audio_script_max_items
    return max(1, min(promised, settings.audio_script_max_items))


def resolve_word_band(word_count: int | None = None) -> tuple[int, int, int]:
    """Return (word_min, word_max, target) for prompts and rewrite checks.

    None → SOP 265–270 (target 267).
    N → N±5.
    """
    if word_count is None:
        return _SOP_WORD_MIN, _SOP_WORD_MAX, _SOP_WORD_TARGET
    target = int(word_count)
    return max(1, target - 5), target + 5, target


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


def _brainstorm(title: str, count: int, category: Category) -> list[str]:
    result = llm.structured(
        prompts.brainstorm_user(title, count),
        _ConceptList,
        system=prompts.brainstorm_system(category),
        model=settings.content_model,
        max_tokens=8000,
    )
    concepts = _trim_concepts(result.concepts, count)
    if len(concepts) < count:
        log.warning("brainstorm_short", got=len(concepts), want=count)
    return concepts


def _refine(title: str, concepts: list[str], count: int, category: Category) -> list[str]:
    result = llm.structured(
        prompts.refine_user(title, concepts, count),
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
    category: Category,
    *,
    word_min: int,
    word_max: int,
) -> AudioScriptItem:
    draft = llm.structured(
        prompts.item_user(
            title, concept_title, number, total,
            word_min=word_min, word_max=word_max,
        ),
        _ItemDraft,
        system=prompts.item_system(category, word_min=word_min, word_max=word_max),
        model=settings.draft_model,
        max_tokens=2000,
    )
    body = body_from_paragraphs(draft.paragraphs)
    wc = word_count(body)

    if wc < word_min or wc > word_max:
        log.info("item_rewrite", concept=concept_title, words=wc,
                 word_min=word_min, word_max=word_max)
        draft = llm.structured(
            prompts.item_rewrite_user(
                title, concept_title, draft.paragraphs, wc,
                word_min=word_min, word_max=word_max,
            ),
            _ItemDraft,
            system=prompts.item_system(category, word_min=word_min, word_max=word_max),
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
    category: Category = "psychology",
    item_count: int | None = None,
    word_count: int | None = None,
    on_progress: ProgressFn | None = None,
) -> AudioScriptResult:
    """Run brainstorm → refine → write and return a transcript result."""
    count = item_count if item_count is not None else resolve_item_count(title)
    word_min, word_max, word_target = resolve_word_band(word_count)
    log.info(
        "audio_script_start",
        title=title,
        count=count,
        category=category,
        word_min=word_min,
        word_max=word_max,
        word_target=word_target,
    )

    def _progress(stage: str, done: int, total: int) -> None:
        if on_progress:
            on_progress(stage, done, total)

    _progress(AudioScriptStage.BRAINSTORMING, 0, count)
    concepts = _brainstorm(title, count, category)
    if len(concepts) < count:
        raise ValueError(
            f"Brainstorm returned only {len(concepts)} concepts; need {count}"
        )

    _progress(AudioScriptStage.REFINING_LIST, 0, count)
    concepts = _refine(title, concepts, count, category)

    _progress(AudioScriptStage.WRITING_ITEMS, 0, count)
    items: list[AudioScriptItem | None] = [None] * count
    done_lock = threading.Lock()
    done_count = 0

    def _write_one(idx: int, concept: str) -> None:
        nonlocal done_count
        item = _write_item(
            title, concept, idx + 1, count, category,
            word_min=word_min, word_max=word_max,
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
