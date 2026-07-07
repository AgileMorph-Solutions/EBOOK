"""Practical-guide content generation (Claude, length-budgeted, safety-aware).

Profile-driven: every label, length rule, and quality bar comes from the book's
EditorialProfile (planned per topic) — no domain vocabulary lives in these
templates. Per chapter, one structured call returns an intro, an optional
opener essay + feature boxes (template-dependent), the entry cards, and a quick
list; a cheap line-editor pass then polishes essay + headnotes. A cross-book
registry rides along so no entry or headnote opening repeats anywhere in the
book. The book's generated safety brief is injected into every prompt.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, Field, field_validator

from bookforge import safety
from bookforge.clients import llm
from bookforge.config import settings
from bookforge.layout.labels import section_eyebrow
from bookforge.pipeline import templates
from bookforge.pipeline.content_guardrails import coherence_rules
from bookforge.schemas import (
    BlockType, BookBible, Chapter, ChecklistModel, ContentBlock,
    EditorialProfile, ImageBrief, Passage, SectionPlan, TableModel, UseEntry,
    profile_of,
)

log = structlog.get_logger(__name__)


class Registry:
    """Cross-chapter memory: what the book has already used, injected into
    every section call so nothing repeats (facts have a half-life — the rule
    must be restated every call, then enforced deterministically in QA)."""

    def __init__(self) -> None:
        self.entry_titles: list[str] = []
        self.openings: list[str] = []

    def add_chapter(self, chapter: Chapter) -> None:
        for block in chapter.blocks:
            for e in block.entries:
                if e.title:
                    self.entry_titles.append(e.title)
                if e.intro:
                    self.openings.append(" ".join(e.intro.split()[:4]))

    def prompt_block(self) -> str:
        if not self.entry_titles:
            return ""
        titles = "; ".join(self.entry_titles[-80:])
        opens = "; ".join(f'"{o}…"' for o in self.openings[-40:])
        return (
            "ALREADY IN THIS BOOK — never repeat or lightly rephrase any of "
            f"these entries: {titles}.\n"
            f"Never OPEN an intro the way any earlier one opens: {opens}.\n"
            "Vary technique, form, and register relative to everything above."
        )


def _voice(bible: BookBible) -> str:
    p = profile_of(bible)
    v = bible.voice
    banned = ", ".join(f'"{b}"' for b in v.banned_patterns) or "hype words and jargon"
    persona = p.persona or v.narrator_persona
    style_sheet = ""
    if p.style_sheet:
        style_sheet = "STYLE SHEET (enforce): " + " ".join(p.style_sheet) + "\n"
    return (
        f"You are {persona}, writing '{bible.title}' for {p.audience}. "
        f"Editorial angle: {p.angle or bible.tone}. Voice: {v.tone_register}. "
        f"Write like a top magazine in this genre: confident, specific, sensory "
        f"where it earns it, one clear idea per sentence. Never use these: "
        f"{banned}.\n{style_sheet}{coherence_rules(bible)}"
    )


class _Sidebar(BaseModel):
    kind: str = ""
    heading: str = ""
    body: str = ""


class _SectionDraft(BaseModel):
    intro: str = Field(description="Short lead paragraph for the chapter opener panel, 25-40 words.")
    essay: str = Field(default="", description="Chapter-opener essay when requested; else empty.")
    sidebars: list[_Sidebar] = Field(default_factory=list)
    pull_quote: str = Field(default="", description="One striking line from the essay, when requested.")
    cards: list[UseEntry] = Field(description="The full entries (the workhorse cards).")
    card_photos: list[str] = Field(
        default_factory=list,
        description="One short photo subject per card, same order as cards.")
    quick_uses: list[UseEntry] = Field(description="Short one-line entries for a compact list.")


_SECTION_PROMPT = """Write the content for the "{title}" chapter of the book.

WHAT THIS CHAPTER COVERS: {summary}

{grounding}

{source_policy}

{planned_entries}

{feedback}

{registry}

