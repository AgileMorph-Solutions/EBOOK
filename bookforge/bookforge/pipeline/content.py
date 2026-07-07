"""Stage 2 — content generation.

Narrative path: novelist-grade prose chapters with claim grounding.
Practical-guide path: structured card/list blocks for the guide card layout.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel

from bookforge.clients import llm
from bookforge.errors import EmptyBookError
from bookforge.pipeline import content_guide, content_guardrails, grounding, layout_budget
from bookforge.schemas import (
    BookBible, Chapter, ChapterPlan, Claim, SectionPlan, SourceDoc, SpecialPlan,
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
just because more source material is available.
- If the book title promises a numbered total (e.g. "50 ways", "9 things"), never \
state a different count in prose; cover the planned beats exactly — no more, no fewer.
- Prose in `intro`, `body`, and `history` blocks must be plain flowing paragraphs — \
no bullet lists, no leading bullet characters (•, -, *)."""

_DEV_EDIT = """Developmental edit of this chapter draft. Improve structure and arc: \
does each block earn its place? Is there a hook, momentum, and a satisfying close? \
Convert flat summary into scene where it helps ("show don't tell"). Keep all claims \
and their source_passage_ids intact. Return the revised chapter.

DRAFT:
{draft}"""

_LINE_EDIT = """Line edit this chapter. Sharpen sentence rhythm, sensory detail, and \
imagery; vary sentence openings; cut flab. Preserve meaning, claims, and citations. \
Correct every spelling, grammar, and punctuation error; verify proper nouns and \
dish/product names are spelled correctly. Never output bullet characters (•, -, *) \
inside prose paragraphs. Then remove every banned AI-tell/cliché. Return the polished chapter.

CHAPTER:
{chapter}"""


def write_guide(
    bible: BookBible,
    source: SourceDoc,
    *,
    limit: int | None = None,
) -> list[Chapter]:
    """Generate practical-guide sections as structured card/list chapters."""
    from bookforge.schemas import profile_of

    sections = bible.sections[:limit] if limit else bible.sections
    chapters: list[Chapter] = []
    n = 1
    noun = profile_of(bible).entry_schema.entry_noun
    list_lead = bible.list_lead or f"Quick {noun} ideas, one line each."
    registry = content_guide.Registry()
    skipped = 0
    for section in sections:
        try:
            ch, n = _build_guide_section(
                section, bible, source, start_number=n,
                registry=registry, list_lead=list_lead,
            )
        except Exception as exc:
            # Degrade, don't die: a failed section is skipped so the rest of the
            # book still ships. If the provider is down, EVERY section fails and
            # the empty-book guard below re-raises for park & auto-resume.
            if llm.is_provider_unavailable(exc):
                raise
            skipped += 1
            log.warning("section_skipped", title=section.title, err=str(exc)[:200])
            continue
        chapters.append(ch)
    if not chapters:
        raise EmptyBookError(
            f"all {len(sections)} sections failed to generate — nothing to ship"
        )
    drift = content_guardrails.scan_chapters_for_count_drift(chapters, bible)
    for issue in drift:
        log.warning("count_drift", issue=issue)
    log.info("guide_written", sections=len(chapters), skipped=skipped, uses=n - 1,
             count_drift=len(drift))
    return chapters


def _make_special(sp: SpecialPlan, section_title: str, bible: BookBible):
    """Build an optional special page (chart/checklist). Returns None on failure
    so a missing decorative page never sinks the book."""
    try:
        if sp.kind == "chart":
            return content_guide.make_chart(
                section_title, sp.heading, sp.columns, sp.rows, bible,
            )
        return content_guide.make_checklist(sp.heading, sp.about, sp.items, bible)
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("special_skipped", kind=sp.kind, heading=sp.heading,
                    err=str(exc)[:200])
        return None


def _build_guide_section(
    section: SectionPlan,
    bible: BookBible,
    source: SourceDoc,
    *,
    start_number: int,
    registry: "content_guide.Registry",
    list_lead: str = "",
    feedback: list[str] | None = None,
) -> tuple[Chapter, int]:
    """Build one guide section end-to-end: retrieve passages → write (optionally
    with QA feedback) → attach its special pages → trim to budget. Shared by the
    initial pass and by the QA fix-loop so regeneration matches generation."""
    passages = grounding.retrieve(
        f"{section.title} {section.summary} "
        f"{' '.join(section.planned_entries)}",
        source.passages, k=8,
    )
    ch, n = content_guide.write_section(
        section, bible, start_number=start_number,
        passages=passages, list_lead=list_lead, registry=registry,
        feedback=feedback,
    )
    for sp in bible.specials_for(section.index):
        block = _make_special(sp, section.title, bible)
        if block is not None:
            ch.blocks.append(block)
    return layout_budget.trim_chapter(ch, bible), n


