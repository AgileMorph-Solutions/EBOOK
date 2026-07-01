"""Stage 1 — Book Bible.

Narrative books: chapter plan for illustrated nonfiction.
Practical guides: section plan with card/list quotas for the senior card layout.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, Field, model_validator

from bookforge.clients import llm
from bookforge.layout import theme
from bookforge.pipeline.content_guardrails import parse_promised_count, planned_use_count
from bookforge.schemas import (
    ArtStyle, BookBible, ChapterPlan, HowToPage, SectionPlan, SourceDoc,
    SpecialPlan, VoiceStyle,
)

log = structlog.get_logger(__name__)

_SYSTEM = """You are a senior commissioning editor and art director at a premium \
illustrated-nonfiction publisher. You design books that read like they were \
written by an award-winning author and look like they were art-directed by a \
top studio. You are precise, opinionated, and allergic to generic AI phrasing."""

_NARRATIVE_PROMPT = """Design the Book Bible for an illustrated nonfiction book.

TOPIC: {topic}
TARGET LENGTH: ~{pages} pages, chapter-wise, image-rich (one hero image per spread).

SOURCE MATERIAL (the book must be grounded in this — do not invent beyond it):
\"\"\"
{source}
\"\"\"

Produce:
- A compelling title + subtitle.
- The intended audience and tone.
- An ART STYLE guide so all ~{pages} images share one coherent look: medium, \
palette, lighting, era, composition rules, a negative_prompt of things to avoid, \
and a style_suffix string that will be appended to EVERY image prompt.
- A VOICE guide: narrator persona, register, POV, pacing, vocabulary, and a \
banned_patterns list of clichés/AI-tells to never use.
- A chapter plan: each chapter has a title, one-paragraph summary, 3-6 concrete \
beats, and a page_budget. The page_budgets should sum to roughly {pages}.
- recurring_motifs that tie the book together.

Be specific and editorial. No filler."""

_GUIDE_SYSTEM = """You are a senior editor designing a SENIOR-FIRST practical home \
guide (large print, card-based how-to layout). The book teaches cheap, safe, \
step-by-step tips grounded in the source material. Plain English only."""

_GUIDE_PROMPT = """Design the Book Bible for a senior practical home guide.

TOPIC: {topic}
TARGET LENGTH: ~{pages} pages total.

SOURCE MATERIAL (sections and tips must be grounded here):
\"\"\"
{source}
\"\"\"

Produce:
- title + subtitle (money-saving / practical angle for seniors).
- audience: seniors 65+, large-print friendly.
- tone: warm, plain, reassuring.
- art_style: bright clean home photography; palette matches the topic; \
negative_prompt must include "no text, no clutter, no faces".
- voice: second person, short sentences, everyday words; banned_patterns list.
- base_sections: 6-10 topic areas from the source (e.g. "Kitchen Cleaning"). \
Each has title, one-paragraph summary, and divider_subject — a CLEAN vertical \
portrait scene for the section opener photo panel (centered subject with margin \
on all sides; one bowl, plate, or tidy scene — NOT a wide pantry shelf that \
gets cropped). No pests, no scary imagery.
- how_to: heading, lead (25-40 words), exactly 4 bullet points for the welcome page.
- safety_key: "hp" if hydrogen peroxide / cleaning chemicals dominate; "pest" if \
pest control dominates; else "general".
- theme_name: "pest_guard" if pest topic; "senior_cookbook" if food/cooking/recipe \
topic; "home_clean" if cleaning/chemical topic; pick from source content.
- list_lead: one sentence for quick-tip list pages (e.g. "Quick jobs you can do \
in a minute or two with items already at home.").
- pro_topic: short phrase for the "when to call a professional" page.
- cover_subject: photo prompt for the cover — match the book type (warm kitchen \
scene for food books; tidy clean home for cleaning; protected home for pest). \
No text, no faces.
- specials: 2-3 special pages — each has after_section (1-based index into \
base_sections, NOT the expanded tier list), kind ("chart" or "checklist"), heading, and for charts: columns \
(3 strings) + rows (5-8); for checklists: about (one sentence) + items (8-12).

TITLE/SUBTITLE RULES:
- If the subtitle promises a tip count (e.g. "250 ways"), that number must be \
realistic for ~{pages} pages and the section count you plan — do NOT promise \
"700 ways" or "800 tips" unless the book will actually contain that many.
- Use correct spelling in titles, headings, and product names.
- Headings must accurately describe the section topic.

