"""Prompt templates for retention-focused audio narration scripts.

Mirrors the client SOP: brainstorm → refine list → write each item at a
target word band with a fixed rhetorical arc (hook → power → unsettling → close).
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
Map-Territory Distinction

Map-territory distinction is one of the most unsettling concepts because it reveals that most people are not reacting to reality itself, but to the picture of reality inside their mind. The "map" is your belief, memory, opinion, fear, assumption, trauma, culture, or explanation. The "territory" is what is actually happening beyond the story you have attached to it.

This concept becomes powerful because two people can experience the same moment and live in completely different worlds. One person hears silence and feels abandoned. Another hears silence and feels peace. One person sees rejection. Another sees protection. One person sees failure. Another sees feedback. The event may be the same, but the internal map changes the entire emotional meaning.

The disturbing part is that people often defend their map more than they examine the territory. They protect what they believe because their identity, pride, pain, and past experiences are tied to it. This is why simple disagreements can become intense. People are not only debating facts; they are defending the world their mind has built.

A painful childhood can create a map where distance feels like betrayal. Past humiliation can create a map where correction feels like attack. Repeated disappointment can create a map where hope feels unsafe. The person thinks they are seeing clearly, but they may only be seeing the present through an old emotional wound.

This concept changes how you understand people, conflict, fear, and even yourself. Before reacting, you begin to ask, "Am I seeing what is real, or am I seeing what my mind has learned to expect?" That question alone can break illusions."""


def category_voice(category: Category) -> str:
    return _CATEGORY_VOICE.get(category, _CATEGORY_VOICE["general"])


def brainstorm_system(category: Category) -> str:
    domain = category_voice(category)
    return (
        f"You are a 170-IQ writer specializing in {domain}. You write narration "
        "scripts for high-retention audio in the 'smarter while you sleep' / "
        "psychology explainer niche. Each concept must be distinct, memorable, "
        "and educative — never generic self-help fluff."
    )


def brainstorm_user(title: str, count: int) -> str:
    return (
        f'Write a detailed script plan for the title: "{title}".\n\n'
        f"List exactly {count} distinct concepts/secrets/items for this title. "
        "Each item title should be 2–6 words, Title Case, specific and intriguing "
        "(e.g. 'Map-Territory Distinction', 'Identity Protection'). "
        "No duplicates, no near-synonyms. Order for maximum retention across the full list."
    )


def refine_system(category: Category) -> str:
    domain = category_voice(category)
    return (
        f"You are an expert editor for {domain} narration scripts. Your job is to "
        "maximize retention: sharpen titles, remove weak or redundant items, "
        "reorder for escalating impact, and ensure every item earns its place."
    )


def refine_user(title: str, concepts: list[str], count: int) -> str:
    listing = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(concepts))
    return (
        f'Read through this list and refine it maximally for the title "{title}". '
        f"Aim for maximum retention. Return exactly {count} items.\n\n"
        f"CURRENT LIST:\n{listing}"
    )


def item_system(category: Category, word_min: int = 265, word_max: int = 270) -> str:
    domain = category_voice(category)
    return (
        f"You are a 170-IQ scriptwriter for {domain} audio narration. "
        "Write educative, high-retention segments. No bullet points, no headers "
        "inside the body — only flowing paragraphs.\n\n"
        f"STRUCTURE (4–5 paragraphs, {word_min}–{word_max} words total):\n"
        "1. Opening hook: \"[Title] is one of the most [powerful/unsettling/revealing] "
        "concepts because…\" — explain the core idea vividly.\n"
        "2. \"This becomes powerful because…\" — show real-world impact with concrete examples.\n"
        "3. \"The disturbing/unsettling part is…\" or \"The unsettling part is…\" — "
        "the hidden danger or blind spot.\n"
        "4. Optional deepening paragraph with more examples or social/systemic angle.\n"
        "5. Close with \"Once you understand [title/lowercase topic], you begin to…\" "
        "and a reflective question or insight.\n\n"
        f"EXAMPLE (match tone, rhythm, and paragraph count — do NOT copy content):\n\n"
        f"{_EXAMPLE_ITEM}"
    )


def item_user(
    title: str,
    concept_title: str,
    number: int,
    total: int,
    word_min: int = 265,
    word_max: int = 270,
) -> str:
    return (
        f'Narration title: "{title}"\n'
        f"Write item {number} of {total}: \"{concept_title}\".\n\n"
        "Make it educative with maximum retention. Word length MUST be between "
        f"{word_min} and {word_max} words. Use 4–5 paragraphs. Return the concept "
        "title unchanged and the body as separate paragraphs."
    )


def item_rewrite_user(
    title: str,
    concept_title: str,
    paragraphs: list[str],
    word_count: int,
    word_min: int = 265,
    word_max: int = 270,
) -> str:
    body = "\n\n".join(paragraphs)
    return (
        f'Narration title: "{title}"\n'
        f'Rewrite item "{concept_title}" to be exactly {word_min}–{word_max} words '
        f"(current: {word_count} words). Keep the same structure and meaning. "
        "Return 4–5 paragraphs.\n\n"
        f"CURRENT DRAFT:\n{body}"
    )
