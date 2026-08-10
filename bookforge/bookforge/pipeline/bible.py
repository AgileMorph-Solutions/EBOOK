"""Stage 1 — Book Bible.

Narrative books: chapter plan for illustrated nonfiction.
Practical guides: profile-driven planning in three steps —

    1. PROFILE (Opus): derive the EditorialProfile (genre, audience, angle,
       persona, safety domain, structural template, labels) from topic+source.
       Nothing is keyword-matched; every editorial decision is generated per
       book, then sanity-checked by a second LLM pass.
    2. OUTLINE (Opus): a two-level outline — parts with a narrative arc,
       evocative chapter titles, and entry-level briefs — sized by the
       template's density model.
    3. CRITIQUE (draft model): an "acquisitions editor" pass revises the
       outline before any prose is written (the cheapest place to fix
       structure).
"""
from __future__ import annotations

import json
import math
import re
import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

from bookforge.clients import llm
from bookforge.config import settings
from bookforge.layout import theme
from bookforge.pipeline import templates
from bookforge.pipeline.content_guardrails import (
    expand_sections_to_count, parse_promised_count, planned_use_count,
    propagate_promised_count, reconcile_promise, rewrite_promised_number,
    shrink_sections_to_count,
)
from bookforge.pipeline.page_limits import (
    estimate_guide_pages,
    estimate_rendered_pages,
    max_planned_entries,
    max_sections_for_target,
    page_range_prompt_suffix,
    reconcile_plan_to_page_band,
)
from bookforge.pipeline.plan_scope import PlanScope, build_plan_scope
from bookforge.schemas import (
    ArtStyle, BookBible, DesignKit, EditorialProfile, EntrySchema, HowToPage,
    SafetyCard, SectionPlan, SourceDoc, SpecialPlan, VoiceStyle,
)

log = structlog.get_logger(__name__)

_SYSTEM = """You are a veteran commissioning editor and art director at a premium \
illustrated-nonfiction publisher. You design books that read like they were \
written by an award-winning author and look like they were art-directed by a \
top studio. You are precise, opinionated, and allergic to generic AI phrasing."""

_NARRATIVE_PROMPT = """Design the Book Bible for an illustrated nonfiction book.

TOPIC: {topic}
TARGET LENGTH: ~{pages} pages, chapter-wise, richly illustrated.

SOURCE MATERIAL (the book must be grounded in this — do not invent beyond it):
\"\"\"
{source}
\"\"\"

Produce:
- A compelling title + subtitle.
- The intended audience and tone.
- An ART STYLE guide so all images share one coherent look: medium, \
palette, lighting, era, composition rules, a negative_prompt of things to avoid, \
and a style_suffix string that will be appended to EVERY image prompt. \
Plan a hero image every 1–2 pages (~{pages_image_min} images minimum for the book).
- A VOICE guide: narrator persona, register, POV, pacing, vocabulary, and a \
banned_patterns list of clichés/AI-tells to never use.
- A chapter plan: each chapter has a title, one-paragraph summary, 3-6 concrete \
beats, and a page_budget. The page_budgets should sum to roughly {pages}.
- recurring_motifs that tie the book together.

{page_limit_note}

Be specific and editorial. No filler."""


# --------------------------------------------------------------------------- #
# Step 1 — editorial profile (Opus, zero keywords)
# --------------------------------------------------------------------------- #
class _SafetyCardDraft(BaseModel):
    pair: str
    why: str


class _ProfileDraft(BaseModel):
    """LLM-decided editorial brief; density knobs come from the template spec."""

    genre: str
    audience: str
    angle: str
    persona: str
    safety_domain: str = "general_home"  # chemical | pest | food | general_home | none
    structure_template: str = "essay_entries"
    entry_noun: str = "entry"
    need_label: str = "What You Need"
    steps_label: str = "Steps"
    requires_materials: bool = True   # False for habit/mindset guides (no "What You Need")
    require_quantities: bool = False
    show_savings: bool = False
    show_meta: bool = False
    min_steps: int = 3
    max_steps: int = 6
    headnote_lo: int = 20
    headnote_hi: int = 40
    cover_kicker: str = ""
    cover_foot: str = ""
    back_matter_heading: str = ""
    back_matter_angle: str = ""
    grounded_content: str = ""
    authored_content: str = ""
    style_sheet: list[str] = Field(default_factory=list)
    # --- generated safety brief (topic-specific; no static canon exists) ---
    safety_rules: str = ""
    safety_cards: list[_SafetyCardDraft] = Field(default_factory=list)
    danger_patterns: list[list[str]] = Field(default_factory=list)


_PROFILE_PROMPT = """You are the publisher deciding the editorial brief for a new
practical nonfiction book. The client's TITLE/TOPIC wording is the contract —
honor it literally (a "Cookbook" must be a real cookbook with complete recipes,
whatever the source text contains; a "Field Guide" must identify things; etc.).

TOPIC (the client's request): {topic}

SOURCE MATERIAL EXCERPT:
\"\"\"
{excerpt}
\"\"\"

Decide the brief. Derive EVERYTHING from this topic and source — do not carry
assumptions from other books, and do not default to any particular audience,
money angle, or domain unless the topic itself says so.

- genre: the kind of book the title promises, in 1-3 words.
- audience: who will actually buy and use this book.
- angle: the editorial angle in one sentence — what makes THIS book distinct.
- persona: the author persona a top publisher would hire for exactly this book.
- safety_domain: what the reader physically does while using the book —
  "food" (cooking/eating), "chemical" (cleaning products), "pest" (pest
  control), "general_home" (household tasks), or "none" (no physical risk).
- structure_template: pick ONE from this menu (by structure, not by topic):
{template_menu}
- entry_noun / need_label / steps_label: what the repeating unit is called and
  how its parts are labeled (e.g. recipes need "Ingredients" and "Method" with
  serves/time; repair tips need "What You Need"/"Steps").
- requires_materials: does an entry in this book need PHYSICAL things the reader
  must have in hand (ingredients, tools, supplies)? true for recipes/projects/
  repairs; FALSE for habit, mindset, behaviour, finance, or advice books where an
  entry needs nothing physical — those must NOT show a "What You Need" list.
- require_quantities: true when every need-list item must carry an exact amount
  (always true for recipes). Only meaningful when requires_materials is true.
- show_savings: true ONLY if the topic is explicitly about saving money.
- show_meta: true when entries need a yield/time strip (recipes, projects).
- min_steps/max_steps and headnote_lo/headnote_hi (word range for the entry
  intro/headnote): size them to the genre.
- cover_kicker / cover_foot: short cover strap lines a top publisher would use
  for this exact book. Never generic.
- back_matter_heading / back_matter_angle: the back-matter page this genre's
  readers expect (e.g. substitutions & pantry notes; resources; when to call a
  professional) — derived from the topic.
- grounded_content: which statements MUST come from the source (history, dates,
  names, claims).
- authored_content: what the writer DEVELOPS from the source to professional
  depth (e.g. the complete recipes themselves, fully worked from the source's
  dishes and era). Frame this as developing/expanding the source's real ideas —
  the writer may add a genuinely new, concrete, expert-recognizable entry when it
  fits the subject, but must NEVER invent hollow entries to reach a count, and
  never write an entry about an attitude or feeling. Depth over volume.
- style_sheet: 3-6 term-consistency rules for this book (one canonical name per
  ingredient/tool/proper noun, unit style, etc.).
- SAFETY BRIEF, written fresh for THIS topic (there is no pre-made canon —
  reason from what the reader will physically do):
  * safety_rules: the non-negotiable safety rules a professional editor in this
    exact domain would force every writer to obey, written as direct prompt
    instructions ("SAFETY RULES (obey exactly): - …"). Cover the real risks of
    this topic with concrete thresholds where they exist. Empty if
    safety_domain is "none".
  * safety_cards: exactly 4 "never do this" pairs for the book's Safety First
    page — each has pair (the two things never to combine, or the act never to
    do, 3-7 words) and why (one plain sentence). Empty if no physical risk.
  * danger_patterns: 5-10 automated-scan patterns; each is a list of 2-3
    lowercase substrings that, appearing together in one instruction, would
    indicate something unsafe for this topic. Empty if no physical risk."""


