"""Structural templates — how premium publishers structure practical nonfiction.

Six template specs distilled from publisher conventions (plus the legacy tips-
card book). A template is DATA: the planning stage shows the menu to the model,
the model picks one per book, and the chosen spec seeds the EditorialProfile's
density/rhythm defaults (which the model may still tune per book).

Nothing in here is topic-specific — specs describe structure (spine, rhythm,
density), never domain vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    menu_line: str            # one-line description shown to the planning model
    spine: str                # organizing logic, injected into the outline prompt
    rhythm: str               # per-chapter page rhythm, injected into prompts
    pages_per_entry: float
    tiers: int
    quick_list_weight: float
    image_density: str        # sparse | standard | every_entry
    narrative_share: float
    feature_menu: list[str] = field(default_factory=list)
    has_opener_essay: bool = False


TEMPLATES: dict[str, TemplateSpec] = {
    "tips_cards": TemplateSpec(
        key="tips_cards",
        menu_line="High-count numbered how-to tips on uniform cards; minimal prose.",
        spine="distinct everyday topic areas, each a run of numbered how-to cards",
        rhythm="section intro, then a run of full cards, then a compact quick list",
        pages_per_entry=1.0, tiers=2, quick_list_weight=1.0,
        image_density="sparse", narrative_share=0.0,
    ),
    "essay_entries": TemplateSpec(
        key="essay_entries",
        menu_line=("Narrative-forward: each chapter opens with a grounded essay, "
                   "followed by 5-8 complete entries with story-rich headnotes. "
                   "For heritage/place/history-angle books."),
        spine=("6-12 thematic chapters, each anchored to one idea, era, place, or "
               "material from the source — never generic categories"),
        rhythm=("chapter-opener essay (300-500 words), a run of complete entries, "
                "1-2 feature boxes between entries, a short variations list to close"),
        pages_per_entry=2.0, tiers=1, quick_list_weight=0.15,
        image_density="every_entry", narrative_share=0.35,
        feature_menu=["then_and_now", "from_the_source", "technique_note"],
        has_opener_essay=True,
    ),
    "reference_bible": TemplateSpec(
        key="reference_bible",
        menu_line=("High-density compendium: 10-20 exhaustive category chapters, "
                   "compact functional entries, reference charts."),
        spine="10-20 conventional, exhaustive category chapters",
        rhythm=("brief chapter intro, dense entry runs, a reference chart or "
                "comparison box per chapter"),
        pages_per_entry=0.8, tiers=1, quick_list_weight=0.6,
        image_density="standard", narrative_share=0.1,
        feature_menu=["technique_note", "comparison_box"],
    ),
    "field_guide": TemplateSpec(
        key="field_guide",
        menu_line=("Identification guide: a primer chapter, then rigidly parallel "
                   "one-page entries with at-a-glance stat blocks."),
        spine="a how-to-identify primer, then entries grouped by family/type/region",
        rhythm=("every entry identical in shape: stat block, description, "
                "how to spot/choose, care/use, common problems"),
        pages_per_entry=1.0, tiers=1, quick_list_weight=0.2,
        image_density="every_entry", narrative_share=0.1,
        feature_menu=["comparison_box", "seasonal_calendar"],
    ),
    "framework": TemplateSpec(
        key="framework",
        menu_line=("Idea-driven: Part I the problem + the framework, Part II one "
                   "chapter per component, Part III application. Anecdote-led "
                   "chapters with exercises and takeaway boxes."),
        spine=("three parts: the problem and the framework; one chapter per "
               "framework component; application and troubleshooting"),
        rhythm=("opening anecdote, concept exposition, examples, practical "
                "exercises, a key-takeaways box to close each chapter"),
        pages_per_entry=1.5, tiers=1, quick_list_weight=0.3,
        image_density="sparse", narrative_share=0.6,
        feature_menu=["case_study", "worksheet", "common_mistakes"],
        has_opener_essay=True,
    ),
    "project_based": TemplateSpec(
        key="project_based",
        menu_line=("Make/do progression: skills primer, then projects in ascending "
                   "difficulty — hero photo, materials, numbered steps, variations."),
        spine="a skills primer chapter, then project chapters in ascending difficulty",
        rhythm=("per project: hero photo first, what-you-learn intro, materials and "
                "tools in order of use, numbered steps, variations, troubleshooting"),
        pages_per_entry=2.0, tiers=1, quick_list_weight=0.2,
        image_density="every_entry", narrative_share=0.15,
        feature_menu=["technique_note", "gallery"],
    ),
    "narrative_history": TemplateSpec(
        key="narrative_history",
        menu_line=("History-first prose chapters; practical entries appear as "
                   "period artifacts set off from the narrative."),
        spine="chronological or episodic narrative chapters",
        rhythm=("continuous prose with verbatim artifact entries set off "
                "typographically, each framed before and after by analysis"),
        pages_per_entry=3.0, tiers=1, quick_list_weight=0.0,
        image_density="standard", narrative_share=0.8,
        feature_menu=["from_the_source", "timeline"],
        has_opener_essay=True,
    ),
}


def spec_for(key: str) -> TemplateSpec:
    return TEMPLATES.get(key, TEMPLATES["tips_cards"])


def menu_text() -> str:
    """The template menu shown to the planning model — described by structure
    and mood, never by topic keywords."""
    return "\n".join(
        f'- "{t.key}": {t.menu_line}' for t in TEMPLATES.values()
        if t.key != "tips_cards"
    ) + (
        '\n- "tips_cards": ' + TEMPLATES["tips_cards"].menu_line +
        " (only when the title itself promises a large numbered tip count)"
    )
