"""Pydantic schemas — the typed contract that flows through every pipeline stage.

These double as the JSON Schemas we hand to Claude for structured output, so the
model returns data we can validate instead of prose we have to parse.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Stage 0 — ingest
# --------------------------------------------------------------------------- #
class SourceDoc(BaseModel):
    """Normalized source corpus the whole book is grounded in."""

    topic: str
    origin: Literal["text", "youtube", "audio"] = "text"
    origin_ref: str | None = None  # URL or filename
    text: str
    # Chunked passages with stable ids, used for RAG grounding + claim citations.
    passages: list["Passage"] = Field(default_factory=list)


class Passage(BaseModel):
    id: str  # e.g. "p0007"
    text: str
    embedding: list[float] | None = None


# --------------------------------------------------------------------------- #
# Stage 1 — Book Bible
# --------------------------------------------------------------------------- #
class ArtStyle(BaseModel):
    medium: str  # "editorial food photography", "vintage gouache illustration"...
    palette: str
    lighting: str
    era: str
    composition_rules: str
    negative_prompt: str  # what to avoid, applied to every image
    style_suffix: str  # appended to every image prompt to lock consistency


class VoiceStyle(BaseModel):
    narrator_persona: str
    tone_register: str  # "warm, literary, authoritative" (avoids BaseModel.register)
    pov: str
    pacing: str
    vocabulary: str
    banned_patterns: list[str] = Field(
        default_factory=lambda: [
            "in conclusion", "nestled", "testament to", "rich tapestry",
            "delve", "it's worth noting", "boasts", "whether you're",
        ]
    )


class ChapterPlan(BaseModel):
    index: int
    title: str
    summary: str
    beats: list[str]
    page_budget: int


class SpecialPage(str, Enum):
    """Named special pages a practical-guide section can own."""

    safety_dilution = "safety_dilution"
    never_mix = "never_mix"
    replace_chart = "replace_chart"
    savings_calc = "savings_calc"
    monthly_checklist = "monthly_checklist"
    when_pro = "when_pro"
    exterminator_tracker = "exterminator_tracker"


class HowToPage(BaseModel):
    heading: str = "How to Use This Book"
    lead: str = ""
    points: list[str] = Field(default_factory=list)


class SpecialPlan(BaseModel):
    after_section: int
    kind: Literal["chart", "checklist"]
    heading: str
    columns: list[str] = Field(default_factory=list)
    rows: int = 6
    about: str = ""
    items: int = 10


class SectionPlan(BaseModel):
    """Practical-guide analog of ChapterPlan: a TOC category with quotas."""

    index: int
    title: str  # == a table-of-contents category
    summary: str = ""
    entry_budget: int = 0      # how many of the book's "uses" this section owns
    full_card_quota: int = 0   # of those, how many are full use_cards
    list_quota: int = 0        # remainder rendered as compact use_list rows
    special_pages: list[SpecialPage] = Field(default_factory=list)
    image_quota: int = 1       # hero + key step images for this section
    page_budget: int = 4
    # Explicit subject for this section's full-page divider photo. Set this when
    # the section TITLE would mislead the image model (e.g. "Roach Prevention"
    # makes it draw roaches) — describe the clean/prevented scene instead. When
    # empty, the orchestrator falls back to a title-based subject.
    divider_subject: str = ""
    # Optional part grouping (from the profile's structural template): parts give
    # the TOC a narrative arc ("Part II — The Cold Revolution").
    part_index: int = 0
    part_title: str = ""
    # Entry-level outline ("Title — one-line brief") planned at bible time; the
    # writer realizes exactly these, which is what makes chapters non-generic.
    planned_entries: list[str] = Field(default_factory=list)


class EntrySchema(BaseModel):
    """Shape and labels of the book's repeating unit (recipe, tip, project…).

    Defaults reproduce the legacy senior home-guide card exactly, so a bible
    without a profile renders byte-identically.
    """

    entry_noun: str = "tip"                 # "recipe", "project", "profile"…
    need_label: str = "What You Need"       # cookbook: "Ingredients"
    steps_label: str = "Steps"              # cookbook: "Method"
    requires_materials: bool = True         # False → omit "What You Need" entirely
                                            # (habit/mindset guides need nothing physical)
    require_quantities: bool = False        # every need item carries an amount
    show_savings: bool = True               # render/require the $-savings callout
    show_meta: bool = False                 # "Serves 4 · 45 min" strip
    min_steps: int = 3
    max_steps: int = 5
    headnote_lo: int = 20   # entry intro (headnote) word range
    headnote_hi: int = 35


class SafetyCard(BaseModel):
    """One 'never do this' pair for the book's Safety First page."""

    pair: str  # e.g. the two things never to combine / the act never to do
    why: str


