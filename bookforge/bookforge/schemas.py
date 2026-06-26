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


class BookBible(BaseModel):
    title: str
    subtitle: str
    audience: str
    tone: str
    art_style: ArtStyle
    voice: VoiceStyle
    chapters: list[ChapterPlan]
    recurring_motifs: list[str] = Field(default_factory=list)
    target_pages: int = 100


# --------------------------------------------------------------------------- #
# Stage 2 — content
# --------------------------------------------------------------------------- #
class BlockType(str, Enum):
    intro = "intro"
    history = "history"
    ingredients = "ingredients"
    method = "method"
    sidebar = "sidebar"
    pull_quote = "pull_quote"
    body = "body"


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


class ContentBlock(BaseModel):
    type: BlockType
    heading: str | None = None
    text: str
    claims: list[Claim] = Field(default_factory=list)
    image_brief: ImageBrief | None = None


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


class Page(BaseModel):
    number: int
    template: TemplateName
    chapter_index: int
    blocks: list[ContentBlock]
    image_key: str | None = None


class QAResult(BaseModel):
    groundedness: float  # fraction of retained claims verified (target = 1.0)
    unsupported_claims: list[Claim] = Field(default_factory=list)
    prose_quality_score: float = 0.0
    layout_issues: list[str] = Field(default_factory=list)
    image_issues: list[str] = Field(default_factory=list)
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
