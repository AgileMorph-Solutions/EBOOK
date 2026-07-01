"""Practical-guide content generation (Claude, length-budgeted, safety-aware).

One structured call per section returns a section intro, a set of full how-to
cards, and a list of quick uses — all sized to FIT the senior-first page layout
by construction (so pages are full but never clipped). Charts, checklists, and
the "when to call a pro" page have their own small generators. The safety canon
is injected into every prompt so the model writes safe instructions.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from bookforge import safety
from bookforge.clients import llm
from bookforge.layout.labels import section_eyebrow
from bookforge.pipeline.content_guardrails import coherence_rules
from bookforge.schemas import (
    BlockType, BookBible, Chapter, ChecklistModel, ContentBlock, ImageBrief,
    Passage, SectionPlan, TableModel, UseEntry,
)

log = structlog.get_logger(__name__)

_PRODUCT_LINES = {
    "hp": (
        "Every use must be genuinely useful, distinct, and SAFE, using ordinary "
        "3% hydrogen peroxide (or plain water, cloth, or a spray bottle)."
    ),
    "pest": (
        "Every use must be genuinely useful, distinct, and SAFE, using common "
        "household items already at home."
    ),
    "general": (
        "Every use must be genuinely useful, distinct, and SAFE, using common "
        "household items already at home."
    ),
}


def _voice(bible: BookBible) -> str:
    v = bible.voice
    banned = ", ".join(f'"{b}"' for b in v.banned_patterns) or "hype words and jargon"
    return (
        f"You are the author of '{bible.title}', writing for SENIORS (age 65+). "
        f"Persona: {v.narrator_persona}. Voice: {v.tone_register}. "
        f"Write in the second person ('you'), one clear idea per sentence, plain "
        f"everyday words, no jargon and no idioms. Never use these: {banned}. "
        f"Be warm, practical, and reassuring.\n"
        f"{coherence_rules(bible)}"
    )


class _SectionDraft(BaseModel):
    intro: str = Field(description="One warm paragraph, 25-40 words, introducing the section.")
    cards: list[UseEntry] = Field(description="Full how-to entries (the workhorse cards).")
    card_photos: list[str] = Field(
        default_factory=list,
        description="One short photo subject per card, same order as cards.")
    quick_uses: list[UseEntry] = Field(description="Short one-line uses for a compact list.")


_SECTION_PROMPT = """Write the content for the "{title}" section of the book.

WHAT THIS SECTION COVERS: {summary}

{grounding}

Produce a section like this:
- intro: one warm paragraph, 25-40 words.
- cards: EXACTLY {cards} full how-to entries. For EACH card:
    * title: an action, 2-6 words (e.g. "Whiten the grout").
    * intro: 20-35 words, 2 short sentences on why it helps.
    * what_you_need: 2-5 items, each 1-5 words.
    * steps: 3-5 steps, each a single action, 6-20 words.
    * savings: 6-12 words naming what it replaces and a rough dollar amount.
    * caution: ONLY if there is a real safety point, max 18 words; otherwise "".
    * number: leave 0 (it is assigned later).
- card_photos: EXACTLY {cards} short photo subjects (one per card, same order),
  each a concrete scene to photograph (no text, no faces), e.g.
  "a sparkling clean white-tiled shower with bright grout lines".
- quick_uses: EXACTLY {quick} short entries. For EACH: a title (2-6 words) and an
  intro that is ONE line, 8-16 words, telling how to do it. Leave steps empty.

{safety}