class EditorialProfile(BaseModel):
    """Per-book editorial brief derived at planning time (Opus).

    Carries everything that used to be hardcoded to the senior/cleaning domain:
    audience, angle, labels, safety brief, structure, density, and the
    grounding policy. `None` on a bible means "legacy senior home guide".
    The safety brief is GENERATED per topic — there is no per-domain canon.
    """

    genre: str = "home_guide"
    audience: str = "general adult readers"
    angle: str = ""            # editorial angle, e.g. "forgotten American food history"
    persona: str = ""          # author persona a top publisher would hire
    safety_domain: Literal["chemical", "pest", "food", "general_home", "none"] = (
        "general_home"
    )
    # --- LLM-generated safety brief (topic-specific, replaces any static canon) ---
    safety_rules: str = ""     # prompt block injected into every writer call
    safety_cards: list[SafetyCard] = Field(default_factory=list)  # Safety First page
    danger_patterns: list[list[str]] = Field(default_factory=list)
    # ^ lowercase substring co-occurrence patterns for the deterministic QA scan
    entry_schema: EntrySchema = Field(default_factory=EntrySchema)
    # --- structure ---
    structure_template: Literal[
        "tips_cards",        # legacy senior card book (T-legacy)
        "essay_entries",     # T1 narrative-forward: chapter essay + entries
        "reference_bible",   # T2 high-density compendium
        "field_guide",       # T3 rigidly parallel taxonomy entries
        "framework",         # T4 problem→framework→application
        "project_based",     # T5 skills primer + difficulty-ordered projects
        "narrative_history", # T6 prose chapters with artifact entries
    ] = "tips_cards"
    narrative_share: float = 0.0   # 0.0-0.8: how much of the book is essay/prose
    pages_per_entry: float = 1.0   # cookbook ≈ 2.0 (full recipe page + photo)
    tiers: int = 2                 # 1 = no "More Uses" tier expansion
    tier_names: list[str] = Field(
        default_factory=lambda: ["Essentials", "More Uses"])
    list_section_heading: str = "More {topic} Uses"
    quick_list_weight: float = 1.0  # scales the quick-list quota (cookbook ~0.15)
    image_density: Literal["sparse", "standard", "every_entry"] = "every_entry"
    feature_menu: list[str] = Field(default_factory=list)  # allowed sidebar kinds
    # --- cover & matter ---
    cover_kicker: str = ""
    cover_foot: str = ""
    back_matter_heading: str = "When to Call a Professional"
    back_matter_angle: str = ""
    # --- grounding policy ---
    grounded_content: str = ""  # what MUST trace to the source (history, dates…)
    authored_content: str = ""  # what the model is commissioned to CREATE
    style_sheet: list[str] = Field(default_factory=list)  # term canon rules


class DesignKit(BaseModel):
    """A bespoke, per-book design system planned at bible time (by Opus).

    Replaces "pick one of 4 look-alike presets" with a topic-specific palette +
    type pairing + art direction, so no two books look the same. `None` on a bible
    means "use the legacy preset menu" (old manifests render identically).

    Colour tokens mirror the keys `layout/theme.py` presets emit as CSS variables;
    `validate_kit()` there enforces contrast and fills any gap from a preset.
    """

    # --- palette (hex) — same token names the guide CSS consumes as vars ---
    paper: str          # page background
    panel: str          # tinted card/box background (light)
    panel_dark: str     # dark step-box background
    ink: str            # body text on paper
    accent: str         # primary brand/heading colour
    deep: str           # darkest brand shade (dividers, bands)
    gold: str           # secondary/metallic accent (rules, step numbers)
    muted: str          # eyebrows, folios, captions
    onpaper_soft: str   # soft body variant
    savings: str = "#2E7D5B"
    caution: str = "#B5651D"
    danger: str = "#B3261E"
    # --- typography — role → font-family stack (from theme.FONT_LIBRARY) ---
    display: str        # headings (a distinctive display/serif)
    body: str           # body text (a legible text face; plain zero glyph)
    cond: str           # eyebrows/labels/folios (a condensed/grotesque)
    # --- direction ---
    mood: str = ""              # e.g. "clinical, energetic, modern-athletic"
    cover_art_direction: str = ""  # steers the cover + interior image style
    motif: str = ""             # a recurring visual motif for the book