Produce:
- intro: one lead paragraph, 25-40 words, for the chapter opener panel.
{storyboard_spec}
- cards: EXACTLY {cards} complete {noun}s. For EACH card:
{card_spec}
- card_photos: EXACTLY {cards} short photo subjects (one per card, same order),
  each a concrete scene to photograph (no text, no faces) showing that card's
  finished result.
- quick_uses: EXACTLY {quick} short entries — quick ideas, variations, or
  compact versions related to this chapter. For EACH: a title (2-6 words) and
  an intro that is ONE line, 8-16 words. Leave steps empty.

{safety}

{quality_line} Stay UNDER the length limits — short is better than long.
DISTINCTNESS: no two cards in this chapter (or the book) may overlap in
substance, technique, OR main subject — each must stand alone as its own idea;
vary form and register across the chapter. If you cannot make a card genuinely
distinct and specific, make an existing idea deeper instead of repeating it.
Do not mention any entry total that contradicts the book title or subtitle."""


def _card_spec(profile: EditorialProfile) -> str:
    es = profile.entry_schema
    noun = es.entry_noun
    lines = [
        f"    * title: the {noun}'s name, 2-6 words, specific and concrete.",
        f"    * intro (the headnote): {es.headnote_lo}-{es.headnote_hi} words — "
        "why this one matters, with one vivid, concrete detail. Every card's "
        "intro must open differently.",
    ]
    if not es.requires_materials:
        lines.append(
            "    * what_you_need: leave it EMPTY ([]). This book's entries need "
            "nothing physical — do NOT invent materials to fill the field.")
    elif es.require_quantities:
        lines.append(
            f"    * what_you_need ({es.need_label}): 5-9 items in order of use "
            "(NEVER more than 9 — the printed card has finite room; fold small "
            "pantry staples into one line like \"Salt and pepper\"). "
            "Every FOOD item MUST carry an exact amount (\"2 cups flour\", "
            "\"1 (15-ounce) can\"). Seasonings may read \"Salt, to taste\" and "
            "an oil may read \"Neutral oil, for frying\". List any equipment or "
            "materials (foil, freezer bags, a thermometer) LAST and without an "
            "amount.")
    else:
        lines.append(
            f"    * what_you_need ({es.need_label}): 2-5 CONCRETE, PHYSICAL items "
            "the reader must have in hand, each 1-5 words — something you could "
            "photograph (a tool, object, ingredient, or measurable quantity). "
            "NEVER list an attitude, feeling, or abstraction. BANNED as items: "
            "patience, mindset, consistency, willingness, motivation, awareness, "
            "discipline, a plan, a goal, good lighting, time, commitment. If the "
            f"{noun} needs nothing physical, leave what_you_need EMPTY ([]).")
    lines.append(
        f"    * steps ({es.steps_label}): {es.min_steps}-{es.max_steps} numbered "
        "steps, each ONE action in the imperative, 6-25 words, with exact "
        "temperatures, times, settings, or sensory done-ness cues wherever "
        "they matter (a cue plus a time range beats a bare time). Steps must "
        "instruct only the SAFE method; put any 'never do X' warning in the "
        "caution field, not inside a step.")
    if es.show_meta:
        lines.append(
            "    * serves: the yield, phrased the same way on every card.")
        lines.append(
            "    * time: the time line (e.g. \"20 min prep · 45 min total\").")
    else:
        lines.append("    * serves and time: leave \"\".")
    if es.show_savings:
        lines.append(
            "    * savings: 6-12 words naming what it replaces and a rough "
            "dollar amount.")
    else:
        lines.append("    * savings: leave \"\".")
    lines.append(
        "    * variation: optional one-line riff on the card, 8-16 words, "
        "or \"\".")
    lines.append(
        "    * note: optional make-ahead/storage/practical note, max 20 words, "
        "or \"\".")
    lines.append(
        "    * source_line: when the card adapts something from the source "
        "material, credit it (\"Adapted from …\"); else \"\".")
    lines.append(
        "    * caution: ONLY if there is a real safety point, max 18 words; "
        "otherwise \"\".")
    lines.append("    * number: leave 0 (it is assigned later).")
    return "\n".join(lines)


def _storyboard_spec(profile: EditorialProfile) -> str:
    spec = templates.spec_for(profile.structure_template)
    if not spec.has_opener_essay:
        return "- essay, sidebars, pull_quote: leave empty."
    features = ", ".join(profile.feature_menu) or "a short contextual note"
    return (
        "- essay: the chapter-opener essay, 300-500 words, written like a top "
        "magazine feature lede — grounded in the source material with at least "
        "two specific, concrete details (names, dates, places, numbers) drawn "
        "from it. No throat-clearing; start inside the subject.\n"
        f"- sidebars: 1-2 feature boxes (kinds: {features}); each has kind, a "
        "heading (2-5 words), and a body of 40-80 words with one concrete "
        "detail.\n"
        "- pull_quote: one striking line lifted or distilled from the essay, "
        "under 20 words."
    )


def _source_policy(profile: EditorialProfile) -> str:
    grounded = profile.grounded_content or "facts, history, names, and dates"
    authored = (profile.authored_content
                or f"the {profile.entry_schema.entry_noun}s themselves")
    return (
        f"SOURCE POLICY: {grounded} must trace to the grounding passages — never "
        "assert a fact the passages do not support. Your job is to develop and "
        f"expand the source's real ideas into {authored} at professional depth. "
        "You may author a NEW entry ONLY when it is a distinct, concrete technique "
        "a domain expert would recognize as real, correct, and non-obvious, and it "
        "is consistent with the source's subject. NEVER invent an entry merely to "
        "reach a count; NEVER write an entry about an attitude, mindset, or "
        "feeling; NEVER repeat or lightly reword another entry. When the source is "
        "thin, go DEEPER on each real idea (more specific method, examples, and "
        "detail) rather than adding hollow ones."
    )


def _quality_line(profile: EditorialProfile) -> str:
    noun = profile.entry_schema.entry_noun
    return (
        f"ACQUISITIONS TEST — every {noun} must earn its page: it teaches a "
        "specific, non-obvious, immediately usable thing the reader could not "
        f"guess from the title alone. Cut anything generic. A {noun} that could "
        "appear unchanged in a different book on a different topic is filler — "
        "make it sharper and more specific. Give each one vivid, concrete detail "
        "(a number, name, method, or sensory cue drawn from the subject), never a "
        "restatement of the premise. Realistic to follow exactly as written.")


def _feedback_block(faults: list[str]) -> str:
    """Turn QA faults for this chapter into a revision instruction. Empty when
    there are none, so the first-pass prompt is byte-identical to before."""
    if not faults:
        return ""
    listing = "\n".join(f"  - {f}" for f in faults)
    return (
        "REVISION — a previous draft of THIS chapter had the specific faults "
        "below. Fix EXACTLY these while keeping everything that was already good "
        "(same subjects, same planned entries, same order). Do not introduce new "
        f"problems:\n{listing}"
    )


def _planned_entries_block(section: SectionPlan) -> str:
    if not section.planned_entries:
        return ""
    listing = "\n".join(f"  {i + 1}. {e}" for i, e in
                        enumerate(section.planned_entries))
    return ("PLANNED ENTRIES (realize exactly these, in this order — improve "
            f"them, never replace them with different subjects):\n{listing}")


def _format_passages(passages: list[Passage]) -> str:
    if not passages:
        return ""
    body = "\n\n".join(f"[{p.id}] {p.text}" for p in passages)
    return (
        "GROUNDING PASSAGES (only assert facts supported here; cite ids when unsure):\n"
        f"{body}"
    )


class _Revision(BaseModel):
    essay: str = ""
    intros: list[str] = Field(default_factory=list)

    @field_validator("intros", mode="before")
    @classmethod
    def _coerce_intros(cls, v):
        # Models sometimes emit the list as a JSON string — parse instead of
        # failing the whole revision pass.
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except ValueError:
                pass
            return []
        return v


_REVISE_PROMPT = """You are a line editor. Polish this chapter's essay and card
intros: cut AI-tells and filler, make each intro open differently (vary the
first words), keep every concrete detail, and keep lengths within their limits
(essay 300-500 words; each intro {lo}-{hi} words). Do not change meaning or
invent facts. Return the polished essay (or "" if none was given) and the
intros in the SAME order and count.