{product_line} Stay UNDER the length limits — short is better than long.
Do not mention inflated tip totals that contradict the book title or subtitle."""


def _format_passages(passages: list[Passage]) -> str:
    if not passages:
        return ""
    body = "\n\n".join(f"[{p.id}] {p.text}" for p in passages)
    return (
        "GROUNDING PASSAGES (only assert facts supported here; cite ids when unsure):\n"
        f"{body}"
    )


def write_section(
    section: SectionPlan,
    bible: BookBible,
    *,
    start_number: int,
    safety_key: str = "hp",
    passages: list[Passage] | None = None,
    list_lead: str = "",
) -> tuple[Chapter, int]:
    """Generate one section's blocks. Returns (Chapter, next_global_number)."""
    passages = passages or []
    draft = llm.structured(
        _SECTION_PROMPT.format(
            title=section.title, summary=section.summary,
            cards=section.full_card_quota, quick=section.list_quota,
            safety=safety.rules_for(safety_key),
            product_line=_PRODUCT_LINES.get(safety_key, _PRODUCT_LINES["general"]),
            grounding=_format_passages(passages),
        ),
        _SectionDraft, system=_voice(bible), max_tokens=8000,
    )

    blocks: list[ContentBlock] = [
        ContentBlock(type=BlockType.section_intro, text=draft.intro.strip())
    ]
    n = start_number
    img_left = max(0, section.image_quota)
    for i, card in enumerate(draft.cards):
        card.number = n
        n += 1
        blk = ContentBlock(type=BlockType.use_card, heading=card.title, entries=[card])
        if img_left > 0:
            subject = (draft.card_photos[i] if i < len(draft.card_photos)
                       and draft.card_photos[i] else card.title)
            blk.image_brief = ImageBrief(prompt=subject, kind="photo", aspect="square")
            img_left -= 1
        blocks.append(blk)

    for q in draft.quick_uses:
        q.number = n
        n += 1
    if draft.quick_uses:
        base = section_eyebrow(section.title)
        lead = list_lead or (
            "Quick jobs you can do in a minute or two with items already at home."
        )
        blocks.append(ContentBlock(
            type=BlockType.use_list,
            heading=f"More {base} Uses",
            text=lead,
            entries=draft.quick_uses))

    log.info("section_written", title=section.title, cards=len(draft.cards),
             quick=len(draft.quick_uses), uses=n - start_number)
    return Chapter(index=section.index, title=section.title, blocks=blocks), n


_CHART_PROMPT = """Make a money-saving comparison table for the "{section}" part of \
"{book_title}" — a seniors' practical home guide.

Columns (exactly these, in order): {columns}
Produce EXACTLY {rows} rows. Each cell is 2-6 words. The first column names a store
product or service with its rough price. The middle column is the cheap at-home method.
The last column is a rough yearly dollar saving (e.g. "$24").
Keep everything realistic and safe. Do not inflate tip or savings counts."""


def make_chart(section: str, heading: str, columns: list[str], rows: int,
               bible: BookBible) -> ContentBlock:
    table = llm.structured(
        _CHART_PROMPT.format(
            section=section, book_title=bible.title,
            columns=", ".join(columns), rows=rows),
        TableModel, system=_voice(bible), max_tokens=2000)
    table.columns = columns
    table.note = "Savings are rough estimates based on regular home use."
    return ContentBlock(
        type=BlockType.chart, heading=heading,
        text="See how cheap home methods compare to store products.",
        table=table,
    )


_CHECK_PROMPT = """Make a simple home checklist titled "{heading}" for seniors, about:
{about}

Produce {items} short checklist items, each 4-9 words, a single clear task. Give each a
cadence of "weekly" or "monthly". Keep them safe and easy.
Headings must match the checklist topic exactly."""


def make_checklist(heading: str, about: str, items: int, bible: BookBible) -> ContentBlock:
    cl = llm.structured(
        _CHECK_PROMPT.format(heading=heading, about=about, items=items),
        ChecklistModel, system=_voice(bible), max_tokens=2000)
    return ContentBlock(type=BlockType.checklist, heading=heading,
                        text="Tick these off as you go — it keeps things easy.", checklist=cl)


class _Pro(BaseModel):
    lead: str
    signs: list[str]
    reassure: str


_PRO_PROMPT = """Write a short "When to Call a Professional" page for a seniors' home
guide about {topic}.
- lead: 2 sentences, 25-40 words.
- signs: 4-6 warning signs, each 6-16 words, when a professional is the safer choice.
- reassure: 1-2 warm sentences, 20-35 words, that it is fine to ask for help.
Keep it calm and plain. Do not mention inflated tip totals."""


def make_pro(topic: str, bible: BookBible) -> dict:
    pro = llm.structured(_PRO_PROMPT.format(topic=topic), _Pro,
                         system=_voice(bible), max_tokens=1500)
    return {"heading": "When to Call a Professional", "lead": pro.lead,
            "signs": pro.signs, "reassure": pro.reassure}
