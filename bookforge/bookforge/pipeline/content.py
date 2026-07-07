"""Stage 2 — content generation.

Narrative path: novelist-grade prose chapters with claim grounding.
Practical-guide path: structured card/list blocks for the senior guide layout.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, ValidationError

from bookforge.clients import llm
from bookforge.pipeline import content_guide, content_guardrails, grounding, layout_budget
from bookforge.schemas import (
    BlockType, BookBible, Chapter, ChapterPlan, Claim, ContentBlock, SourceDoc,
    SpecialPlan,
)

log = structlog.get_logger(__name__)


def _voice_system(bible: BookBible) -> str:
    v = bible.voice
    banned = ", ".join(f'"{b}"' for b in v.banned_patterns)
    return (
        f"You are the author of '{bible.title}'. Persona: {v.narrator_persona}. "
        f"Register: {v.tone_register}. POV: {v.pov}. Pacing: {v.pacing}. "
        f"Vocabulary: {v.vocabulary}.\n"
        f"NEVER use these AI-tells or clichés: {banned}. "
        "Show, don't tell. Vary sentence rhythm. Ground every factual claim in the "
        "provided source passages and cite their ids."
    )


_DRAFT_PROMPT = """Write chapter {idx}: "{title}".

Chapter summary: {summary}
Beats to cover: {beats}
LENGTH BUDGET: this chapter must fit in approximately {page_budget} printed \
page(s) (~300-350 words per page, image-rich layout). {length_guidance}

GROUNDING PASSAGES (cite these ids in each claim; do not assert facts absent here):
{passages}

STORY SO FAR (avoid repeating; build on it): {running_summary}

Return structured ContentBlocks for this chapter. Guidance:
- Open with an `intro` block that hooks the reader like literary nonfiction.
- Use `history`, `body`, and `sidebar` blocks; add `pull_quote` where it lands.
- For recipe content use `ingredients` and `method` blocks.
- For EACH block, extract atomic factual `claims`, each tied to the \
source_passage_ids it comes from. Leave verdict as "pending".
- Give blocks that deserve a hero image an `image_brief` with a vivid prompt \
(the global art style is added later — describe subject, scene, mood). Set \
needs_in_image_text=true only for labels/cards with words baked in.
- `blocks` must NEVER be empty or missing — even a very short 1-page chapter \
must return at least one `intro` block with a short paragraph.
- Stay within the LENGTH BUDGET above — do not pad with extra blocks or beats \
just because more source material is available."""

_DEV_EDIT = """Developmental edit of this chapter draft. Improve structure and arc: \
does each block earn its place? Is there a hook, momentum, and a satisfying close? \
Convert flat summary into scene where it helps ("show don't tell"). Keep all claims \
and their source_passage_ids intact. Do NOT add new blocks or lengthen the chapter — \
match or trim the draft's existing length. Return the revised chapter.

DRAFT:
{draft}"""

_LINE_EDIT = """Line edit this chapter. Sharpen sentence rhythm, sensory detail, and \
imagery; vary sentence openings; cut flab. Preserve meaning, claims, and citations. \
Then remove every banned AI-tell/cliché. Return the polished chapter.