def _chapter_start_number(ch: Chapter) -> int:
    """The global entry number the section started at (smallest assigned number),
    so a regenerated section keeps its place in the book's numbering."""
    nums = [e.number for b in ch.blocks for e in b.entries if e.number]
    return min(nums) if nums else 1


def regenerate_guide_sections(
    chapters: list[Chapter],
    bible: BookBible,
    source: SourceDoc,
    faults_by_idx: dict[int, list[str]],
    *,
    registry_seed: list[Chapter],
) -> list[Chapter]:
    """Rebuild ONLY the chapters named in faults_by_idx, feeding each its own QA
    faults as targeted revision feedback. Every other chapter is returned as-is.

    Numbering: each rebuilt section reuses its original start number. Dedup: the
    Registry is seeded from every OTHER current chapter, so the redraft still
    avoids repeating the rest of the book but is free to reuse its own titles."""
    from bookforge.schemas import profile_of

    sections_by_idx = {s.index: s for s in bible.sections}
    noun = profile_of(bible).entry_schema.entry_noun
    list_lead = bible.list_lead or f"Quick {noun} ideas, one line each."

    out: list[Chapter] = []
    for ch in chapters:
        faults = faults_by_idx.get(ch.index)
        section = sections_by_idx.get(ch.index)
        if not faults or section is None:
            out.append(ch)
            continue
        registry = content_guide.Registry()
        for other in registry_seed:
            if other.index != ch.index:
                registry.add_chapter(other)
        try:
            new_ch, _ = _build_guide_section(
                section, bible, source,
                start_number=_chapter_start_number(ch),
                registry=registry, list_lead=list_lead, feedback=faults,
            )
        except Exception as exc:
            # Keep the prior draft rather than losing the section — the loop will
            # ship best-effort regardless.
            if llm.is_provider_unavailable(exc):
                raise
            log.warning("section_regen_skipped", idx=ch.index, err=str(exc)[:200])
            out.append(ch)
            continue
        log.info("section_regenerated", idx=ch.index, faults=len(faults))
        out.append(new_ch)
    return out


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

    # The draft is the one indispensable pass — no draft means no chapter, so a
    # non-provider failure here propagates to write_book's per-chapter guard.
    draft = llm.structured(
        _DRAFT_PROMPT.format(
            idx=plan.index, title=plan.title, summary=plan.summary,
            beats="; ".join(plan.beats), passages=passage_text,
            running_summary=running_summary or "(this is the first chapter)",
        ),
        Chapter, system=system, max_tokens=8000,
    )
    # Editorial passes are best-effort polish: on failure, keep the last good
    # version rather than losing the whole chapter.
    final = draft
    try:
        final = llm.structured(_DEV_EDIT.format(draft=draft.model_dump_json()),
                               Chapter, system=system, max_tokens=8000)
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("dev_edit_skipped", idx=plan.index, err=str(exc)[:200])
    try:
        final = llm.structured(_LINE_EDIT.format(chapter=final.model_dump_json()),
                              Chapter, system=system, max_tokens=8000)
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("line_edit_skipped", idx=plan.index, err=str(exc)[:200])
    final.index, final.title = plan.index, plan.title

    try:
        attribute_claims(final, passages)
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("claims_skipped", idx=plan.index, err=str(exc)[:200])
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
    """Compress a finished chapter into a few lines for the running memory.
    Returns "" on failure — a missing memory line only risks minor repetition,
    never the book."""
    text = " ".join(b.text for b in chapter.blocks)[:6000]
    try:
        return llm.complete(
            f"In 3 sentences, summarize what chapter '{chapter.title}' covered, "
            f"naming key facts/threads so later chapters don't repeat them:\n\n{text}",
            model=None, max_tokens=300,
        )
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("memory_summary_skipped", idx=chapter.index, err=str(exc)[:200])
        return ""


def write_book(bible: BookBible, source: SourceDoc, *, limit: int | None = None) -> list[Chapter]:
    chapters: list[Chapter] = []
    running = ""
    plans = bible.chapters[:limit] if limit else bible.chapters
    skipped = 0
    for plan in plans:
        try:
            ch = write_chapter(plan, bible, source, running)
        except Exception as exc:
            # A failed chapter is skipped so the rest of the book still ships;
            # provider-wide failures propagate to the empty-book guard below.
            if llm.is_provider_unavailable(exc):
                raise
            skipped += 1
            log.warning("chapter_skipped", idx=plan.index, title=plan.title,
                        err=str(exc)[:200])
            continue
        chapters.append(ch)
        running += f"\n- Ch{plan.index} {plan.title}: {summarize_for_memory(ch)}"
    if not chapters:
        raise EmptyBookError(
            f"all {len(plans)} chapters failed to generate — nothing to ship"
        )
    if skipped:
        log.warning("book_written_partial", written=len(chapters), skipped=skipped)
    return chapters
