# BookForge

Turn a **topic + source text (or a YouTube transcript)** into a **print-ready,
chapter-wise, image-rich ebook** (Amazon KDP PDF) — grounded against its sources,
edited to a novelist bar, and laid out with full-bleed AI imagery.

The project lives in [`bookforge/`](bookforge/), which contains the Phase-0
vertical spike plus the production scaffold.

## Pipeline

```
INGEST → BOOK BIBLE → CONTENT (chapter-wise) → IMAGES (style-locked)
       → LAYOUT/TYPESET → QA GATE → ASSEMBLE → print-ready PDF
```

| Stage | What it does |
|---|---|
| 0 Ingest | Text or YouTube (captions → Whisper fallback) → chunked source corpus |
| 1 Book Bible | Title, art-style lock, voice lock, chapter plan (structured output) |
| 2 Content | Grounded, chapter-wise writing + editorial cascade + claim extraction |
| 3 Images | Style-locked candidates → vision scoring → right-size to 300 DPI |
| 4/6 Layout + Assemble | HTML/CSS → Paged.js PDF → linearize → file-size preflight |
| 5 QA | **Blocking** claim verification (no hallucination) + prose-quality gate |

## Quick start (local Phase-0 spike, no Docker)

```bash
cd bookforge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install playwright && playwright install chromium   # local HTML→PDF

cp .env.example .env        # then fill in your own (rotated) API keys

python -m bookforge.cli \
  --topic "100 Forgotten British Meals Our Grandparents Lived On" \
  --text-file sample_source.txt \
  --chapters 1 --pages 100
```

Output PDF lands in `bookforge/artifacts/books/<book_id>/interior.pdf`.

## Full stack (Docker)

```bash
docker compose -f bookforge/infra/docker-compose.yml up --build
# api      :8000   (POST /books, GET /tasks/{id}, GET /books/{id}/interior.pdf)
# render   :4000   (Paged.js HTML→PDF)
# minio    :9001   (artifact storage console)
```

## Accuracy guarantee

Nothing unverified ships: every atomic claim is checked against its cited source
by an independent judge. A book only passes when groundedness meets the configured
threshold (default 100%) and a human signs off in the review UI.

## Authentication

The API and UI are gated behind JWT auth (self-hosted; `python-jose` + bcrypt).
Every `/books*` endpoint requires a bearer token, and each user only sees their
own books. Users with `is_admin=true` see everything.

**One-time setup:**

```bash
cd bookforge
# Bring up Postgres (and Redis) so the DB is reachable
docker compose -f infra/docker-compose.yml up -d postgres redis

# Apply schema + seed a test admin + a regular user (idempotent).
alembic upgrade head
python -m scripts.seed_users
```

The seed script creates two dev accounts (rotate before any non-local use):

| Role  | Email                     | Password              |
|-------|---------------------------|-----------------------|
| admin | `admin@bookforge.local`   | `admin-dev-password`  |
| user  | `user@bookforge.local`    | `user-dev-password`   |

Override via `--admin-email`, `--admin-password`, `--user-email`,
`--user-password`, or the `SEED_ADMIN_PASSWORD` / `SEED_USER_PASSWORD` env vars.

Any books that existed before auth was enabled are backfilled to the admin on
first seed.

**API usage:**

```bash
# Log in → grab a token (host port is whatever docker-compose maps; e.g. 8010)
TOKEN=$(curl -s -X POST localhost:8010/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"user@bookforge.local","password":"user-dev-password"}' \
  | jq -r .access_token)

# Every /books* call needs Authorization: Bearer <token>
curl -H "Authorization: Bearer $TOKEN" localhost:8010/books
```

Admins can invite additional users:

```bash
curl -X POST localhost:8010/auth/register \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"email":"new@example.com","password":"pw","is_admin":false}'
```

Set `AUTH_ENABLED=false` in `.env` to bypass auth entirely (useful for local
CLI / spike work).

## Security

API keys live only in `bookforge/.env`, which is gitignored — never commit secrets
or paste them into chat. All services read secrets from the environment.
`JWT_SECRET` **must** be rotated to a long random string before any deployment.

See [`bookforge/README.md`](bookforge/README.md) for full module-level details.
