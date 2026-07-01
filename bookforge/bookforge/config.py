"""Central configuration, loaded from environment / .env.

Every secret and tunable lives here so nothing is hard-coded in the pipeline.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM ---
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    content_model: str = Field("claude-opus-4-8", alias="CONTENT_MODEL")
    draft_model: str = Field("claude-haiku-4-5-20251001", alias="DRAFT_MODEL")
    vision_qa_model: str = Field("claude-opus-4-8", alias="VISION_QA_MODEL")

    # --- Images (gemini Imagen default; openai gpt-image-1 alternative) ---
    image_primary_provider: str = Field("gemini", alias="IMAGE_PRIMARY_PROVIDER")
    image_primary_model: str = Field("imagen-4.0-generate-001", alias="IMAGE_PRIMARY_MODEL")
    image_allow_provider_fallback: bool = Field(False, alias="IMAGE_ALLOW_PROVIDER_FALLBACK")
    image_reject_text_artifacts: bool = Field(True, alias="IMAGE_REJECT_TEXT_ARTIFACTS")

    # --- Transcription / search ---
    deepgram_api_key: str = Field("", alias="DEEPGRAM_API_KEY")
    search_api_key: str = Field("", alias="SEARCH_API_KEY")
    search_provider: str = Field("tavily", alias="SEARCH_PROVIDER")

    # --- Infra ---
    database_url: str = Field(
        "postgresql+psycopg://bookforge:bookforge@localhost:5432/bookforge",
        alias="DATABASE_URL",
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    s3_endpoint: str = Field("http://localhost:9000", alias="S3_ENDPOINT")
    s3_bucket: str = Field("bookforge", alias="S3_BUCKET")
    s3_access_key: str = Field("minioadmin", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field("minioadmin", alias="S3_SECRET_KEY")
    s3_region: str = Field("us-east-1", alias="S3_REGION")

    # --- Guardrails ---
    per_book_budget_usd: float = Field(50.0, alias="PER_BOOK_BUDGET_USD")
    image_candidates_per_page: int = Field(2, alias="IMAGE_CANDIDATES_PER_PAGE")
    max_file_size_mb: int = Field(80, alias="MAX_FILE_SIZE_MB")
    groundedness_threshold: float = Field(1.0, alias="GROUNDEDNESS_THRESHOLD")
    layout_qa_enabled: bool = Field(True, alias="LAYOUT_QA_ENABLED")
    layout_qa_block: bool = Field(False, alias="LAYOUT_QA_BLOCK")
    layout_qa_sample_pages: int = Field(10, alias="LAYOUT_QA_SAMPLE_PAGES")

    # --- Print specs ---
    trim_width_in: float = Field(8.5, alias="TRIM_WIDTH_IN")
    trim_height_in: float = Field(11.0, alias="TRIM_HEIGHT_IN")
    bleed_in: float = Field(0.125, alias="BLEED_IN")
    target_dpi: int = Field(300, alias="TARGET_DPI")

    # --- Observability ---
    langfuse_public_key: str = Field("", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field("", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field("https://cloud.langfuse.com", alias="LANGFUSE_HOST")

    @property
    def page_px(self) -> tuple[int, int]:
        """Full-bleed page size in pixels at target DPI."""
        w = round((self.trim_width_in + 2 * self.bleed_in) * self.target_dpi)
        h = round((self.trim_height_in + 2 * self.bleed_in) * self.target_dpi)
        return w, h


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
