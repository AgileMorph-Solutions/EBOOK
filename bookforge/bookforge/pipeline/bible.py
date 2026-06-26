"""Stage 1 — Book Bible.

A single structured-output call that turns the source corpus into the contract
the rest of the pipeline obeys: title, art style (locks visual consistency),
voice (locks prose consistency), and a chapter plan with a page budget.
"""
from __future__ import annotations

import structlog

from bookforge.clients import llm
from bookforge.schemas import BookBible, SourceDoc

log = structlog.get_logger(__name__)

_SYSTEM = """You are a senior commissioning editor and art director at a premium \
illustrated-nonfiction publisher. You design books that read like they were \
written by an award-winning author and look like they were art-directed by a \
top studio. You are precise, opinionated, and allergic to generic AI phrasing."""

_PROMPT = """Design the Book Bible for an illustrated nonfiction book.

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


def build_bible(source: SourceDoc, *, target_pages: int = 100) -> BookBible:
    # Cap source we send (the bible only needs the gist); full corpus is used
    # later for grounded writing + claim verification.
    excerpt = source.text[:20000]
    bible = llm.structured(
        _PROMPT.format(topic=source.topic, pages=target_pages, source=excerpt),
        BookBible,
        system=_SYSTEM,
        max_tokens=8000,
    )
    bible.target_pages = target_pages
    log.info("bible_built", title=bible.title, chapters=len(bible.chapters))
    return bible