ESSAY:
{essay}

CARD INTROS:
{intros}"""


def _revise(draft: _SectionDraft, bible: BookBible) -> _SectionDraft:
    """One cheap self-refine pass over the prose-bearing fields."""
    p = profile_of(bible)
    intros = [c.intro for c in draft.cards]
    if not any(intros) and not draft.essay:
        return draft
    try:
        rev = llm.structured(
            _REVISE_PROMPT.format(
                lo=p.entry_schema.headnote_lo, hi=p.entry_schema.headnote_hi,
                essay=draft.essay or "(none)",
                intros="\n".join(f"[{i}] {t}" for i, t in enumerate(intros)),
            ),
            _Revision, system=_voice(bible),
            model=settings.draft_model, max_tokens=6000,
        )
    except Exception as exc:
        if llm.is_provider_unavailable(exc):
            raise
        log.warning("revision_pass_skipped", err=str(exc)[:160])
        return draft
    if draft.essay and rev.essay.strip():
        draft.essay = rev.essay.strip()
    if len(rev.intros) == len(draft.cards):
        for card, intro in zip(draft.cards, rev.intros):
            if intro.strip():
                card.intro = intro.strip()
    return draft


def write_section(
    section: SectionPlan,
    bible: BookBible,
    *,
    start_number: int,
    safety_key: str = "",
    passages: list[Passage] | None = None,
    list_lead: str = "",
    registry: Registry | None = None,
    feedback: list[str] | None = None,
) -> tuple[Chapter, int]:
    """Generate one section's blocks. Returns (Chapter, next_global_number).

    `feedback` (QA faults for this chapter) turns the call into a targeted
    revision — empty/None keeps the prompt identical to a first-pass draft."""
    del safety_key  # legacy arg; the safety brief now comes from the bible
    passages = passages or []
    profile = profile_of(bible)
    es = profile.entry_schema
    spec = templates.spec_for(profile.structure_template)

    draft = llm.structured(
        _SECTION_PROMPT.format(
            title=section.title, summary=section.summary,
            cards=section.full_card_quota, quick=section.list_quota,
            noun=es.entry_noun,
            card_spec=_card_spec(profile),
            storyboard_spec=_storyboard_spec(profile),
            source_policy=_source_policy(profile),
            planned_entries=_planned_entries_block(section),
            feedback=_feedback_block(feedback or []),
            registry=registry.prompt_block() if registry else "",
            safety=safety.rules_for_bible(bible),
            quality_line=_quality_line(profile),
            grounding=_format_passages(passages),
        ),
        _SectionDraft, system=_voice(bible), max_tokens=12000,
    )
    draft = _revise(draft, bible)

    blocks: list[ContentBlock] = [
        ContentBlock(type=BlockType.section_intro, text=draft.intro.strip())
    ]
    if spec.has_opener_essay and draft.essay.strip():
        blocks.append(ContentBlock(
            type=BlockType.history, heading=section.title,
            text=draft.essay.strip()))
        if draft.pull_quote.strip():
            blocks.append(ContentBlock(
                type=BlockType.pull_quote, text=draft.pull_quote.strip()))

    # Interleave feature boxes into the card run (never as bare final pages).
    sidebar_after = {}
    if draft.sidebars:
        gap = max(2, len(draft.cards) // (len(draft.sidebars) + 1))
        for si, sb in enumerate(draft.sidebars[:2]):
            sidebar_after[(si + 1) * gap] = sb

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
        sb = sidebar_after.get(i + 1)
        if sb and (sb.body or "").strip():
            blocks.append(ContentBlock(
                type=BlockType.sidebar, heading=sb.heading or sb.kind,
                text=sb.body.strip()))

    for q in draft.quick_uses:
        q.number = n
        n += 1
    if draft.quick_uses:
        base = section_eyebrow(section.title)
        lead = list_lead or f"Quick {es.entry_noun} ideas, one line each."
        heading = profile.list_section_heading.format(topic=base)
        blocks.append(ContentBlock(
            type=BlockType.use_list,
            heading=heading,
            text=lead,
            entries=draft.quick_uses))

    if registry is not None:
        ch = Chapter(index=section.index, title=section.title, blocks=blocks)
        registry.add_chapter(ch)
    else:
        ch = Chapter(index=section.index, title=section.title, blocks=blocks)

    log.info("section_written", title=section.title, cards=len(draft.cards),
             quick=len(draft.quick_uses), essay=bool(draft.essay),
             uses=n - start_number)
    return ch, n


_CHART_PROMPT = """Make a reference table for the "{section}" part of \
"{book_title}" — a {genre} for {audience}.

