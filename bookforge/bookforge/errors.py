"""Pipeline exception types used for failure classification.

Kept in their own module (no heavy imports) so both the pipeline stages and the
API's failure classifier can import them without circular-import risk.
"""
from __future__ import annotations


class EmptyBookError(RuntimeError):
    """Every chapter/section failed to generate — there is no book to ship.

    In practice this almost always means the AI provider is unavailable
    (credits/auth/outage), so the API classifies it as provider_unavailable and
    parks the job for auto-resume rather than shipping a hollow PDF."""


class IngestError(RuntimeError):
    """Source ingestion failed (e.g. YouTube download/transcription). Treated as
    transient by the API so the job retries, then parks."""


class ContentQAError(RuntimeError):
    """The deterministic content audit failed hard (missing quantities on a
    quantity-required book, unsafe instructions, mass duplication). Raised
    BEFORE the image stage so a defective book never spends image budget or
    ships. Retrying the same content won't help — the job needs regeneration
    or operator review, so the API classifies it as permanent."""


class GeometryError(RuntimeError):
    """The rendered PDF failed the deterministic geometry gate (page size/scale/
    clip). Kept HARD on purpose — we never ship a visibly broken layout. Retrying
    won't help (it's a render regression), so the API classifies it as permanent
    and marks the job failed with a loud operator alert."""


class LayoutError(GeometryError):
    """The in-page layout audit found clipped or overflowing text (text stuck
    below its fit floor, content overflowing a clip container, or a blocking
    vision-QA defect) that the deterministic repair ladder could not fix.
    Subclasses GeometryError on purpose: same contract (HARD failure, never
    ship a visibly broken page) and the API already classifies GeometryError
    as permanent with a friendly layout message.

    `defects` carries the audit's structured overflow records (page, entry
    repair key, over_px, text snippet) so the orchestrator's layout-revise
    loop can regenerate exactly the offending blocks."""

    def __init__(self, message: str, defects: list[dict] | None = None):
        super().__init__(message)
        self.defects = defects or []


class ImageCoverageError(RuntimeError):
    """Too few required image slots were filled after generation + QA fallback.

    Raised at the end of the images stage so a photo-less ebook never ships when
    IMAGE_REQUIRE_COVERAGE demands full coverage."""

    def __init__(self, message: str, *, required: int, got: int, missing: list[str]):
        self.required = required
        self.got = got
        self.missing = missing
        super().__init__(message)


class ReadinessError(RuntimeError):
    """Services or request are not ready — fail before ingest/content spend."""

    def __init__(
        self,
        title: str,
        message: str,
        reason_code: str | None = None,
    ) -> None:
        self.title = title
        self.reason_code = reason_code
        super().__init__(message)


class PipelineCancelledError(RuntimeError):
    """User or operator cancelled the job — stop between stages, do not retry."""
