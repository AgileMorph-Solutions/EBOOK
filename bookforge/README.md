# BookForge

Turn a **topic + source text (or a YouTube transcript)** into a **print-ready,
chapter-wise, image-rich ebook** (Amazon KDP PDF) — grounded, edited to a
novelist bar, and laid out with full-bleed AI imagery.

See the full architecture in
[`../../.claude/plans/synthetic-scribbling-elephant.md`](../). This repo is the
**Phase-0 vertical spike + production scaffold**.

## Pipeline

```
INGEST → BOOK BIBLE → CONTENT (chapter-wise) → IMAGES (style-locked)
       → LAYOUT/TYPESET → QA GATE → ASSEMBLE → print-ready PDF
```

| Stage | Module | What it does |
|---|---|---|
| 0 Ingest | `pipeline/ingest.py` | Text or YouTube (captions → Whisper fallback) → chunked source corpus |
| 1 Book Bible | `pipeline/bible.py` | Title, art-style lock, voice lock, chapter plan (structured output) |
| 2 Content | `pipeline/content.py` | Grounded, chapter-wise writing + editorial cascade + claim extraction |
| 3 Images | `pipeline/images.py` | Style-locked candidates → vision scoring → right-size to 300 DPI |
| 5 QA | `pipeline/qa.py` | **Blocking** claim verification (no hallucination) + prose-quality gate |
| 4/6 Layout+Assemble | `layout/`, `pipeline/assemble.py` | HTML/CSS → Paged.js PDF → linearize → file-size preflight |

## Quick start (local Phase-0 spike, no Docker)

```bash
cd bookforge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install playwright && playwright install chromium   # local HTML→PDF

cp .env.example .env        # then fill ROTATED keys (see security note below)

python -m bookforge.cli \
  --topic "100 Forgotten British Meals Our Grandparents Lived On" \
  --text-file sample_source.txt \
  --chapters 1 --pages 100
```

Output PDF lands in `./artifacts/books/<book_id>/interior.pdf`.

## Run the app (single-node, no Docker)

Serves the FastAPI backend (in-process pipeline, local Playwright render) plus a
**production** build of the Next.js UI:

```bash
cd bookforge
playwright install chromium         # one-time (local HTML→PDF)
./run_local.sh                      # UI :3000 + API :8000
# API_PORT=8000 UI_PORT=3000 SKIP_UI_BUILD=1 ./run_local.sh
```

Open **http://localhost:3000** to create a book, watch pipeline progress, and
download the PDF. The **/jobs** page is the history view (filter by status,
re-download past books). `GET /health` reports version + key/render status.

Dynamic guides scale to length: set `target_pages` (e.g. 200) and the bible plans
~16 dense sections (recipe-grade ~9 cards + ~32 tips each). See
[`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md).

## Full stack (Docker)

```bash
docker compose -f infra/docker-compose.yml up --build
# ui       :3000   (create books, poll status, download PDF)
# api      :8000   (POST /books, GET /books/{id}, GET /books/{id}/download)
# render   :4000   (Paged.js HTML→PDF)
# scale workers:  docker compose -f infra/docker-compose.yml up --scale worker=4
```

Open **http://localhost:3000** to create a book via the web UI (topic + pasted text, file upload, or YouTube URL).

Or submit via API:
```bash
curl -X POST localhost:8000/books -H 'content-type: application/json' \
  -d '{"topic":"Forgotten British Meals","text":"...","chapter_limit":1}'
```

## Authentication

The API is gated by JWT auth (self-hosted; `python-jose` + bcrypt), and every
`/books*` endpoint is scoped to the authenticated user. Users with
`is_admin=true` see all books.

**One-time setup** (from `bookforge/`):

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis
alembic upgrade head
python -m scripts.seed_users
```

Seeds two dev accounts (rotate before non-local use):

| Role  | Email                     | Password              |
|-------|---------------------------|-----------------------|
| admin | `admin@bookforge.local`   | `admin-dev-password`  |
| user  | `user@bookforge.local`    | `user-dev-password`   |

Override with `--admin-email`, `--admin-password`, `--user-email`,
`--user-password`, or via `SEED_ADMIN_PASSWORD` / `SEED_USER_PASSWORD`.
Legacy books (created pre-auth) are backfilled to the admin.

**Auth endpoints:**
- `POST /auth/login` `{email, password}` → `{access_token}`
- `GET /auth/me` (bearer) → current user
- `POST /auth/register` (admin-only) → create a new user

```bash
TOKEN=$(curl -s -X POST localhost:8010/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"user@bookforge.local","password":"user-dev-password"}' | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" localhost:8010/books
```

Set `AUTH_ENABLED=false` in `.env` to disable auth (CLI spike / local debug).

## 🔴 Security

The API keys pasted during setup are **compromised** — revoke them and put
**rotated** keys in `.env` (gitignored). Never commit `.env` or paste keys into
chat. All services read secrets from env only. `JWT_SECRET` **must** be rotated
to a long random string before any deployment.

## Accuracy guarantee

Nothing unverified ships: every atomic claim is checked against its cited source
by an independent judge (`pipeline/qa.py`). A book only passes when groundedness
≥ `GROUNDEDNESS_THRESHOLD` (default 100%) and a human signs off in the review UI.
