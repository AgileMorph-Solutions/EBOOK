"""Prompt templates for retention-focused audio narration scripts.

Flow: brainstorm → refine list → write each item at a target word band in a
conversational spoken voice (varied openings, no stock phrase templates).
Prompts are title/category-parameterized — never hardcode a sample title.
"""
from __future__ import annotations

from typing import Literal

Category = Literal["psychology", "relationship", "philosophy", "general"]

_CATEGORY_VOICE: dict[str, str] = {
    "psychology": "psychology, cognitive bias, and human behavior",
    "relationship": "relationships, social dynamics, and emotional intelligence",
    "philosophy": "philosophy, wisdom, and how to see the world differently",
    "general": "self-improvement, mindset, and human nature",
}

_EXAMPLE_ITEM = """\
Map-territory distinction

Picture this: you and a friend hear the same silence after an argument. You feel abandoned. They feel calm. Nothing in the room changed — only the story each of you told yourselves. That story is the map. The silence is the territory.

Most of us argue about maps as if they were facts. A text that feels cold. A look that feels like rejection. A quiet day that feels like proof nobody cares. You are not reacting to what happened; you are reacting to the picture your mind painted over it.

And here is the quiet trap — people will defend that picture harder than they will check reality. Identity, pride, old wounds… they all glue themselves to the map. So a tiny disagreement turns loud, not because the facts are huge, but because the map feels like home.

Once you catch yourself mid-reaction, try a smaller move: ask what you are actually seeing, versus what you expected to see. That tiny pause is often enough to stop the old story from running the night."""


def category_voice(category: Category) -> str:
    return _CATEGORY_VOICE.get(category, _CATEGORY_VOICE["general"])


def feedback_block(feedback: str | None) -> str:
    """Append revision notes when regenerating. Empty when none so create prompts stay identical."""
    text = (feedback or "").strip()
    if not text:
        return ""
    return (
        "\n\nREVISION FEEDBACK — a previous draft of this narration was reviewed. "
        "Apply the notes below across the full script (concept list and every item). "
        "Keep what already works; fix exactly what the feedback asks for:\n"
        f"{text}"
    )


def brainstorm_system(category: Category) -> str:
    domain = category_voice(category)
    return (
        f"You are a 170-IQ writer specializing in {domain}. You write narration "
        "scripts for high-retention audio in the 'smarter while you sleep' / "
        "psychology explainer niche. Each concept must be distinct, memorable, "
        "and educative — never generic self-help fluff."
    )


def brainstorm_user(title: str, count: int, feedback: str | None = None) -> str:
    return (
        f'Write a detailed script plan for the title: "{title}".\n\n'
        f"List exactly {count} distinct concepts/secrets/items for this title. "
        "Each item title should be 2–6 words in sentence case: capitalize only "
        "the first word (e.g. 'Map-territory distinction', "
        "'Foot in the door escalation'). Never use Title Case like "
        "'Foot In The Door Escalation'. No trailing period on titles. "
        "No duplicates, no near-synonyms. Order for maximum retention across the full list."
        f"{feedback_block(feedback)}"
    )


def refine_system(category: Category) -> str:
    domain = category_voice(category)
    return (
        f"You are an expert editor for {domain} narration scripts. Your job is to "
        "maximize retention: sharpen titles, remove weak or redundant items, "
        "reorder for escalating impact, and ensure every item earns its place. "
        "Keep every title in sentence case (first word capital, rest lowercase)."
    )


def refine_user(
    title: str,
    concepts: list[str],
    count: int,
    feedback: str | None = None,
) -> str:
    listing = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(concepts))
    return (
        f'Read through this list and refine it maximally for the title "{title}". '
        f"Aim for maximum retention. Return exactly {count} items in sentence case "
        "(first word capital only; e.g. 'Identity protection', not "
        "'Identity Protection').\n\n"
        f"CURRENT LIST:\n{listing}"
        f"{feedback_block(feedback)}"
    )


def item_system(category: Category, word_min: int = 265, word_max: int = 270) -> str:
    domain = category_voice(category)
    return (
        f"You are a late-night narration scriptwriter for {domain} audio. "
        "Speak to one listener like a sharp, warm friend — conversational and "
        "sticky, never like an encyclopedia entry. No bullet points, no headers "
        "inside the body — only flowing paragraphs.\n\n"
        f"LENGTH: 4–5 paragraphs, {word_min}–{word_max} words total.\n\n"
        "SOFT ARC (do not use fixed sentence starters):\n"
        "- Hook the listener with a scene, question, or vivid moment.\n"
        "- Make the idea concrete with \"you\" and everyday examples.\n"
        "- Leave them with something sticky — a reframe, a quiet warning, "
        "or a small next move.\n\n"
        "VARIETY RULES (critical):\n"
        "- Every item must open differently. Never reuse the same opening pattern "
        "across items.\n"
        "- BANNED stock phrases (never use these or close paraphrases):\n"
        "  • \"[Title] is one of the most [powerful/unsettling/revealing]…\"\n"
        "  • \"This becomes powerful because…\"\n"
        "  • \"The unsettling/disturbing part is…\" / \"The unsettling part is…\"\n"
        "  • \"Once you understand…\"\n"
        "- Prefer short punchy lines mixed with longer ones; concrete scenes over "
        "abstract definitions.\n\n"
        "Return the concept title in sentence case (first word capital only).\n\n"
        f"EXAMPLE (match conversational tone and paragraph count — do NOT copy content):\n\n"
        f"{_EXAMPLE_ITEM}"
    )


def item_user(
    title: str,
    concept_title: str,
    number: int,
    total: int,
    word_min: int = 265,
    word_max: int = 270,
    feedback: str | None = None,
) -> str:
    return (
        f'Narration title: "{title}"\n'
        f"Write item {number} of {total}: \"{concept_title}\".\n\n"
        "Make it conversational and high-retention — not encyclopedic. "
        "Vary your opening; do not use banned stock phrases. Word length MUST "
        f"be between {word_min} and {word_max} words. Use 4–5 paragraphs. "
        "Return the concept title in sentence case and the body as separate paragraphs."
        f"{feedback_block(feedback)}"
    )


def item_rewrite_user(
    title: str,
    concept_title: str,
    paragraphs: list[str],
    word_count: int,
    word_min: int = 265,
    word_max: int = 270,
    feedback: str | None = None,
) -> str:
    body = "\n\n".join(paragraphs)
    return (
        f'Narration title: "{title}"\n'
        f'Rewrite item "{concept_title}" to be exactly {word_min}–{word_max} words '
        f"(current: {word_count} words). Keep the meaning and conversational tone. "
        "Do not fall back into banned stock phrases "
        "(\"is one of the most…\", \"This becomes powerful because…\", "
        "\"The unsettling part is…\", \"Once you understand…\"). "
        "Vary the opening. Return 4–5 paragraphs.\n\n"
        f"CURRENT DRAFT:\n{body}"
        f"{feedback_block(feedback)}"
    )