Columns (exactly these, in order): {columns}
Produce EXACTLY {rows} rows. Each cell is 2-6 words. Every row must be accurate,
realistic, and genuinely useful to this book's reader.{savings_note}
Do not inflate any counts or figures."""


def make_chart(section: str, heading: str, columns: list[str], rows: int,
               bible: BookBible) -> ContentBlock:
    p = profile_of(bible)
    savings_note = ""
    note = None
    if p.entry_schema.show_savings:
        savings_note = ("\nWhere a column names a saving, give a rough, honest "
                        "dollar figure.")
        note = "Savings are rough estimates based on regular home use."
    table = llm.structured(
        _CHART_PROMPT.format(
            section=section, book_title=bible.title, genre=p.genre,
            audience=p.audience, columns=", ".join(columns), rows=rows,
            savings_note=savings_note),
        TableModel, system=_voice(bible), max_tokens=2000)
    table.columns = columns
    table.note = note
    return ContentBlock(
        type=BlockType.chart, heading=heading,
        text="A quick reference you will come back to.",
        table=table,
    )


_CHECK_PROMPT = """Make a simple checklist titled "{heading}" for the readers of
"{book_title}" ({audience}), about: {about}

Produce {items} short checklist items, each 4-9 words, a single clear task. Give each a
cadence of "weekly" or "monthly". Keep them safe and easy.
Headings must match the checklist topic exactly."""


def make_checklist(heading: str, about: str, items: int, bible: BookBible) -> ContentBlock:
    p = profile_of(bible)
    cl = llm.structured(
        _CHECK_PROMPT.format(heading=heading, about=about, items=items,
                             book_title=bible.title, audience=p.audience),
        ChecklistModel, system=_voice(bible), max_tokens=2000)
    return ContentBlock(type=BlockType.checklist, heading=heading,
                        text="Tick these off as you go — it keeps things easy.", checklist=cl)


class _Pro(BaseModel):
    lead: str
    signs: list[str]
    reassure: str


_PRO_PROMPT = """Write a short back-matter page titled "{heading}" for
"{book_title}" — a {genre} for {audience} — about {topic}.
Page angle: {angle}
- lead: 2 sentences, 25-40 words.
- signs: 4-6 key points, each 6-16 words, that genuinely serve this page's angle.
- reassure: 1-2 warm closing sentences, 20-35 words.
Keep it calm and plain. Do not mention inflated entry totals."""


def make_pro(topic: str, bible: BookBible) -> dict:
    p = profile_of(bible)
    heading = p.back_matter_heading or "When to Call a Professional"
    pro = llm.structured(
        _PRO_PROMPT.format(
            heading=heading, book_title=bible.title, genre=p.genre,
            audience=p.audience, topic=topic,
            angle=p.back_matter_angle or
            "the closing reference readers of this genre expect"),
        _Pro, system=_voice(bible), max_tokens=1500)
    return {"heading": heading, "lead": pro.lead,
            "signs": pro.signs, "reassure": pro.reassure}