Be specific. Every base_section must reflect the source, not generic filler."""


class _BaseSection(BaseModel):
    title: str
    summary: str
    divider_subject: str = ""


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
            heading = (
                "Replace Expensive Products" if kind == "chart"
                else "Weekly Kitchen Checklist"
            )
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
            columns=self.columns or ["Store product", "Cheap method", "~Saving"],
            rows=self.rows,
            about=self.about,
            items=self.items if isinstance(self.items, int) else 10,
        )


class _PracticalBibleDraft(BaseModel):
    title: str
    subtitle: str
    audience: str = "Seniors (65+), large-print friendly"
    tone: str = "warm, plain, reassuring"
    art_style: ArtStyle
    voice: VoiceStyle
    base_sections: list[_BaseSection] = Field(min_length=3)
    how_to: HowToPage
    safety_key: str = "general"
    theme_name: str = "home_clean"
    list_lead: str = ""
    pro_topic: str = ""
    cover_subject: str = ""
    specials: list[_SpecialDraft] = Field(default_factory=list)


_TIER_NAMES = ("Essentials", "More Uses", "Extra Tips")


def _clamp(lo: int, hi: int, v: int) -> int:
    return max(lo, min(hi, v))


def _section_quotas(target_pages: int, num_sections: int) -> tuple[int, int, int, int]:
    pages_per = max(4, target_pages // max(1, num_sections))
    full_cards = _clamp(3, 10, pages_per // 3)
    list_quota = _clamp(10, 35, int(pages_per * 1.2))
    image_quota = min(3, full_cards)
    return full_cards, list_quota, image_quota, pages_per


def _expand_sections(
    base_sections: list[_BaseSection], target_pages: int,
) -> list[SectionPlan]:
    tiers = _TIER_NAMES if target_pages >= 150 else _TIER_NAMES[:2]
    n = len(base_sections) * len(tiers)
    fc, lq, iq, pb = _section_quotas(target_pages, n)
    expanded: list[SectionPlan] = []
    idx = 1
    for base in base_sections:
        for tier in tiers:
            if tier == "Essentials":
                title = f"{base.title} — Essentials"
                summary = base.summary
            elif tier == "More Uses":
                title = f"{base.title} — More Uses"
                summary = (
                    f"More practical {base.title.lower()} tips with everyday "
                    f"household items."
                )
            else:
                title = f"{base.title} — Extra Tips"
                summary = (
                    f"Additional senior-friendly {base.title.lower()} jobs "
                    f"that save money."
                )
            expanded.append(SectionPlan(
                index=idx, title=title, summary=summary,
                divider_subject=base.divider_subject,
                full_card_quota=fc, list_quota=lq, image_quota=iq,
                page_budget=pb,
            ))
            idx += 1
    return expanded


def _default_specials() -> list[SpecialPlan]:
    return [
        SpecialPlan(
            after_section=1, kind="chart",
            heading="Replace Expensive Staples",
            columns=["Store product", "Cheap rice method", "~Yearly saving"],
            rows=6,
        ),
        SpecialPlan(
            after_section=2, kind="checklist",
            heading="Weekly Rice Cooking Checklist",
            about="a simple weekly kitchen routine for cooking rice on a budget",
            items=10,
        ),
    ]


def _map_specials(
    specials: list[SpecialPlan], base_count: int, tiers_per_base: int,
) -> list[SpecialPlan]:
    """Map after_base_section (1..base_count) to expanded section index."""
    mapped: list[SpecialPlan] = []
    for sp in specials:
        base_idx = sp.after_section
        if base_idx < 1:
            base_idx = 1
        if base_idx > base_count:
            base_idx = base_count
        section_idx = (base_idx - 1) * tiers_per_base + 1
        mapped.append(sp.model_copy(update={"after_section": section_idx}))
    return mapped


def infer_guide_theme(topic: str, excerpt: str, llm_theme: str) -> str:
    """Pick guide preset from keyword scoring on topic + source; LLM hint is a tie-break."""
    text = f"{topic} {excerpt[:8000]}".lower()
    guide_presets = ("home_clean", "pest_guard", "senior_cookbook")
    scores = {
        preset: sum(1 for w in theme._KEYWORDS.get(preset, []) if w in text)
        for preset in guide_presets
    }
    best = max(scores, key=lambda p: scores[p])
    if scores[best] == 0:
        return llm_theme if llm_theme in theme.PRESETS else "home_clean"
    if llm_theme in guide_presets and scores.get(llm_theme, 0) >= scores[best]:
        return llm_theme
    return best


def _infer_theme(topic: str, excerpt: str, explicit: str) -> str:
    return infer_guide_theme(topic, excerpt, explicit)


def build_bible(
    source: SourceDoc,
    *,
    target_pages: int = 100,
    book_kind: str = "practical_guide",
) -> BookBible:
    excerpt = source.text[:20000]

    if book_kind == "narrative":
        bible = llm.structured(
            _NARRATIVE_PROMPT.format(
                topic=source.topic, pages=target_pages, source=excerpt),
            BookBible, system=_SYSTEM, max_tokens=8000,
        )
        bible.target_pages = target_pages
        bible.book_kind = "narrative"
        log.info("bible_built", kind="narrative", title=bible.title,
                 chapters=len(bible.chapters))
        return bible

    draft = llm.structured(
        _GUIDE_PROMPT.format(topic=source.topic, pages=target_pages, source=excerpt),
        _PracticalBibleDraft, system=_GUIDE_SYSTEM, max_tokens=8000,
    )
    tiers_per = 3 if target_pages >= 150 else 2
    sections = _expand_sections(draft.base_sections, target_pages)
    raw_specials = [s.to_plan() for s in draft.specials] or _default_specials()
    specials = _map_specials(raw_specials, len(draft.base_sections), tiers_per)

    bible = BookBible(
        title=draft.title,
        subtitle=draft.subtitle,
        audience=draft.audience,
        tone=draft.tone,
        art_style=draft.art_style,
        voice=draft.voice,
        target_pages=target_pages,
        book_kind="practical_guide",
        sections=sections,
        theme_name=_infer_theme(source.topic, excerpt, draft.theme_name),
        safety_key=draft.safety_key,
        cover_subject=draft.cover_subject,
        how_to=draft.how_to,
        list_lead=draft.list_lead or (
            "Quick jobs you can do in a minute or two with items already at home."
        ),
        pro_topic=draft.pro_topic or source.topic,
        specials=specials,
    )
    planned = planned_use_count(sections)
    promised = parse_promised_count(bible.title, bible.subtitle)
    bible.promised_uses = promised or planned
    log.info("bible_built", kind="practical_guide", title=bible.title,
             sections=len(bible.sections), promised_uses=bible.promised_uses,
             planned_uses=planned)
    return bible