CHAPTER:
{chapter}"""


def _length_guidance(page_budget: int) -> str:
    if page_budget <= 1:
        return "Write ONE short intro block only (120-250 words total) — no more."
    if page_budget <= 3:
        return f"Use 1-3 blocks total, roughly {page_budget * 300} words combined."
    return (
        f"Use enough blocks to fill the budget without padding — roughly "
        f"{page_budget * 300} words combined across all blocks."
    )


def _fallback_chapter(plan: ChapterPlan) -> Chapter:
    """Minimal, always-valid chapter used when the model's structured output
    for a chapter omits required fields (seen on very small target_pages)."""
    text = plan.summary.strip() or f"An introduction to {plan.title}."
    return Chapter(
        index=plan.index, title=plan.title,
        blocks=[ContentBlock(type=BlockType.intro, text=text)],
    )


def _safe_chapter_call(
    prompt: str, system: str, *, idx: int, stage: str, fallback: Chapter,
) -> Chapter:
    """Call llm.structured() for a Chapter, falling back to the previous good
    chapter (or a minimal stub) rather than crashing the whole pipeline when
    the model omits required fields — most common on very short chapters."""
    try:
        return llm.structured(prompt, Chapter, system=system, max_tokens=8000)
    except ValidationError as exc:
        log.warning("chapter_stage_invalid", stage=stage, idx=idx, err=str(exc)[:300])
        return fallback


def write_guide(
    bible: BookBible,
    source: SourceDoc,
    *,
    limit: int | None = None,
) -> list[Chapter]:
    """Generate practical-guide sections as structured card/list chapters."""
    sections = bible.sections[:limit] if limit else bible.sections
    chapters: list[Chapter] = []
    n = 1
    list_lead = bible.list_lead or (
        "Quick jobs you can do in a minute or two with items already at home."
    )
    for section in sections:
        passages = grounding.retrieve(
            f"{section.title} {section.summary}", source.passages, k=8,
        )
        ch, n = content_guide.write_section(
            section, bible, start_number=n,
            safety_key=bible.safety_key,
            passages=passages,
            list_lead=list_lead,
        )
        for sp in bible.specials_for(section.index):
            ch.blocks.append(_make_special(sp, section.title, bible))
        chapters.append(layout_budget.trim_chapter(ch))
    drift = content_guardrails.scan_chapters_for_count_drift(chapters, bible)
    for issue in drift:
        log.warning("count_drift", issue=issue)
    log.info("guide_written", sections=len(chapters), uses=n - 1,
             count_drift=len(drift))
    return chapters


def _make_special(sp: SpecialPlan, section_title: str, bible: BookBible):
    if sp.kind == "chart":
        return content_guide.make_chart(
            section_title, sp.heading, sp.columns, sp.rows, bible,
        )
    return content_guide.make_checklist(sp.heading, sp.about, sp.items, bible)


def write_chapter(
    plan: ChapterPlan,
    bible: BookBible,
    source: SourceDoc,
    running_summary: str,
) -> Chapter:
    system = _voice_system(bible)
    passages = grounding.retrieve(
        f"{plan.title} {plan.summary} {' '.join(plan.beats)}", source.passages, k=8
    )
    passage_text = "\n\n".join(f"[{p.id}] {p.text}" for p in passages)

    fallback = _fallback_chapter(plan)
    page_budget = max(1, plan.page_budget)
    draft = _safe_chapter_call(
        _DRAFT_PROMPT.format(
            idx=plan.index, title=plan.title, summary=plan.summary,
            beats="; ".join(plan.beats), passages=passage_text,
            running_summary=running_summary or "(this is the first chapter)",
            page_budget=page_budget,
            length_guidance=_length_guidance(page_budget),
        ),
        system, idx=plan.index, stage="draft", fallback=fallback,
    )
    dev = _safe_chapter_call(
        _DEV_EDIT.format(draft=draft.model_dump_json()),
        system, idx=plan.index, stage="dev_edit", fallback=draft,
    )
    final = _safe_chapter_call(
        _LINE_EDIT.format(chapter=dev.model_dump_json()),
        system, idx=plan.index, stage="line_edit", fallback=dev,
    )
    final.index, final.title = plan.index, plan.title

    attribute_claims(final, passages)
    log.info("chapter_written", idx=plan.index, blocks=len(final.blocks),
             claims=sum(len(b.claims) for b in final.blocks))
    return final


class _AttrClaim(BaseModel):
    block_index: int
    text: str
    source_passage_ids: list[str]


class _Attribution(BaseModel):
    claims: list["_AttrClaim"]


_ATTR_PROMPT = """Extract every atomic factual claim from this finished chapter and
attribute each to the source passage ids that support it.

SOURCE PASSAGES:
{passages}

CHAPTER BLOCKS (index : text):
{blocks}

Rules:
- A claim is a single checkable assertion (a date, name, quantity, or factual
  statement). Skip pure flavour/atmosphere sentences with no factual content.
- For each claim, give the block_index it appears in, the claim text, and the
  list of source_passage_ids ([] if NOTHING in the sources supports it — be honest).
- Do not invent passage ids; use only the ids shown above."""


def attribute_claims(chapter: Chapter, passages) -> None:
    """Populate chapter.blocks[*].claims from the final prose, citing real ids."""
    passage_text = "\n\n".join(f"[{p.id}] {p.text}" for p in passages)
    blocks_text = "\n\n".join(f"[{i}] {b.text}" for i, b in enumerate(chapter.blocks))
    attribution = llm.structured(
        _ATTR_PROMPT.format(passages=passage_text, blocks=blocks_text),
        _Attribution, max_tokens=4000,
    )
    valid_ids = {p.id for p in passages}
    for b in chapter.blocks:
        b.claims = []
    for i, c in enumerate(attribution.claims):
        if 0 <= c.block_index < len(chapter.blocks):
            cited = [pid for pid in c.source_passage_ids if pid in valid_ids]
            chapter.blocks[c.block_index].claims.append(
                Claim(id=f"{chapter.index}.{c.block_index}.{i}", text=c.text,
                      source_passage_ids=cited)
            )


def summarize_for_memory(chapter: Chapter) -> str:
    """Compress a finished chapter into a few lines for the running memory."""
    text = " ".join(b.text for b in chapter.blocks)[:6000]
    return llm.complete(
        f"In 3 sentences, summarize what chapter '{chapter.title}' covered, "
        f"naming key facts/threads so later chapters don't repeat them:\n\n{text}",
        model=None, max_tokens=300,
    )


def write_book(bible: BookBible, source: SourceDoc, *, limit: int | None = None) -> list[Chapter]:
    chapters: list[Chapter] = []
    running = ""
    plans = bible.chapters[:limit] if limit else bible.chapters
    for plan in plans:
        ch = write_chapter(plan, bible, source, running)
        chapters.append(ch)
        running += f"\n- Ch{plan.index} {plan.title}: {summarize_for_memory(ch)}"
    return chapters
