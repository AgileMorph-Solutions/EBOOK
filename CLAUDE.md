# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BookForge is an AI-powered ebook generation pipeline that transforms a topic + source text (or YouTube transcript) into a print-ready, image-rich Amazon KDP PDF. The pipeline enforces accuracy via claim-level grounding and RAG, applies multi-pass editorial polish, generates style-locked AI imagery, and renders print-spec PDFs.

## Development Setup

```bash
cd bookforge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install playwright && playwright install chromium
cp .env.example .env
# Fill .env with API keys (ANTHROPIC_API_KEY is required minimum)
```

## Running the Pipeline

**Local spike (single process, no Docker):**
```bash
python -m bookforge.cli \
  --topic "100 Forgotten British Meals Our Grandparents Lived On" \
  --text-file sample_source.txt \
  --chapters 1 --pages 100
```
Output: `./artifacts/books/<book_id>/interior.pdf`

**Full stack (Docker Compose):**
```bash
docker compose -f bookforge/infra/docker-compose.yml up --build

# Submit a book job (returns immediately with book_id)
curl -X POST http://localhost:8000/books \
  -H 'content-type: application/json' \
  -d '{"topic":"Forgotten British Meals","text":"<your source text>","chapter_limit":1}'

# Poll status → queued | running | done | failed
curl http://localhost:8000/books/<book_id>

# Download PDF once status=done
curl http://localhost:8000/books/<book_id>/download -o book.pdf
```

**Preview design themes without running the full pipeline:**
```bash
python bookforge/preview_design.py
```

**Test image generation:**
```bash
python bookforge/test_photoreal.py
```

## Architecture

The pipeline has two execution modes sharing the same stage logic:
- **Spike/CLI**: `pipeline/orchestrator.py` runs all stages in-process, sequentially.
- **Production**: `workflow/tasks.py` wraps stages as Celery tasks (chord/chain) on a Redis broker.

### Pipeline Stages (in order)

| Stage | File | What it does |
|-------|------|-------------|
| 0 – Ingest | `pipeline/ingest.py` | Accepts text or YouTube URL; chunks into semantic passages with stable IDs for RAG |
| 1 – Book Bible | `pipeline/bible.py` | Single Claude call → title, art direction, voice persona, chapter plan (structured JSON via tool_use) |
| 2 – Content | `pipeline/content.py` | Per-chapter: RAG retrieval → draft → dev edit → line edit → AI-tell removal (4 sequential Claude calls); extracts atomic claims with source citations |
| 3 – Images | `pipeline/images.py` | Per image brief: generate N candidates → vision score (relevance 60% + aesthetic 40%) → pick best → de-AI finish |
| 4 – Layout | `layout/build.py` | Builds page directors (cover, chapter dividers, feature pages, text pages), encodes images as base64 data URIs, renders HTML with design tokens |
| 5 – QA | `pipeline/qa.py` | Verifies every claim in parallel against cited source passages; blocks if groundedness < threshold or prose score < 0.7 |
| 6 – Assemble | `pipeline/assemble.py` | HTML → PDF via render service (Paged.js + Chromium), linearizes with pikepdf, checks page count & file size |

### Key Files

- **`bookforge/schemas.py`** — Single source of truth for all data models (`BookBible`, `Chapter`, `ContentBlock`, `Claim`, `GeneratedImage`, `QAResult`, `BookArtifact`). Pydantic models double as JSON schemas fed to Claude's tool_use for structured output.
- **`bookforge/config.py`** — All settings via Pydantic `BaseSettings`. Every knob (models, print dimensions, cost caps, provider choice) is controlled here from `.env`.
- **`bookforge/clients/llm.py`** — Anthropic wrapper with three call patterns: `complete()` (text), `structured()` (forces JSON via tool_use), `vision_json()` (image scoring). Tracks per-model spend against `PER_BOOK_BUDGET_USD`.
- **`bookforge/clients/images.py`** — Dual-provider abstraction: OpenAI `gpt-image-1` or Gemini Imagen. Handles retries + aspect ratio mapping.
- **`bookforge/pipeline/grounding.py`** — Lexical TF-overlap retriever today; designed for pgvector drop-in when `DATABASE_URL` has pgvector.
- **`bookforge/pipeline/postprocess.py`** — De-AI finishing: filmic color grade + film grain + subtle vignette applied identically to all generated images.
- **`bookforge/layout/theme.py`** — Topic-keyword → curated design token mapping (e.g., "british meals" → `vintage_cookbook` preset). No random design; theme picker is deterministic.
- **`render/server.js`** — Express + Puppeteer + Paged.js on port 4000. Receives HTML, returns print-spec PDF.

### Infrastructure Services (docker-compose)

| Service | Port | Purpose |
|---------|------|---------|
| `api` | 8000 | FastAPI job submission + status polling |
| `worker` | — | Celery workers (content + images queues) |
| `render` | 4000 | Node.js Paged.js HTML→PDF renderer |
| `postgres` | 5432 | Metadata + pgvector embeddings (future) |
| `redis` | 6379 | Celery broker + results backend |
| `minio` | 9000/9001 | S3-compatible artifact storage |
| `ui` | 3000 | Next.js review dashboard (profile: `ui`) |

## Key Design Decisions

**Structured output via tool_use**: Claude never returns prose to parse. All pipeline outputs (`BookBible`, `Chapter`, etc.) are Pydantic schemas registered as tool schemas—Claude is forced to return valid JSON that's validated directly.

**Claims extracted post-editorial**: Claim extraction runs after all editing passes complete, so rewrites don't corrupt citations.

**Grounding blocks shipping**: `pipeline/qa.py` runs every claim against its cited source passage (8 concurrent). `GROUNDEDNESS_THRESHOLD=1.0` means 100% of claims must be supported. Lower this carefully.

**Spike ↔ Production parity**: `orchestrator.py` (CLI) and `workflow/tasks.py` (Celery) call the same stage functions. Adding a stage means touching both files.

**Budget guardrail is hard**: `cost.py` uses a thread-safe accumulator. Exceeding `PER_BOOK_BUDGET_USD` raises an exception mid-pipeline.

## Environment Variables

Critical variables in `.env` (see `.env.example` for full list):

```
ANTHROPIC_API_KEY          # Required
OPENAI_API_KEY             # For image generation (default provider)
GEMINI_API_KEY             # Alternative image provider

CONTENT_MODEL              # Default: claude-opus-4-8
DRAFT_MODEL                # Default: claude-haiku-4-5-20251001
VISION_QA_MODEL            # Default: claude-opus-4-8
IMAGE_PRIMARY_PROVIDER     # openai (default) or fal

PER_BOOK_BUDGET_USD        # Default: 50 — hard spend cap
GROUNDEDNESS_THRESHOLD     # Default: 1.0 — claim verification pass rate

DATABASE_URL               # PostgreSQL (required for Celery mode)
REDIS_URL                  # Redis (required for Celery mode)
S3_ENDPOINT / S3_BUCKET    # MinIO/S3 (required for Celery mode)

TRIM_WIDTH_IN / TRIM_HEIGHT_IN   # Print dimensions (default: 8.5×11)
TARGET_DPI                       # Default: 300
```