_PROFILE_CHECK_PROMPT = """Review this editorial brief for a book. Fix anything
inconsistent with the topic — wrong safety_domain for what the reader physically
does, an audience or angle the topic doesn't support, labels that don't fit the
genre, a structure_template that fights the title's promise, or leftover
assumptions that belong to some other book. Return the corrected brief (or the
same brief if it is already right).

TOPIC: {topic}

BRIEF:
{brief}"""


def _profile_from_draft(d: _ProfileDraft) -> EditorialProfile:
    spec = templates.spec_for(d.structure_template)
    safety_domain = d.safety_domain if d.safety_domain in (
        "chemical", "pest", "food", "general_home", "none") else "general_home"
    return EditorialProfile(
        genre=d.genre,
        audience=d.audience,
        angle=d.angle,
        persona=d.persona,
        safety_domain=safety_domain,  # type: ignore[arg-type]
        entry_schema=EntrySchema(
            entry_noun=d.entry_noun or "entry",
            need_label=d.need_label or "What You Need",
            steps_label=d.steps_label or "Steps",
            requires_materials=d.requires_materials,
            require_quantities=d.require_quantities and d.requires_materials,
            show_savings=d.show_savings,
            show_meta=d.show_meta,
            min_steps=max(1, d.min_steps),
            max_steps=max(d.min_steps, d.max_steps),
            headnote_lo=max(10, d.headnote_lo),
            headnote_hi=max(d.headnote_lo, d.headnote_hi),
        ),
        structure_template=spec.key,  # type: ignore[arg-type]
        narrative_share=spec.narrative_share,
        pages_per_entry=spec.pages_per_entry,
        tiers=spec.tiers,
        quick_list_weight=spec.quick_list_weight,
        image_density=spec.image_density,  # type: ignore[arg-type]
        feature_menu=list(spec.feature_menu),
        cover_kicker=d.cover_kicker,
        cover_foot=d.cover_foot,
        back_matter_heading=d.back_matter_heading or "Notes & Resources",
        back_matter_angle=d.back_matter_angle,
        grounded_content=d.grounded_content,
        authored_content=d.authored_content,
        style_sheet=d.style_sheet,
        safety_rules=d.safety_rules,
        safety_cards=[SafetyCard(pair=c.pair, why=c.why) for c in d.safety_cards],
        danger_patterns=[p for p in d.danger_patterns if p],
    )


def derive_profile(
    source: SourceDoc, *, overrides: dict | None = None,
) -> EditorialProfile:
    """Derive the per-book editorial brief. Planning runs on the strongest
    model (CONTENT_MODEL) — it is the highest-leverage call in the pipeline —
    with a cheap LLM self-check instead of any keyword table."""
    excerpt = source.text[:12000]
    draft = llm.structured(
        _PROFILE_PROMPT.format(
            topic=source.topic, excerpt=excerpt,
            template_menu=templates.menu_text(),
        ),
        _ProfileDraft, system=_SYSTEM,
        model=settings.content_model, max_tokens=3000,
    )
    try:
        checked = llm.structured(
            _PROFILE_CHECK_PROMPT.format(
                topic=source.topic, brief=draft.model_dump_json(indent=1)),
            _ProfileDraft, system=_SYSTEM,
            model=settings.draft_model, max_tokens=3000,
        )
        draft = checked
    except Exception as exc:  # self-check is best-effort polish
        if llm.never_swallow(exc):
            raise
        log.warning("profile_check_skipped", err=str(exc)[:160])

    profile = _profile_from_draft(draft)
    if overrides:
        profile = profile.model_copy(update={
            k: v for k, v in overrides.items() if hasattr(profile, k)
        })
    log.info("profile_derived", genre=profile.genre,
             template=profile.structure_template, audience=profile.audience,
             safety_domain=profile.safety_domain,
             entry_noun=profile.entry_schema.entry_noun)
    return profile


# --------------------------------------------------------------------------- #
# Per-book art direction — a bespoke DesignKit so no two books look alike.
# --------------------------------------------------------------------------- #
class _DesignDraft(BaseModel):
    mood: str = ""
    paper: str = ""
    panel: str = ""
    panel_dark: str = ""
    ink: str = ""
    accent: str = ""
    deep: str = ""
    gold: str = ""
    muted: str = ""
    onpaper_soft: str = ""
    savings: str = "#2E7D5B"
    caution: str = "#B5651D"
    danger: str = "#B3261E"
    display_font: str = ""   # a family NAME from the display menu
    body_font: str = ""      # a family NAME from the body menu
    cond_font: str = ""      # a family NAME from the cond menu
    cover_art_direction: str = ""
    motif: str = ""


_DESIGN_PROMPT = """You are the ART DIRECTOR giving this book a BESPOKE visual
identity — it must look unmistakably like THIS topic, never a generic template.
Two books on different topics must never look alike.

TOPIC: {topic}
GENRE / EDITORIAL ANGLE: {genre} — {angle}
AUDIENCE: {audience}

Design a cohesive, cinematic, PRINT-grade system:
- mood: 2-4 words for the visual mood (reason from the subject, e.g. "clinical,
  energetic, modern-athletic" for fitness; "warm, rustic, heritage" for cooking).
- PALETTE (hex, "#RRGGBB"). Choose colours that BELONG to this topic — never a
  default cream+teal:
  * paper — the page background (usually a near-white or a soft tinted ground; may
    be bold if the topic earns it).
  * panel — a subtle tint of paper for light boxes.
  * panel_dark — a deep, rich shade for dark step boxes.
  * ink — body text; must be very dark on paper.
  * accent — the primary brand/heading colour.
  * deep — the darkest brand shade (dividers, bands).
  * gold — a secondary/metallic accent (rules, step numbers).
  * muted — eyebrows/captions.
  * onpaper_soft — a soft body variant.
  CONTRAST IS MANDATORY (the design is rejected otherwise): ink-on-paper must be
  near-black-on-light (ratio >= 6:1); white-on-panel_dark >= 4.5:1; accent-on-
  paper >= 3:1; ink-on-panel >= 4.5:1.
- FONTS — choose ONE family per role, by EXACT NAME, from this menu only:
{font_menu}
  Pair them for the topic's mood: a distinctive display + a highly legible body +
  a clean condensed label face. Return display_font, body_font, cond_font.
- cover_art_direction: one sentence directing cover + interior photography for
  this topic (subject, lighting, palette, composition; no text, no faces).
- motif: a small recurring visual motif for the book."""


def _stack_for(role: str, name: str) -> str:
    """Map a chosen font family NAME to its css stack, defaulting per role."""
    lib = theme.FONT_LIBRARY.get(role, {})
    if name in lib:
        return lib[name]
    return {"display": theme._GUIDE_FONTS["display"],
            "body": theme._GUIDE_FONTS["body"],
            "cond": theme._GUIDE_FONTS["cond"]}[role]


def derive_design(source: SourceDoc, profile: EditorialProfile) -> DesignKit | None:
    """Plan a bespoke per-book DesignKit (Opus). Returns None if generation fails
    or the palette can't pass contrast validation — the caller then falls back to
    the legacy preset menu, so this can never break a book."""
    try:
        d = llm.structured(
            _DESIGN_PROMPT.format(
                topic=source.topic, genre=profile.genre,
                angle=profile.angle, audience=profile.audience,
                font_menu=theme.design_font_menu(),
            ),
            _DesignDraft, system=_SYSTEM,
            model=settings.content_model, max_tokens=1500,
        )
    except Exception as exc:
        if llm.never_swallow(exc):
            raise
        log.warning("design_kit_skipped", err=str(exc)[:160])
        return None

    kit = DesignKit(
        paper=d.paper, panel=d.panel, panel_dark=d.panel_dark, ink=d.ink,
        accent=d.accent, deep=d.deep, gold=d.gold, muted=d.muted,
        onpaper_soft=d.onpaper_soft, savings=d.savings, caution=d.caution,
        danger=d.danger,
        display=_stack_for("display", d.display_font),
        body=_stack_for("body", d.body_font),
        cond=_stack_for("cond", d.cond_font),
        mood=d.mood, cover_art_direction=d.cover_art_direction, motif=d.motif,
    )
    # Reject (→ preset fallback) if the palette fails contrast.
    if theme.kit_from_design(kit) is None:
        log.warning("design_kit_low_contrast", mood=d.mood,
                    ink=d.ink, paper=d.paper, accent=d.accent)
        return None
    log.info("design_kit_derived", mood=d.mood, display=d.display_font,
             body=d.body_font, accent=d.accent, paper=d.paper)
    return kit