def legacy_profile() -> EditorialProfile:
    """The implicit profile of every pre-profile book: the senior home guide.

    This module is the ONLY sanctioned home for the legacy senior/money-saving
    vocabulary — prompts and layout must read it from here, never inline it.
    """
    return EditorialProfile(
        genre="home_guide",
        audience="Seniors (65+), large-print friendly",
        angle="cheap, safe, step-by-step home tips",
        safety_domain="general_home",
        entry_schema=EntrySchema(),  # defaults == legacy card
        structure_template="tips_cards",
        cover_kicker="A Senior's Money-Saving Guide",
        cover_foot="Simple steps · Big savings · Plain English",
    )


def profile_of(bible: "BookBible") -> EditorialProfile:
    """The bible's profile, or the legacy senior profile for old manifests."""
    return bible.profile or legacy_profile()


class BookBible(BaseModel):
    title: str
    subtitle: str
    audience: str
    tone: str
    art_style: ArtStyle
    voice: VoiceStyle
    chapters: list[ChapterPlan] = Field(default_factory=list)
    recurring_motifs: list[str] = Field(default_factory=list)
    target_pages: int = 100
    # --- practical-guide path (additive; narrative books leave these default) ---
    book_kind: Literal["narrative", "practical_guide"] = "narrative"
    sections: list[SectionPlan] = Field(default_factory=list)
    theme_name: str = "home_clean"
    safety_key: str = "general"
    # Editorial brief derived at planning time. None == legacy senior guide
    # (old manifests deserialize and rerender identically via profile_of()).
    profile: EditorialProfile | None = None
    # Bespoke per-book design system (palette + fonts + art direction), planned
    # by Opus. None → fall back to the legacy preset menu (theme.pick).
    design: DesignKit | None = None
    cover_subject: str = ""
    how_to: HowToPage | None = None
    list_lead: str = ""
    pro_topic: str = ""
    specials: list[SpecialPlan] = Field(default_factory=list)
    promised_uses: int | None = None  # marketing count from title/subtitle

    def specials_for(self, section_index: int) -> list[SpecialPlan]:
        return [s for s in self.specials if s.after_section == section_index]


# --------------------------------------------------------------------------- #
# Stage 2 — content
# --------------------------------------------------------------------------- #
class BlockType(str, Enum):
    # --- narrative / recipe path (existing — do not remove) ---
    intro = "intro"
    history = "history"
    ingredients = "ingredients"
    method = "method"
    sidebar = "sidebar"
    pull_quote = "pull_quote"
    body = "body"
    # --- practical-guide path (additive) ---
    section_intro = "section_intro"  # short lead-in under a section hero
    use_card = "use_card"            # ONE full how-to entry (the workhorse)
    use_list = "use_list"            # compact page of many terse uses
    what_you_need = "what_you_need"  # standalone supplies list
    steps = "steps"                  # standalone numbered steps
    caution = "caution"              # safety/warning callout (safety-gated)
    savings_note = "savings_note"    # "replaces X / saves ~$Y" callout
    chart = "chart"                  # table (replace-this / dilution / tracker)
    checklist = "checklist"          # checkbox list (prevention / pest-proofing)
    faq = "faq"                      # Q/A pairs


class Claim(BaseModel):
    """An atomic factual assertion extracted from the prose, for verification."""

    id: str
    text: str
    source_passage_ids: list[str] = Field(default_factory=list)
    verdict: Literal["supported", "unsupported", "contradicted", "pending"] = "pending"
    verifier_notes: str = ""


class ImageBrief(BaseModel):
    """Instructions for the image stage, derived from a block's content."""

    prompt: str  # the SUBJECT only (no style words); the pipeline wraps it
    kind: Literal["photo", "illustration"] = "photo"  # hybrid art direction
    needs_in_image_text: bool = False
    aspect: Literal["full_bleed", "square", "wide", "portrait"] = "full_bleed"
    caption: str | None = None


# --- structured payloads for practical-guide blocks (additive) --------------- #
class StepItem(BaseModel):
    text: str


