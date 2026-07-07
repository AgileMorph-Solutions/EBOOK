# BookForge Containers — Who Does What

Quick reference for the docker-compose stack in `infra/docker-compose.yml`.

## TL;DR — Which container generates the ebook?

**`bookforge-api-1`** is the container that actually runs the ebook generation
pipeline today.

When you `POST /books`, the API enqueues the job as a FastAPI **BackgroundTask**
that runs **in-process inside the api container** (see
`bookforge/api/main.py::_run_pipeline` → `pipeline/orchestrator.py::run_book`).
So all the heavy work — Claude calls, image generation, HTML assembly — happens
inside `bookforge-api-1`.

The `worker` and `beat` containers exist for the Celery-based production DAG
(`bookforge/workflow/tasks.py`), but the HTTP path does **not** currently
dispatch to them. If you want to see logs while a book is generating:

```bash
docker logs -f bookforge-api-1
```

The one exception is the final PDF rendering step, which is delegated over HTTP
to `bookforge-render-1` (Paged.js + Chromium).

## Full container map

| Container | Image / Build | Port (host → container) | Purpose |
|---|---|---|---|
| `bookforge-api-1` | `infra/Dockerfile.python` | `8010 → 8000` | FastAPI. Accepts job submissions, serves status/downloads, **and runs the pipeline itself as background tasks**. |
| `bookforge-worker-1` / `-2` | `infra/Dockerfile.python` | — | Celery workers listening on `content,images,celery` queues. Reserved for the Celery DAG path (`workflow/tasks.py`); not used by the current HTTP flow. |
| `bookforge-beat-1` | `infra/Dockerfile.python` | — | Celery beat scheduler for periodic tasks. Idle unless a schedule is registered. |
| `bookforge-render-1` | `infra/Dockerfile.render` | `4010 → 4000` | Node.js + Paged.js + Puppeteer. Called by the api at the **assemble** stage to turn HTML into a print-spec PDF. |
| `bookforge-ui-1` | `node:20-slim` (dev, `next dev`) | `3010 → 3000` | Next.js review dashboard at http://localhost:3010. Talks to the API at `localhost:8010`. |
| `bookforge-postgres-1` | `pgvector/pgvector:pg16` | — | Metadata store (users, `BookRecord` ownership) and future pgvector embeddings. |
| `bookforge-redis-1` | `redis:7-alpine` | — | Celery broker + result backend. Also the queue Celery workers idle on. |

Named volumes:
- `artifacts` → mounted at `/home/forge/artifacts` in api/worker/beat. Holds
  `books/<book_id>/{status.json, bible.json, chapters.json, images.json,
  interior.pdf}`. **This is where a running/finished book lives.**
- `pg-data`, `redis-data` — standard state volumes.

## Pipeline stage → container

| Stage | Runs in | Notes |
|---|---|---|
| ingest | `api` | Text / YouTube transcript → chunked passages. |
| bible | `api` | One Claude call → title, chapter/section plan. |
| content | `api` | Sequential per-section Claude calls. **This is where jobs spend most of their time and where "stuck at content" hangs live.** |
| images | `api` | OpenAI / Gemini image gen + Claude vision scoring. |
| layout | `api` | Builds HTML with base64-encoded images. |
| assemble | `api` → HTTP → `render` | HTML sent to `http://render:4000`; PDF returned. Only step that leaves the api container. |
| qa (narrative only) | `api` | Claim verification against source passages. |

## Cheat sheet

```bash
# Follow the ebook generation live
docker logs -f bookforge-api-1

# See PDF rendering
docker logs -f bookforge-render-1

# Peek at persisted job state
docker exec bookforge-api-1 ls /home/forge/artifacts/books/

# Restart just the pipeline runner (kills in-flight background tasks;
# lifespan hook will mark them as failed on the next boot)
docker compose -f infra/docker-compose.yml restart api

# Flush the Celery broker (only relevant once the Celery path is wired up)
docker exec bookforge-redis-1 redis-cli FLUSHALL
```