# --------------------------------------------------------------------------- #
# Step 2 — outline (two-level, template-driven)
# --------------------------------------------------------------------------- #
class _EntryBrief(BaseModel):
    title: str
    brief: str = ""


class _BaseSection(BaseModel):
    title: str
    summary: str
    divider_subject: str = ""
    part_title: str = ""
    entries: list[_EntryBrief] = Field(default_factory=list)


class _SpecialDraft(BaseModel):
    """Looser LLM output — coerced to SpecialPlan after validation."""
    after_section: int = 1
    kind: str = "checklist"
    heading: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: int = 6
    about: str = ""
    items: int | list[str] = 10

    @model_validator(mode="after")
    def _coerce(self) -> "_SpecialDraft":
        kind = self.kind if self.kind in ("chart", "checklist") else "checklist"
        heading = self.heading.strip()
        if not heading:
            heading = "Reference Chart" if kind == "chart" else "Routine Checklist"
        about = self.about
        items = self.items
        if isinstance(items, list):
            if not about:
                about = " ".join(str(x) for x in items[:4])
            items = _clamp(8, 12, len(items))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "heading", heading)
        object.__setattr__(self, "about", about)
        object.__setattr__(self, "items", int(items))
        return self

    def to_plan(self) -> SpecialPlan:
        return SpecialPlan(
            after_section=self.after_section,
            kind=self.kind,  # type: ignore[arg-type]
            heading=self.heading,
            columns=self.columns or ["Item", "Method", "Notes"],
            rows=self.rows,
            about=self.about,
            items=self.items if isinstance(self.items, int) else 10,
        )


def _coerce_nested(v, model: type[BaseModel]) -> BaseModel:
    """LLM occasionally emits nested objects as JSON strings — parse them."""
    if isinstance(v, model):
        return v
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return v  # type: ignore[return-value]
    if isinstance(v, dict):
        return model.model_validate(v)
    return v  # type: ignore[return-value]


class _PracticalBibleDraft(BaseModel):
    title: str
    subtitle: str
    audience: str = ""
    tone: str = ""
    art_style: ArtStyle
    voice: VoiceStyle
    base_sections: list[_BaseSection] = Field(min_length=1)
    how_to: HowToPage
    theme_name: str = ""
    list_lead: str = ""
    pro_topic: str = ""
    cover_subject: str = ""
    specials: list[_SpecialDraft] = Field(default_factory=list)

    @field_validator("art_style", mode="before")
    @classmethod
    def _coerce_art_style(cls, v):
        return _coerce_nested(v, ArtStyle)

    @field_validator("voice", mode="before")
    @classmethod
    def _coerce_voice(cls, v):
        return _coerce_nested(v, VoiceStyle)

    @field_validator("how_to", mode="before")
    @classmethod
    def _coerce_how_to(cls, v):
        return _coerce_nested(v, HowToPage)


_GUIDE_SYSTEM_TPL = """You are a veteran commissioning editor at a premium \
practical-nonfiction publisher, designing a {genre} for {audience}. \
Editorial angle: {angle}. The book must read as if written by {persona} — \
with the structure, headings, and features a top publication in this genre \
would use. You are precise, opinionated, allergic to generic AI phrasing, and \
allergic to off-topic filler: every chapter must serve the {genre}'s promise."""


_GUIDE_PROMPT_TPL = """Design the Book Bible for this {genre}.

TOPIC (the client's request — its wording is the contract): {topic}
TARGET LENGTH: ~{pages} pages total.

SOURCE MATERIAL:
\"\"\"
{source}
\"\"\"

SOURCE POLICY: the source is the book's subject-matter backbone
({grounded_content}). You develop and expand the source's real ideas into
{authored_content} at professional depth. Plan a chapter only around genuinely
distinct, substantive material a domain expert would respect — when the source is
thin, cover fewer ideas MORE deeply rather than manufacturing hollow chapters or
padding a count. Never plan a chapter, part, or entry that is filler, an
attitude/mindset, or a near-duplicate of another.

STRUCTURE (follow this template): {spine}.
Chapter rhythm: {rhythm}.

Produce:
- title + subtitle that honor the client's topic wording. If the title names a \
count (e.g. 100 recipes), plan only as many {entry_noun}s as fit within the \
hard page maximum ({pages} pages — never exceed it). State the fitted count \
honestly in title/subtitle when the source asks for more than fits.
- audience: {audience}. tone: fitting the angle — {angle}.
- art_style: one coherent photographic/illustration look for the whole book,
  matched to the topic and era; negative_prompt must include "no text, no
  clutter, no faces". Plan for a photo every 1–2 pages — divider portraits,
  card hero shots, and step photos wherever they teach.
- voice: persona, register, POV, pacing, vocabulary, and banned_patterns
  (clichés/AI-tells to never use).
- base_sections: EXACTLY {base_lo}-{base_hi} chapters. Organize them into 2-5
  named PARTS with a narrative arc — the table of contents should read like a
  story that builds, and each part_title names its act. For EACH chapter give:
    * title: evocative, specific, publisher-grade (never mechanical patterns
      like "Topic — Essentials"); it must accurately describe the chapter.
      DISPLAY CONSTRAINT: it is set as large type in a narrow print sidebar —
      keep it to at most 6 words / 48 characters, and avoid any single word
      longer than 14 characters.
    * summary: one paragraph on what the chapter covers and why it earns its
      place in the arc.
    * part_title: the part it belongs to (same string for chapters that share
      a part).
    * divider_subject: a CLEAN vertical portrait photo scene for the chapter
      opener panel — one centered subject with margin on all sides, matched to
      this chapter's content. Never leave it empty.
    * entries: EXACTLY {cards_per_section} planned {entry_noun}s, each a distinct,
      concrete, expert-recognizable idea — with a title (2-6 words, a real
      {entry_noun} specific to this chapter) and a one-line brief naming the
      specific technique/insight it delivers. No two entries in the BOOK may
      overlap in substance OR subject; none may be an attitude/mindset/"framing"
      or a restatement of the premise. If you cannot fill the count with genuinely
      distinct, substantive entries, say so in the brief rather than padding.
- how_to: heading, lead (25-40 words), exactly 4 bullet points for the welcome
  page — written for THIS book's reader. EACH bullet 10-18 words; they are set
  in fixed-height feature cards on one page, so long bullets get cut.
- theme_name: pick ONE design preset from this menu by mood fit:
{theme_menu}
- list_lead: one sentence introducing the short quick-{entry_noun} list pages.
- pro_topic: a short phrase for the back-matter page "{back_matter_heading}"
  ({back_matter_angle}).
- cover_subject: a photo prompt for the cover that captures the angle. No text,
  no faces.
- specials: 2-3 special reference pages this genre's readers expect (charts =
  the reference tables of the genre, e.g. substitutions, conversions,
  temperatures, seasonal timing; checklists = routines). Each has
  after_section (1-based chapter index), kind ("chart" or "checklist"),
  heading, and for charts: columns (3 strings) + rows (5-8); for checklists:
  about + items (8-12).{savings_note}

{page_limit_note}

Use correct spelling everywhere. Every chapter must reflect the source and the
angle, never generic filler."""


_CRITIQUE_SYSTEM = """You are a sharp acquisitions editor at a major publishing \
house reviewing a book outline before contract. You kill filler, sharpen \
titles, and protect the title's promise to the reader."""

_CRITIQUE_PROMPT = """Review this outline for "{title}" — a {genre} for {audience}
(~{pages} pages). Would this table of contents survive a bookstore buyer?

Check for and FIX:
- chapters that are filler, overlap another chapter, or drift off the topic's
  promise (a {genre} chapter must deliver {entry_noun}s worth buying);
- mechanical or vague chapter titles — make each specific and evocative;
- a missing or broken narrative arc across the parts;
- planned entries that repeat one another or don't belong to their chapter;
- missing divider_subject photo scenes.

Return the COMPLETE corrected list of chapters (same schema: title, summary,
part_title, divider_subject, entries). Improve, never pad: if a chapter or an
entry is filler or a duplicate and cannot be made genuinely substantive, CUT it
(you may return fewer chapters/entries) rather than keep a hollow one — a tighter,
sharper book beats a padded one.

OUTLINE:
{outline}"""