class UseEntry(BaseModel):
    """One of the book's numbered "uses", in full-card or compact-list form."""

    number: int = 0  # stable global id (1..250 for book 1)
    title: str
    intro: str = ""  # short lead / recipe headnote; empty in compact-list form
    what_you_need: list[str] = Field(default_factory=list)  # doubles as ingredients
    steps: list[StepItem] = Field(default_factory=list)
    caution: str | None = None  # short inline safety note
    savings: str | None = None  # "Replaces bleach pens (~$6/yr)"
    # --- recipe/project metadata (additive; legacy entries leave these "") ---
    serves: str = ""      # yield, phrased identically book-wide ("Serves 4")
    time: str = ""        # "20 min prep · 45 min bake"
    variation: str = ""   # one-line riff ("Make it your own…")
    note: str = ""        # make-ahead / storage note
    source_line: str = ""  # attribution ("Adapted from …") when sourced


class TableModel(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)  # each row len == columns
    note: str | None = None


class ChecklistItem(BaseModel):
    text: str
    cadence: Literal["daily", "weekly", "monthly", "seasonal", "once"] | None = None


class ChecklistModel(BaseModel):
    title: str | None = None
    items: list[ChecklistItem] = Field(default_factory=list)


class FAQItem(BaseModel):
    q: str
    a: str


class ContentBlock(BaseModel):
    type: BlockType
    heading: str | None = None
    text: str = ""  # prose blocks; structured blocks may leave this empty
    claims: list[Claim] = Field(default_factory=list)
    image_brief: ImageBrief | None = None
    # --- structured payloads (exactly one populated for structured types) ---
    entries: list[UseEntry] = Field(default_factory=list)  # use_card (1) / use_list (N)
    table: TableModel | None = None                         # chart
    checklist: ChecklistModel | None = None                # checklist
    faqs: list[FAQItem] = Field(default_factory=list)       # faq
    callout_kind: Literal["savings", "caution", "tip"] | None = None


class Chapter(BaseModel):
    index: int
    title: str
    blocks: list[ContentBlock]


# --------------------------------------------------------------------------- #
# Stage 3 — images
# --------------------------------------------------------------------------- #
class GeneratedImage(BaseModel):
    block_ref: str  # chapter.block index, e.g. "2.0"
    storage_key: str  # S3 key of the chosen, upscaled, print-ready image
    width: int
    height: int
    score: float = 0.0
    flagged_for_review: bool = False


# --------------------------------------------------------------------------- #
# Stage 4/5/6 — pages, QA, assembly
# --------------------------------------------------------------------------- #
class TemplateName(str, Enum):
    cover = "cover"
    chapter_opener = "chapter_opener"
    full_bleed_image = "full_bleed_image"
    recipe_spread = "recipe_spread"
    text_page = "text_page"
    sidebar = "sidebar"
    # practical-guide templates
    title_page = "title_page"
    how_to_use = "how_to_use"
    toc = "toc"
    safety_page = "safety_page"
    use_card_page = "use_card_page"
    use_list_page = "use_list_page"
    chart_page = "chart_page"
    checklist_page = "checklist_page"
    faq_page = "faq_page"
    pro_page = "pro_page"


class Page(BaseModel):
    number: int
    template: TemplateName
    chapter_index: int
    blocks: list[ContentBlock]
    image_key: str | None = None


class SafetyFinding(BaseModel):
    """A safety problem found in the prose/instructions (chemical guides)."""

    severity: Literal["block", "warn"]
    rule: str  # which safety rule was violated
    location: str = ""  # "section 4 / entry 'Clean grout'"
    excerpt: str = ""
    fix: str = ""


class QAResult(BaseModel):
    groundedness: float  # fraction of retained claims verified (target = 1.0)
    unsupported_claims: list[Claim] = Field(default_factory=list)
    prose_quality_score: float = 0.0
    layout_issues: list[str] = Field(default_factory=list)
    image_issues: list[str] = Field(default_factory=list)
    safety_findings: list[SafetyFinding] = Field(default_factory=list)
    safety_passed: bool = True
    passed: bool = False


class BookArtifact(BaseModel):
    book_id: str
    interior_pdf_key: str | None = None
    cover_pdf_key: str | None = None
    page_count: int = 0
    file_size_mb: float = 0.0
    qa: QAResult | None = None


# resolve forward refs
SourceDoc.model_rebuild()
