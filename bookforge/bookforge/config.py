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
    # Comma-separated fallback models tried (in order) when the primary model
    # fails with a MODEL-SPECIFIC error (not-found, overload, non-credit 400).
    # Credits/auth failures are NOT cascaded — they fail identically on every
    # model and are handled by park & auto-resume instead.
    content_model_fallbacks: str = Field(
        "claude-sonnet-4-6,claude-haiku-4-5-20251001",
        alias="CONTENT_MODEL_FALLBACKS",
    )

    # --- Images (gemini Imagen default; openai gpt-image-1 alternative) ---
    image_primary_provider: str = Field("gemini", alias="IMAGE_PRIMARY_PROVIDER")
    image_primary_model: str = Field("imagen-4.0-generate-001", alias="IMAGE_PRIMARY_MODEL")
    image_allow_provider_fallback: bool = Field(False, alias="IMAGE_ALLOW_PROVIDER_FALLBACK")
    image_reject_text_artifacts: bool = Field(True, alias="IMAGE_REJECT_TEXT_ARTIFACTS")

    # --- Transcription / search ---
    deepgram_api_key: str = Field("", alias="DEEPGRAM_API_KEY")
    search_api_key: str = Field("", alias="SEARCH_API_KEY")
    search_provider: str = Field("tavily", alias="SEARCH_PROVIDER")
    # Netscape-format cookies.txt (exported from a real logged-in YouTube session)
    # used to work around YouTube IP-blocking this server's requests ("Sign in to
    # confirm you're not a bot"). Empty = unauthenticated requests (may be blocked).
    yt_cookies_file: str = Field("", alias="YT_COOKIES_FILE")

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
    per_book_budget_usd: float = Field(50.0, alias="PER_BOOK_BUDGET_USD")  # 0 = no cap
    # Upper bound on DISTINCT base topics the bible may plan (each expands to N
    # tiered sections). 0 = no ceiling (length scales freely with target_pages).
    max_base_sections: int = Field(0, alias="MAX_BASE_SECTIONS")
    # Operator override for per-entry image density ("sparse" | "standard" |
    # "every_entry"). Empty = use the book profile's template default.
    # every_entry ≈ one photo per entry — premium look, higher image spend.
    image_density_override: str = Field("", alias="IMAGE_DENSITY")

    # --- Resilience / self-heal ---
    # Whole-job auto-retries for TRANSIENT failures before the job is parked.
    max_job_retries: int = Field(2, alias="MAX_JOB_RETRIES")
    # Backoff (seconds) between park & auto-resume attempts when the AI provider
    # is unavailable (credits/auth/outage). The last value repeats indefinitely,
    # so a parked job self-completes on the next tick after credits are restored.
    provider_retry_backoff_s: str = Field(
        "60,300,900,1800", alias="PROVIDER_RETRY_BACKOFF_S",
    )
    # How often the background loop scans for parked ("waiting") jobs to resume.
    resume_poll_interval_s: int = Field(60, alias="RESUME_POLL_INTERVAL_S")
    # Cap on restart-driven resumes of a single job (guards against crash loops).
    max_resume_attempts: int = Field(3, alias="MAX_RESUME_ATTEMPTS")
    # Cache TTL for the /health provider ping (a cheap 1-token model call).
    health_ping_ttl_s: int = Field(60, alias="HEALTH_PING_TTL_S")
    # Optional webhook (Slack-style JSON {"text": ...}) for operator alerts.
    operator_alert_webhook: str = Field("", alias="OPERATOR_ALERT_WEBHOOK")
    max_concurrent_books: int = Field(2, alias="MAX_CONCURRENT_BOOKS")
    image_candidates_per_page: int = Field(2, alias="IMAGE_CANDIDATES_PER_PAGE")
    max_file_size_mb: int = Field(80, alias="MAX_FILE_SIZE_MB")
    max_source_size_mb: int = Field(10, alias="MAX_SOURCE_SIZE_MB")
    groundedness_threshold: float = Field(1.0, alias="GROUNDEDNESS_THRESHOLD")
    # QA fix-loop: how many times the guide gate may feed its faults back into the
    # writer and regenerate the offending sections before the book ships anyway.
    # 0 = single pass, no fix loop (the book still ships best-effort, never errors).
    max_qa_revisions: int = Field(3, alias="MAX_QA_REVISIONS")
    # Layout fix-loop: how many times a LayoutError (audit defect that survived
    # the deterministic repair ladder) may regenerate just the offending blocks
    # with hard length budgets before the job fails. 0 = fail on first audit miss.
    max_layout_revisions: int = Field(2, alias="MAX_LAYOUT_REVISIONS")
    # Strict quality: treat a below-bar prose/editorial score as an ACTIONABLE
    # fault (regenerate the weak chapter) rather than a soft warning that ships.
    strict_quality: bool = Field(True, alias="STRICT_QUALITY")
    prose_floor: float = Field(0.7, alias="PROSE_FLOOR")
    layout_qa_enabled: bool = Field(True, alias="LAYOUT_QA_ENABLED")
    layout_qa_block: bool = Field(False, alias="LAYOUT_QA_BLOCK")
    layout_qa_sample_pages: int = Field(10, alias="LAYOUT_QA_SAMPLE_PAGES")
    # Deterministic geometry gate on the FINAL PDF (rasterize + measure coverage).
    # Zero API cost, runs on every book, hard-fails a page-size/scale/clip defect.
    pdf_geometry_check: bool = Field(True, alias="PDF_GEOMETRY_CHECK")

    # --- Print specs ---
    trim_width_in: float = Field(8.5, alias="TRIM_WIDTH_IN")
    trim_height_in: float = Field(11.0, alias="TRIM_HEIGHT_IN")
    bleed_in: float = Field(0.125, alias="BLEED_IN")
    target_dpi: int = Field(300, alias="TARGET_DPI")

    # --- Auth ---
    auth_enabled: bool = Field(True, alias="AUTH_ENABLED")
    jwt_secret: str = Field("change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")
    jwt_expires_min: int = Field(60 * 24, alias="JWT_EXPIRES_MIN")

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