class _OutlineRevision(BaseModel):
    base_sections: list[_BaseSection] = Field(min_length=1)


def _clamp(lo: int, hi: int, v: int) -> int:
    return max(lo, min(hi, v))


# Legacy density (tips_cards): one legacy "use card" fills ~1 page and a
# quick-tip list runs ~6 tips/page; a dense section lands near ~14 pages.
_PAGES_PER_SECTION = 14


def _base_section_target(target_pages: int, tiers: int) -> int:
    """Legacy tips_cards sizing (unchanged behavior)."""
    want = max(4, round(target_pages / (_PAGES_PER_SECTION * tiers)))
    ceiling = settings.max_base_sections
    return min(want, ceiling) if ceiling and ceiling > 0 else want


def _section_quotas(target_pages: int, num_sections: int) -> tuple[int, int, int, int]:
    """Legacy tips_cards quotas (unchanged behavior)."""
    pages_per = max(8, (target_pages + num_sections - 1) // max(1, num_sections))
    full_cards = _clamp(7, 11, round(pages_per * 0.72))
    list_quota = _clamp(24, 38, round(pages_per * 2.5))
    image_quota = min(3, full_cards)
    return full_cards, list_quota, image_quota, pages_per


class _Density(BaseModel):
    base_sections: int
    cards_per_section: int
    list_quota: int
    image_quota: int
    page_budget: int


def _image_quota_for_section(
    cards: int,
    page_budget: int,
    profile: EditorialProfile,
) -> int:
    """Target ~1 hero image every 1–2 pages per section."""
    if profile.image_density == "every_entry":
        return cards
    return max(1, min(cards, round(page_budget / 1.5)))


def _guide_density(
    target_pages: int,
    profile: EditorialProfile,
    *,
    promised: int | None = None,
    min_sections: int = 0,
) -> _Density:
    """Template-driven density: fewer, deeper entries for entry-rich genres.

    A 350-page essay_entries cookbook lands near 20-24 chapters × ~6 recipes
    (≈130-150 entries) instead of the legacy 1,176 tips.
    """
    spec = templates.spec_for(profile.structure_template)

    if target_pages <= 15:
        # Smoke / pamphlet: cap ALL templates (including tips_cards) so a
        # 5-page request never balloons to 40+ cards / 100+ pages.
        avail = max(1, target_pages - 2)
        cards = max(2, min(3, round(avail / 3)))
        n = max(1, min(3, round(avail / 2)))
        ceiling = settings.max_base_sections
        if ceiling and ceiling > 0:
            n = min(n, ceiling)
        pb = max(1, round(avail / max(1, n)))
        # Honor every_entry even in pamphlet mode; otherwise cap at 1/section.
        if profile.image_density == "every_entry":
            image_quota = cards
        else:
            image_quota = min(1, cards)
        if promised and promised > cards:
            cards = max(cards, math.ceil(promised / max(1, n)))
            if n == 1:
                cards = max(cards, promised)
            if profile.image_density == "every_entry":
                image_quota = cards
        return _Density(
            base_sections=n, cards_per_section=cards, list_quota=0,
            image_quota=image_quota, page_budget=pb,
        )

    if spec.key == "tips_cards":
        tiers = max(1, profile.tiers)
        n = _base_section_target(target_pages, tiers)
        fc, lq, iq, pb = _section_quotas(target_pages, n * tiers)
        iq = _image_quota_for_section(fc, pb, profile) if profile.image_density != "every_entry" else iq
        return _Density(base_sections=n, cards_per_section=fc,
                        list_quota=lq, image_quota=iq, page_budget=pb)

    from bookforge.pipeline.feasibility import MAX_CARDS_PER_SECTION

    fixed_overhead = 9  # cover, title, how-to, safety, TOC, specials, back matter
    avail = max(12, target_pages - fixed_overhead)
    ppe = max(0.5, profile.pages_per_entry)
    factor = 0.9 if profile.image_density == "every_entry" else 0.75
    per_section_overhead = 1.0 + (1.6 if spec.has_opener_essay else 0.4)
    list_pages = max(0.0, profile.quick_list_weight) * 1.2
    if min_sections > 0:
        n = min_sections
        content_avail = max(1, avail - n * per_section_overhead)
        cards = max(3, min(
            MAX_CARDS_PER_SECTION,
            math.ceil(content_avail / max(0.5, n * ppe * factor)),
        ))
    else:
        cards = 6 if profile.pages_per_entry >= 1.5 else 8
        pages_per_section = per_section_overhead + cards * ppe + list_pages
        n = max(4, round(avail / pages_per_section))
    pages_per_section = per_section_overhead + cards * ppe + list_pages
    ceiling = settings.max_base_sections
    if ceiling and ceiling > 0:
        n = min(n, ceiling)
    list_quota = round(12 * max(0.0, profile.quick_list_weight))
    if list_quota < 6:
        list_quota = 0
    override = settings.image_density_override.strip().lower()
    if override in ("sparse", "standard", "every_entry"):
        profile.image_density = override  # type: ignore[assignment]
    pb = round(pages_per_section)
    image_quota = _image_quota_for_section(cards, pb, profile)
    return _Density(
        base_sections=n, cards_per_section=cards, list_quota=list_quota,
        image_quota=image_quota, page_budget=pb,
    )


def _estimate_density_pages(
    density: _Density, tiers: int, *, pamphlet: bool,
) -> int:
    num_sections = density.base_sections * max(1, tiers)
    overhead = (2 if pamphlet else 9) + (0 if pamphlet else 4)
    return overhead + num_sections * density.page_budget


def _cap_density(
    density: _Density,
    target_pages: int,
    profile: EditorialProfile,
    *,
    pamphlet: bool,
    promised: int | None = None,
) -> _Density:
    """Ensure density planning cannot exceed the target page count."""
    plan_cap = max(1, target_pages)
    tiers = max(1, profile.tiers)
    d = density.model_copy()
    min_cards = (
        math.ceil(promised / max(1, d.base_sections))
        if promised and pamphlet else 0
    )
    while _estimate_density_pages(d, tiers, pamphlet=pamphlet) > plan_cap:
        if d.list_quota > 0:
            d.list_quota = 0
            continue
        if d.cards_per_section > 2 and d.cards_per_section > min_cards:
            d.cards_per_section -= 1
            continue
        if d.base_sections > 1 and (
            not promised
            or d.base_sections * max(d.cards_per_section, min_cards) > promised
        ):
            d.base_sections -= 1
            if promised and pamphlet:
                min_cards = math.ceil(promised / max(1, d.base_sections))
            continue
        if d.page_budget > 1:
            d.page_budget -= 1
            continue
        break
    if promised and pamphlet:
        per = math.ceil(promised / max(1, d.base_sections))
        if d.cards_per_section < per:
            d = d.model_copy(update={"cards_per_section": per})
    return d


def _cap_sections_to_page_ceiling(
    sections: list[SectionPlan],
    target_pages: int,
    profile: EditorialProfile,
    *,
    pamphlet: bool,
    min_entries: int = 0,
    min_sections: int = 0,
    specials: list | None = None,
) -> list[SectionPlan]:
    """Trim per-section budgets to fit the planning target without mega-merges.

    Never silently shrinks below `min_entries` (the promised / user-accepted
    count). Never merges all quotas into one section — that caused EmptyBookError
    and tiny PDFs. Drop excess *sections* from the end only when shell overhead
    still exceeds the page budget after per-section budget trim.
    """
    from bookforge.pipeline.feasibility import MAX_CARDS_PER_SECTION
    from bookforge.pipeline.page_limits import allowed_page_range

    _, hi = allowed_page_range(target_pages)
    plan_cap = hi
    planned = planned_use_count(sections)
    # Only shrink entry count when the caller did NOT lock a minimum (legacy /
    # pamphlet paths). With min_entries set, keep the promise.
    if min_entries <= 0:
        max_entries = max_planned_entries(
            plan_cap, profile, len(sections) or 1, pamphlet=pamphlet,
        )
        if planned > max_entries:
            sections = shrink_sections_to_count(sections, max_entries)
            log.info(
                "sections_capped_to_page_ceiling",
                max_entries=max_entries,
                planned=planned_use_count(sections),
            )
    elif planned < min_entries:
        sections = expand_sections_to_count(sections, min_entries)
        log.info(
            "sections_expanded_to_min_entries",
            min_entries=min_entries,
            planned=planned_use_count(sections),
        )

    out = [s.model_copy(deep=True) for s in sections]
    for s in out:
        if s.full_card_quota > MAX_CARDS_PER_SECTION:
            # Redistribute overflow into new sibling sections rather than one blob.
            overflow = s.full_card_quota - MAX_CARDS_PER_SECTION
            s.full_card_quota = MAX_CARDS_PER_SECTION
            if s.planned_entries and len(s.planned_entries) > MAX_CARDS_PER_SECTION:
                keep = s.planned_entries[:MAX_CARDS_PER_SECTION]
                rest = s.planned_entries[MAX_CARDS_PER_SECTION:]
                s.planned_entries = keep
            else:
                rest = []
            s.image_quota = min(s.image_quota, s.full_card_quota)
            while overflow > 0:
                chunk = min(MAX_CARDS_PER_SECTION, overflow)
                overflow -= chunk
                chunk_entries = rest[:chunk]
                rest = rest[chunk:]
                out.append(SectionPlan(
                    index=len(out) + 1,
                    title=f"{s.title} (continued)",
                    summary=s.summary,
                    full_card_quota=chunk,
                    list_quota=0,
                    image_quota=chunk if s.image_quota else 0,
                    page_budget=max(1, s.page_budget),
                    planned_entries=chunk_entries,
                    part_index=s.part_index,
                    part_title=s.part_title,
                ))
        if s.image_quota > s.full_card_quota:
            s.image_quota = s.full_card_quota

    # Re-index after possible splits.
    for i, s in enumerate(out, start=1):
        s.index = i

    # Trim to page ceiling — never drop below min_sections when TOC is locked.
    floor_sections = max(1, min_sections) if min_sections > 0 else 1
    while (
        len(out) > floor_sections
        and estimate_rendered_pages(
            out, profile, specials=specials, pamphlet=pamphlet,
        ) > plan_cap
    ):
        last = out[-1]
        remaining = planned_use_count(out[:-1])
        if min_entries > 0 and remaining < min_entries:
            break
        out.pop()
        log.info(
            "section_dropped_for_page_ceiling",
            dropped=last.title,
            remaining_sections=len(out),
            remaining_entries=remaining,
        )

    while estimate_rendered_pages(
        out, profile, specials=specials, pamphlet=pamphlet,
    ) > plan_cap:
        reduced = False
        for s in sorted(out, key=lambda x: x.list_quota, reverse=True):
            if s.list_quota > 0:
                s.list_quota -= 1
                reduced = True
                break
        if reduced:
            continue
        for s in sorted(out, key=lambda x: x.full_card_quota, reverse=True):
            if min_entries > 0 and planned_use_count(out) <= min_entries:
                break
            if s.full_card_quota > 2:
                s.full_card_quota -= 1
                reduced = True
                break
        if reduced:
            continue
        for s in sorted(out, key=lambda x: x.page_budget, reverse=True):
            if s.page_budget > 1:
                s.page_budget -= 1
                reduced = True
                break
        if not reduced:
            break

    for i, s in enumerate(out, start=1):
        s.index = i
    return out


def _normalize_narrative_budgets(
    chapters: list,
    target_pages: int,
) -> None:
    """Scale chapter page_budgets to the planning target."""
    if not chapters:
        return
    plan = max(1, target_pages)
    total = sum(c.page_budget for c in chapters)
    if total <= 0:
        per = max(1, plan // len(chapters))
        for ch in chapters:
            ch.page_budget = per
        return
    if total == plan:
        return
    scale = plan / total
    for ch in chapters:
        ch.page_budget = max(1, round(ch.page_budget * scale))


def _expand_sections(
    base_sections: list[_BaseSection],
    density: _Density,
    profile: EditorialProfile,
) -> list[SectionPlan]:
    """Expand base chapters into SectionPlans (tiered only for tips_cards)."""
    noun = profile.entry_schema.entry_noun
    tier_names = profile.tier_names[:max(1, profile.tiers)] or ["Essentials"]
    expanded: list[SectionPlan] = []
    part_titles: list[str] = []
    idx = 1
    for base in base_sections:
        if base.part_title and base.part_title not in part_titles:
            part_titles.append(base.part_title)
        part_index = (part_titles.index(base.part_title) + 1
                      if base.part_title else 0)
        for ti, tier in enumerate(tier_names):
            if profile.tiers <= 1:
                title, summary = base.title, base.summary
            elif ti == 0:
                title = f"{base.title} — {tier}"
                summary = base.summary
            else:
                title = f"{base.title} — {tier}"
                summary = f"More {base.title.lower()} {noun}s in the same spirit."
            planned = ([f"{e.title} — {e.brief}".rstrip(" —")
                        for e in base.entries] if ti == 0 else [])
            expanded.append(SectionPlan(
                index=idx, title=title, summary=summary,
                divider_subject=base.divider_subject,
                part_index=part_index, part_title=base.part_title,
                planned_entries=planned,
                full_card_quota=density.cards_per_section,
                list_quota=density.list_quota,
                image_quota=density.image_quota,
                page_budget=density.page_budget,
            ))
            idx += 1
    return expanded


def _default_specials(profile: EditorialProfile) -> list[SpecialPlan]:
    """Genre-neutral fallbacks if the model returns no specials — no domain
    vocabulary may live here (it would leak into unrelated books)."""
    noun = profile.entry_schema.entry_noun
    return [
        SpecialPlan(
            after_section=1, kind="chart",
            heading="Quick Reference Chart",
            columns=["Item", "How to do it", "Notes"],
            rows=6,
        ),
        SpecialPlan(
            after_section=2, kind="checklist",
            heading="Routine Checklist",
            about=f"a simple recurring routine drawn from this book's {noun}s",
            items=10,
        ),
    ]


def _map_specials(
    specials: list[SpecialPlan], base_count: int, tiers_per_base: int,
) -> list[SpecialPlan]:
    """Map after_base_section (1..base_count) to expanded section index."""
    mapped: list[SpecialPlan] = []
    for sp in specials:
        base_idx = min(max(sp.after_section, 1), base_count)
        section_idx = (base_idx - 1) * tiers_per_base + 1
        mapped.append(sp.model_copy(update={"after_section": section_idx}))
    return mapped


def _enforce_toc_sections(
    sections: list[_BaseSection],
    toc: list[str],
) -> list[_BaseSection]:
    """Pad/trim sections to match the user TOC and set titles verbatim."""
    enforced: list[_BaseSection] = []
    for i, ch_title in enumerate(toc):
        if i < len(sections):
            s = sections[i]
            enforced.append(_BaseSection(
                title=ch_title,
                summary=s.summary,
                divider_subject=s.divider_subject,
                part_title=s.part_title,
                entries=s.entries,
            ))
        else:
            enforced.append(_BaseSection(
                title=ch_title,
                summary="",
                divider_subject="",
                part_title="",
                entries=[],
            ))
    return enforced


_TOC_SECTION_HDR = re.compile(
    r"^(?:section\s+\d+\s*:|part\s+\d+\s*:|chapter\s+\d+\s*:)", re.IGNORECASE,
)
_TOC_SKIP = frozenset({
    "subtitle", "what's inside", "whats inside", "bonus pages",
    "promise", "ideal length", "recipe count", "why this should exist",
})
_TOC_BARE_ORDINAL = re.compile(r"^\d{1,3}$")
# A pasted TOC line often carries its own dot-leader + page number
# ("How to Use This Cookbook ......... 3") — that's page-number bookkeeping
# from the source document, not part of the title, and must never survive
# into a generated chapter/section heading.
_TOC_DOT_LEADER = re.compile(r"\s*\.{2,}\s*\d+\s*$")


def _is_toc_section_header(line: str) -> bool:
    s = line.strip()
    if _TOC_SECTION_HDR.match(s):
        return True
    low = s.lower()
    return (low.startswith("section ") or low.startswith("chapter ")) and ":" in s


def _parse_toc_lines(toc: list[str]) -> list[_BaseSection]:
    """Parse a flat TOC into section headers with recipe/entry lines."""
    sections: list[_BaseSection] = []
    cur: _BaseSection | None = None
    next_is_header = False
    for raw in toc:
        line = _TOC_DOT_LEADER.sub("", raw.strip()).strip()
        if not line or line.lower() in _TOC_SKIP:
            continue
        if _TOC_BARE_ORDINAL.match(line):
            # A standalone numbering placeholder ("01", "02", ...) marks the
            # NEXT line as its own top-level section — front/back-matter
            # titles ("How to Use This Book") rarely say "Chapter N:" but are
            # still meant to be distinct sections, not entries under one.
            next_is_header = True
            continue
        if next_is_header or _is_toc_section_header(line):
            cur = _BaseSection(title=line, summary="", entries=[])
            sections.append(cur)
            next_is_header = False
        else:
            if cur is None:
                cur = _BaseSection(title="Recipes", summary="", entries=[])
                sections.append(cur)
            cur.entries.append(_EntryBrief(title=line, brief=""))
    return sections


def _merge_base_sections_to_limit(
    sections: list[_BaseSection], limit: int,
) -> list[_BaseSection]:
    if len(sections) <= limit:
        return sections
    out = sections[: limit - 1]
    merged = sections[limit - 1].model_copy(deep=True)
    for s in sections[limit:]:
        merged.entries.extend(s.entries)
    out.append(merged)
    return out


def _toc_looks_like_prose(toc: list[str]) -> bool:
    """True when `toc` is actually prose (e.g. a pasted description/instructions
    block dropped into the TOC field) rather than a list of chapter/entry
    titles. Real titles are short; sentences are long and rarely match any
    recognized section-header pattern. Used to discard unusable input rather
    than forcing it through a doomed page-count plan."""
    lines = [t.strip() for t in toc if t.strip()]
    if not lines:
        return False
    headers = sum(1 for t in lines if _is_toc_section_header(t))
    if headers > 0:
        return False
    avg_len = sum(len(t) for t in lines) / len(lines)
    long_frac = sum(1 for t in lines if len(t) > 90) / len(lines)
    return avg_len > 55 or long_frac > 0.3


def _normalize_user_toc(
    toc: list[str],
    target_pages: int,
    profile: EditorialProfile,
    *,
    pamphlet: bool,
) -> list[_BaseSection] | None:
    """Collapse recipe-list TOCs into page-feasible sections with planned entries."""
    if _toc_looks_like_prose(toc):
        log.warning("user_toc_looks_like_prose_discarded", raw_lines=len(toc))
        return None
    max_secs = max_sections_for_target(target_pages, profile, pamphlet=pamphlet)
    if len(toc) <= max_secs:
        headers = sum(1 for t in toc if _is_toc_section_header(t))
        if headers >= 2 or len(toc) <= max(6, max_secs // 2):
            return None

    parsed = _parse_toc_lines(toc)
    if not parsed:
        return None
    # A clean parse with zero nested entries just means the TOC is already a
    # flat per-section outline (e.g. ordinal-prefixed chapter/front-matter
    # titles) — use it directly rather than discarding it in favor of the
    # noisier `elif toc:` path, which would force the LLM toward `len(toc)`
    # sections (including any bare ordinal placeholder lines) instead of the
    # correct, already-deduplicated section count.

    merged = _merge_base_sections_to_limit(parsed, max_secs)
    log.info(
        "user_toc_normalized",
        raw_lines=len(toc),
        sections=len(merged),
        entries=sum(len(s.entries) for s in merged),
        max_sections=max_secs,
    )
    return merged


def _is_cookbookish(
    source: SourceDoc,
    *,
    description: str | None = None,
    toc: list[str] | None = None,
) -> bool:
    promised = parse_promised_count(source.topic, description or "")
    toc_count = len(toc or [])
    text = (source.topic + " " + (description or "")).lower()
    return (
        (promised is not None and promised >= 50)
        or toc_count >= 15
        or "recipe" in text
    )


def _apply_cookbook_profile_bias(
    profile: EditorialProfile,
    *,
    cookbookish: bool,
    has_user_toc: bool,
) -> EditorialProfile:
    """Bias cookbook-style books toward photo-rich, spread-friendly templates."""
    if not cookbookish:
        return profile
    updates: dict = {"image_density": "every_entry"}
    sparse_templates = ("tips_cards", "reference_bible", "framework")
    if has_user_toc and profile.structure_template in sparse_templates:
        updates["structure_template"] = "project_based"
        updates["pages_per_entry"] = 2.0
    elif profile.structure_template == "tips_cards":
        updates["structure_template"] = "essay_entries"
        updates["pages_per_entry"] = 2.0
    return profile.model_copy(update=updates)


def _critique_outline(
    draft: _PracticalBibleDraft,
    profile: EditorialProfile,
    target_pages: int,
) -> list[_BaseSection]:
    """Acquisitions-editor pass over the outline. Best-effort: on failure the
    original outline stands."""
    outline = "\n".join(
        f"[{i + 1}] ({s.part_title}) {s.title} — {s.summary}\n"
        + "\n".join(f"    - {e.title}: {e.brief}" for e in s.entries)
        for i, s in enumerate(draft.base_sections)
    )
    try:
        revised = llm.structured(
            _CRITIQUE_PROMPT.format(
                title=draft.title, genre=profile.genre,
                audience=profile.audience, pages=target_pages,
                entry_noun=profile.entry_schema.entry_noun, outline=outline,
            ),
            _OutlineRevision, system=_CRITIQUE_SYSTEM,
            model=settings.draft_model, max_tokens=16000,
        )
    except Exception as exc:
        if llm.never_swallow(exc):
            raise
        log.warning("outline_critique_skipped", err=str(exc)[:160])
        return draft.base_sections
    if len(revised.base_sections) < max(3, len(draft.base_sections) // 2):
        log.warning("outline_critique_rejected",
                    got=len(revised.base_sections),
                    expected=len(draft.base_sections))
        return draft.base_sections
    log.info("outline_critiqued", chapters=len(revised.base_sections))
    return revised.base_sections


def _pick_theme(llm_theme: str) -> str:
    """The planning model chooses the preset from a described menu; the only
    programmatic step is validation (unknown name → neutral guide preset)."""
    if llm_theme in theme.GUIDE_PRESET_MENU:
        return llm_theme
    if llm_theme in theme.PRESETS:
        return llm_theme
    return theme.NEUTRAL_GUIDE_PRESET


def _description_block(description: str | None) -> str:
    """Format an optional author description as a prompt block."""
    if not description or not description.strip():
        return ""
    return (
        "\n\nAUTHOR DESCRIPTION (written by the requester — treat as editorial "
        "direction and honor its angle, audience, and themes):\n\"\"\"\n"
        + description.strip()
        + "\n\"\"\""
    )


def _narrative_toc_block(toc: list[str] | None) -> str:
    """Format a required chapter list for the narrative prompt."""
    if not toc:
        return ""
    numbered = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(toc))
    return (
        "\n\nREQUIRED CHAPTER PLAN (the user has specified these chapter titles "
        "— you MUST use them EXACTLY and IN THIS ORDER; do not add, remove, "
        "rename, or reorder any chapter; fill each with a summary, beats, and "
        "page_budget that fit the title):\n" + numbered
    )


def _guide_toc_block(toc: list[str] | None) -> str:
    """Format a required section list for the practical-guide prompt."""
    if not toc:
        return ""
    numbered = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(toc))
    return (
        "\n\nREQUIRED TABLE OF CONTENTS (the user has specified these chapter "
        "titles — your base_sections MUST use them EXACTLY and IN THIS ORDER; "
        "do not add, remove, rename, or reorder any chapter; fill each with a "
        "summary, divider_subject, part_title, and entries):\n" + numbered
    )


def dry_run_toc_feasibility(
    *,
    topic: str,
    text: str | None,
    description: str | None,
    toc: list[str] | None,
    target_pages: int,
    density_mode: str | None,
    accepted_promised_count: int | None,
    profile_overrides: dict | None = None,
) -> tuple[bool, str, list]:
    """Zero-cost (no LLM calls) feasibility pre-check for a TOC-driven
    practical guide. Mirrors the real section-planning math in `build_bible`
    (TOC normalization, density resolution, page-band reconciliation) using a
    default profile guess instead of the LLM-derived one, so an infeasible
    plan (e.g. 60 promised recipes across a real chapter structure that can't
    fit the target page band at any density) is caught BEFORE the paid
    profile-derivation and guide-planning calls, not after.

    Only meaningful when `toc` is provided — without one, section structure
    isn't knowable before the planning LLM runs, so this returns (True, "", [])
    (no opinion, not a green light) rather than guessing.

    Returns (ok, message, options) — `options` are `FeasibilityOption`s built
    from the same real numbers as `message` (via `options_for_plan_infeasible`),
    so a caller never ends up showing a message and a fix-it button that
    disagree with each other."""
    if not toc or target_pages <= 15:
        return True, "", []

    from bookforge.errors import PlanInfeasibleError
    from bookforge.pipeline.feasibility import (
        _default_profile,
        apply_compact_profile,
        assess_request,
        options_for_plan_infeasible,
    )

    source = SourceDoc(topic=topic, text=text or "", passages=[])
    cookbookish = _is_cookbookish(source, description=description, toc=toc)
    profile = _default_profile(cookbookish)
    if profile_overrides:
        profile = profile.model_copy(update=profile_overrides)
    profile = _apply_cookbook_profile_bias(
        profile, cookbookish=cookbookish, has_user_toc=True,
    )
    pamphlet = target_pages <= 15

    topic_promised = (
        parse_promised_count(topic, description or "")
        or parse_promised_count(topic, "")
        or parse_promised_count("", text or "")
    )
    effective_promised = (
        accepted_promised_count
        if accepted_promised_count and accepted_promised_count > 0
        else topic_promised
    )

    if density_mode == "compact" or (
        density_mode is None
        and effective_promised
        and assess_request(
            topic=topic, description=description, text=text,
            target_pages=target_pages, toc=toc,
            accepted_promised_count=accepted_promised_count,
        ).status == "compact"
    ):
        profile = apply_compact_profile(profile)

    normalized = _normalize_user_toc(toc, target_pages, profile, pamphlet=pamphlet)
    if normalized is None:
        # TOC is short/clean enough to skip straight to LLM-guided drafting —
        # can't pre-validate a structure we don't have yet.
        return True, "", []

    density = _cap_density(
        _guide_density(target_pages, profile, promised=effective_promised),
        target_pages, profile, pamphlet=pamphlet, promised=effective_promised,
    )
    sections = _expand_sections(normalized, density, profile)
    min_ent = effective_promised or 0
    try:
        reconcile_plan_to_page_band(
            sections, profile, target_pages=target_pages, pamphlet=pamphlet,
            min_entries=min_ent, promised_ceiling=effective_promised or None,
            sections_locked=True,
        )
        return True, "", []
    except PlanInfeasibleError as exc:
        return False, str(exc), options_for_plan_infeasible(exc)


def build_bible(
    source: SourceDoc,
    *,
    target_pages: int = 100,
    book_kind: str = "practical_guide",
    description: str | None = None,
    toc: list[str] | None = None,
    profile_overrides: dict | None = None,
    density_mode: str | None = None,
    accepted_promised_count: int | None = None,
) -> BookBible:
    excerpt = source.text[:20000]
    desc_block = _description_block(description)

    if toc and _toc_looks_like_prose(toc):
        # A prose block (e.g. the description pasted into the TOC field)
        # cannot be honored as chapter structure — treat as if no TOC was
        # given at all, everywhere below, rather than forcing a doomed plan.
        log.warning("user_toc_looks_like_prose_discarded", raw_lines=len(toc))
        toc = None

    if book_kind == "narrative":
        toc_block = _narrative_toc_block(toc)
        page_limit_note = page_range_prompt_suffix(target_pages)
        prompt = (
            _NARRATIVE_PROMPT.format(
                topic=source.topic, pages=target_pages, source=excerpt,
                page_limit_note=page_limit_note,
                pages_image_min=max(1, target_pages // 2),
            )
            + desc_block
            + toc_block
        )
        bible = llm.structured(
            prompt,
            BookBible, system=_SYSTEM, max_tokens=8000,
        )
        bible.target_pages = target_pages
        bible.book_kind = "narrative"
        # Enforce user TOC exactly: overwrite chapter titles in order.
        if toc and bible.chapters:
            for i, ch_title in enumerate(toc):
                if i < len(bible.chapters):
                    bible.chapters[i].title = ch_title
                else:
                    from bookforge.schemas import ChapterPlan
                    bible.chapters.append(ChapterPlan(
                        index=i + 1, title=ch_title, summary="", beats=[],
                        page_budget=max(1, target_pages // len(toc)),
                    ))
            bible.chapters = bible.chapters[:len(toc)]
        _normalize_narrative_budgets(bible.chapters, target_pages)
        log.info("bible_built", kind="narrative", title=bible.title,
                 chapters=len(bible.chapters), user_toc=bool(toc))
        return bible

    profile = derive_profile(source, overrides=profile_overrides)
    plan_scope = build_plan_scope(
        topic=source.topic,
        description=description,
        text=source.text,
        toc=toc,
        accepted_promised_count=accepted_promised_count,
    )
    profile = _apply_cookbook_profile_bias(
        profile,
        cookbookish=_is_cookbookish(source, description=description, toc=toc),
        has_user_toc=bool(toc),
    )
    pamphlet = target_pages <= 15
    if pamphlet:
        profile = profile.model_copy(update={"tiers": 1})

    # Effective promise: user-accepted lower count wins over topic marketing.
    topic_promised = (
        parse_promised_count(source.topic, description or "")
        or parse_promised_count(source.topic, "")
        or parse_promised_count("", source.text or "")
    )
    if accepted_promised_count and accepted_promised_count > 0:
        effective_promised = accepted_promised_count
    else:
        effective_promised = topic_promised

    # Auto-compact when density_mode says so, or when promise only fits compact.
    from bookforge.pipeline.feasibility import (
        COMPACT_PAGES_PER_ENTRY,
        apply_compact_profile,
        assess_request,
    )
    from bookforge.errors import PlanInfeasibleError

    if density_mode == "compact" or (
        density_mode is None
        and effective_promised
        and assess_request(
            topic=source.topic,
            description=description,
            text=source.text,
            target_pages=target_pages,
            toc=toc,
            accepted_promised_count=accepted_promised_count,
        ).status == "compact"
    ):
        profile = apply_compact_profile(profile)
        density_mode = "compact"
        log.info(
            "bible_compact_density",
            promised=effective_promised,
            pages_per_entry=profile.pages_per_entry,
            target_pages=target_pages,
        )

    promised_hint = effective_promised
    design = derive_design(source, profile)
    spec = templates.spec_for(profile.structure_template)
    density = _cap_density(
        _guide_density(
            target_pages, profile, promised=promised_hint,
            min_sections=plan_scope.min_sections,
        ),
        target_pages,
        profile,
        pamphlet=pamphlet,
        promised=promised_hint,
    )
    normalized_toc_bases: list[_BaseSection] | None = None
    if toc:
        normalized_toc_bases = _normalize_user_toc(
            toc, target_pages, profile, pamphlet=pamphlet,
        )
    if normalized_toc_bases is not None:
        # Fail fast, using the REAL profile (already known at this point) —
        # before the expensive guide-draft LLM call below. When a user TOC
        # fully determines section structure, this check is exactly what
        # the later reconciliation will do anyway; catching it here saves
        # the largest cost component on a doomed plan instead of spending
        # it and finding out afterward.
        _preflight = _expand_sections(normalized_toc_bases, density, profile)
        _preflight = _cap_sections_to_page_ceiling(
            _preflight, target_pages, profile, pamphlet=pamphlet,
            min_entries=effective_promised or 0,
            min_sections=plan_scope.min_sections,
        )
        if effective_promised and planned_use_count(_preflight) < effective_promised:
            _preflight = expand_sections_to_count(_preflight, effective_promised)
        _preflight = _cap_sections_to_page_ceiling(
            _preflight, target_pages, profile, pamphlet=pamphlet,
            min_entries=effective_promised or 0,
            min_sections=plan_scope.min_sections,
        )
        reconcile_plan_to_page_band(
            _preflight, profile, target_pages=target_pages, pamphlet=pamphlet,
            min_entries=effective_promised or plan_scope.min_entries or 0,
            min_sections=plan_scope.min_sections,
            promised_ceiling=effective_promised or None,
            sections_locked=True,
        )
    if normalized_toc_bases is not None:
        base_lo = base_hi = len(normalized_toc_bases)
        base_target = len(normalized_toc_bases)
        guide_toc_block = _guide_toc_block([s.title for s in normalized_toc_bases])
    elif toc:
        base_lo = base_hi = len(toc)
        base_target = len(toc)
        guide_toc_block = _guide_toc_block(toc)
    else:
        base_target = density.base_sections
        base_lo = max(4, base_target - 1)
        base_hi = base_target + 2
        guide_toc_block = ""
    tiers_per = max(1, profile.tiers)
    planned_estimate = (base_target * tiers_per
                        * (density.cards_per_section + density.list_quota))

    savings_note = ""
    if profile.entry_schema.show_savings:
        savings_note = ("\nCharts may compare costs and name realistic savings; "
                        "keep every number honest.")

    page_limit_note = page_range_prompt_suffix(target_pages)
    draft = llm.structured(
        _GUIDE_PROMPT_TPL.format(
            genre=profile.genre, topic=source.topic, pages=target_pages,
            source=excerpt, grounded_content=profile.grounded_content or
            "the facts, history, and claims it contains",
            authored_content=profile.authored_content or
            f"the {profile.entry_schema.entry_noun}s themselves",
            spine=spec.spine, rhythm=spec.rhythm,
            entry_noun=profile.entry_schema.entry_noun,
            planned_entries=planned_estimate,
            audience=profile.audience, angle=profile.angle,
            base_lo=base_lo, base_hi=base_hi,
            cards_per_section=density.cards_per_section,
            theme_menu=theme.menu_text(),
            back_matter_heading=profile.back_matter_heading,
            back_matter_angle=profile.back_matter_angle or
            "the closing reference page readers of this genre expect",
            savings_note=savings_note,
            page_limit_note=page_limit_note,
        ) + desc_block + guide_toc_block,
        _PracticalBibleDraft,
        system=_GUIDE_SYSTEM_TPL.format(
            genre=profile.genre, audience=profile.audience,
            angle=profile.angle, persona=profile.persona or "its ideal author",
        ),
        model=settings.content_model, max_tokens=16000,
    )

    if normalized_toc_bases is not None:
        draft = draft.model_copy(update={"base_sections": normalized_toc_bases})
        log.info("guide_toc_normalized",
                 sections=len(normalized_toc_bases),
                 target_pages=target_pages)
    elif toc:
        enforced = _enforce_toc_sections(draft.base_sections, toc)
        draft = draft.model_copy(update={"base_sections": enforced})
        log.info("guide_toc_enforced", titles=[s.title for s in enforced])

    base_sections = _critique_outline(draft, profile, target_pages)
    if normalized_toc_bases is not None:
        base_sections = normalized_toc_bases
    elif toc:
        base_sections = _enforce_toc_sections(base_sections, toc)
        log.info("guide_toc_reenforced", count=len(base_sections))
    sections = _expand_sections(base_sections, density, profile)
    # Soft budget trim only — do not shrink below the effective promise yet.
    sections = _cap_sections_to_page_ceiling(
        sections, target_pages, profile, pamphlet=pamphlet,
        min_entries=effective_promised or 0,
        min_sections=plan_scope.min_sections,
    )
    if draft.specials:
        raw_specials = [s.to_plan() for s in draft.specials]
    elif pamphlet:
        raw_specials = []
    else:
        raw_specials = _default_specials(profile)
    specials = _map_specials(raw_specials, len(base_sections), tiers_per)

    title = draft.title
    subtitle = draft.subtitle
    promised = effective_promised or (
        parse_promised_count(title, subtitle)
        or parse_promised_count(source.topic, "")
    )
    # If the user accepted a lower count, rewrite marketing copy to match.
    if (
        accepted_promised_count
        and accepted_promised_count > 0
        and topic_promised
        and accepted_promised_count < topic_promised
    ):
        title, subtitle = rewrite_promised_number(
            title, subtitle, accepted_promised_count,
        )
        promised = accepted_promised_count
        log.info(
            "promise_accepted_by_user",
            topic_promised=topic_promised,
            accepted=accepted_promised_count,
        )

    planned = planned_use_count(sections)
    if promised and promised > planned:
        sections = expand_sections_to_count(sections, promised)
        planned = planned_use_count(sections)
        log.info("sections_expanded_to_promise",
                 promised=promised, planned=planned,
                 target_pages=target_pages)

    # Keep the promised count: never silently shrink. Cap with min_entries locked.
    sections = _cap_sections_to_page_ceiling(
        sections, target_pages, profile, pamphlet=pamphlet,
        min_entries=promised or 0,
        min_sections=plan_scope.min_sections,
        specials=specials,
    )
    planned = planned_use_count(sections)

    if pamphlet and promised and planned > promised:
        sections = shrink_sections_to_count(sections, promised)
        planned = promised
        log.info("pamphlet_shrunk_to_promise", promised=promised,
                 sections=len(sections))
    elif promised and planned < promised:
        # Could not honor the promise without user-accepted reduction.
        raise PlanInfeasibleError(
            f"plan could only fit {planned} items but promise is {promised} "
            f"at {target_pages} pages — raise pages or accept a lower count "
            f"at readiness"
        )
    elif not pamphlet and promised and promised < planned:
        title, subtitle, promise_fixed = reconcile_promise(
            title, subtitle, planned)
        if promise_fixed:
            log.warning("subtitle_promise_reconciled",
                        old_subtitle=draft.subtitle, new_subtitle=subtitle,
                        planned=planned)

    # Guard: mega-section collapse
    if (
        len(sections) == 1
        and target_pages > 15
        and sections[0].full_card_quota > 10
    ):
        raise PlanInfeasibleError(
            f"plan collapsed to 1 section with {sections[0].full_card_quota} "
            f"cards at {target_pages} pages — reduce TOC pressure or raise pages"
        )

    min_ent = promised or plan_scope.min_entries or 0
    sections = reconcile_plan_to_page_band(
        sections,
        profile,
        target_pages=target_pages,
        specials=specials,
        pamphlet=pamphlet,
        min_entries=min_ent,
        min_sections=plan_scope.min_sections,
        promised_ceiling=promised or None,
        sections_locked=plan_scope.locked_toc,
    )
    planned = planned_use_count(sections)

    bible = BookBible(
        title=title,
        subtitle=subtitle,
        audience=draft.audience or profile.audience,
        tone=draft.tone or profile.angle,
        art_style=draft.art_style,
        voice=draft.voice,
        target_pages=target_pages,
        book_kind="practical_guide",
        sections=sections,
        theme_name=_pick_theme(draft.theme_name),
        safety_key=profile.safety_domain,
        profile=profile,
        design=design,
        cover_subject=draft.cover_subject,
        how_to=draft.how_to,
        list_lead=draft.list_lead or
        f"Quick {profile.entry_schema.entry_noun} ideas, one line each.",
        pro_topic=draft.pro_topic or source.topic,
        specials=specials,
    )
    # Always the honored count — never keep a higher marketing number after fit.
    bible.promised_uses = promised or planned
    # Immutable snapshot of the user's original promise — later stages may
    # resync `promised_uses` to actual content, but never overwrite this.
    bible.original_promised_uses = bible.promised_uses
    # Make ALL planner-authored copy state the one promised count.
    propagate_promised_count(bible)
    log.info("bible_built", kind="practical_guide", title=bible.title,
             template=profile.structure_template, sections=len(bible.sections),
             promised_uses=bible.promised_uses, planned_uses=planned,
             theme=bible.theme_name, user_toc=bool(toc),
             density_mode=density_mode,
             profile=profile.model_dump())
    return bible
