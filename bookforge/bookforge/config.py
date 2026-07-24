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
    # Readiness probe for image provider: metadata (cheap), generate (real image), off (key only).
    readiness_image_probe: str = Field("generate", alias="READINESS_IMAGE_PROBE")

    # --- Audio scripts / ElevenLabs TTS ---
    elevenlabs_api_key: str = Field("", alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field(
        "bP8FJDHmWVEgXJDitdQd", alias="ELEVENLABS_VOICE_ID",
    )
    elevenlabs_model_id: str = Field(
        "eleven_multilingual_v2", alias="ELEVENLABS_MODEL_ID",
    )
    elevenlabs_tts_workers: int = Field(2, alias="ELEVENLABS_TTS_WORKERS")
    # Docker Compose publishes API on host 8010 (8010→8000).
    api_public_base_url: str = Field(
        "http://localhost:8010", alias="API_PUBLIC_BASE_URL",
    )
    audio_script_max_items: int = Field(100, alias="AUDIO_SCRIPT_MAX_ITEMS")
    audio_script_item_workers: int = Field(4, alias="AUDIO_SCRIPT_ITEM_WORKERS")

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
    # When vision QA rejects every candidate, keep the best-scoring one anyway
    # (flagged_for_review) rather than leaving the slot empty.
    image_accept_qa_rejects: bool = Field(True, alias="IMAGE_ACCEPT_QA_REJECTS")
    # Fraction of required image slots (cover + card heroes + dividers) that must
    # be filled before layout. 1.0 = never ship a book missing any required photo.
    image_require_coverage: float = Field(1.0, alias="IMAGE_REQUIRE_COVERAGE")
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
    # Post-layout page scaling: trim content and re-render when PDF exceeds target.
    max_page_scale_revisions: int = Field(3, alias="MAX_PAGE_SCALE_REVISIONS")
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

    # --- Hang protection / client timeouts ---
    # Per-request read timeout for Anthropic calls. Tenacity owns retries; the
    # SDK's internal retry is disabled so a hung call is bounded by this alone.
    llm_timeout_s: float = Field(180.0, alias="LLM_TIMEOUT_S")
    image_timeout_s: float = Field(120.0, alias="IMAGE_TIMEOUT_S")
    # Process-wide cap on concurrent in-flight LLM requests, so per-stage worker
    # pools compose with MAX_CONCURRENT_BOOKS without stampeding the API.
    llm_max_inflight: int = Field(12, alias="LLM_MAX_INFLIGHT")
    # Cooperative per-attempt wall-clock deadline. On breach the job PARKS with
    # checkpoints intact (resume continues where it left off), it does not fail.
    job_timeout_minutes: int = Field(120, alias="JOB_TIMEOUT_MINUTES")
    # Watchdog: a "running" job whose heartbeat is older than this is re-parked
    # if its worker thread is gone (e.g. wedged then process partially recovered).
    heartbeat_stale_minutes: int = Field(30, alias="HEARTBEAT_STALE_MINUTES")
    # How many concurrency slots parked/resumed jobs may occupy at once — the
    # remainder is reserved for fresh submissions so resume storms can't starve
    # new work. Must be < MAX_CONCURRENT_BOOKS to guarantee a fresh-work slot.
    resume_max_concurrent: int = Field(1, alias="RESUME_MAX_CONCURRENT")

    # --- Always-ship policy ---
    # When every layout repair rung is exhausted, ship the best rendered PDF with
    # layout_warnings + an operator alert instead of failing the job. Set false
    # to restore the old hard-fail behavior.
    ship_on_layout_defects: bool = Field(True, alias="SHIP_ON_LAYOUT_DEFECTS")
    # Ship a book that still exceeds its page ceiling after all trim passes
    # (with a warning) instead of raising PageLimitError.
    ship_on_page_overshoot: bool = Field(False, alias="SHIP_ON_PAGE_OVERSHOOT")
    # Finished PDF page band: [target, min(ratio×target, target+absolute, 300)].
    max_page_overshoot_ratio: float = Field(1.2, alias="MAX_PAGE_OVERSHOOT_RATIO")
    max_page_overshoot_absolute: int = Field(20, alias="MAX_PAGE_OVERSHOOT_ABSOLUTE")
    # How many times a DAILY image-quota exhaustion may park the book until the
    # quota horizon before we ship it with the images we have.
    image_quota_max_parks: int = Field(1, alias="IMAGE_QUOTA_MAX_PARKS")
    # Timezone whose midnight defines the daily image-quota reset (Google quotas
    # reset at midnight Pacific).
    quota_reset_tz: str = Field("America/Los_Angeles", alias="QUOTA_RESET_TZ")

    # --- Throughput (bounded worker pools; 1 = serial, matches old behavior) ---
    section_concurrency: int = Field(4, alias="SECTION_CONCURRENCY")
    image_concurrency: int = Field(4, alias="IMAGE_CONCURRENCY")
    # Generate 1 image candidate and only add more when vision QA rejects it,
    # instead of always generating IMAGE_CANDIDATES_PER_PAGE upfront.
    image_adaptive_candidates: bool = Field(True, alias="IMAGE_ADAPTIVE_CANDIDATES")
    prose_score_concurrency: int = Field(8, alias="PROSE_SCORE_CONCURRENCY")
    vision_qa_concurrency: int = Field(4, alias="VISION_QA_CONCURRENCY")
    # Anthropic prompt caching (cache_control on the stable system prefix).
    prompt_cache_enabled: bool = Field(True, alias="PROMPT_CACHE_ENABLED")
    # Run vision layout QA only on the final accepted artifact instead of on
    # every intermediate assemble pass (intermediates are discarded anyway).
    layout_qa_final_only: bool = Field(True, alias="LAYOUT_QA_FINAL_ONLY")
    # Preflight render probe validates the RENDERER, not the book — cache its
    # verdict per-process for this long.
    preflight_ttl_s: int = Field(3600, alias="PREFLIGHT_TTL_S")

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
