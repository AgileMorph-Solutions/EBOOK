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

## Full stack (Docker)

```bash
docker compose -f infra/docker-compose.yml up --build
# api      :8000   (POST /books, GET /tasks/{id}, GET /books/{id}/interior.pdf)
# render   :4000   (Paged.js HTML→PDF)
# minio    :9001   (artifact storage console)
# scale workers:  docker compose -f infra/docker-compose.yml up --scale worker=4
```

Submit a job:
```bash
curl -X POST localhost:8000/books -H 'content-type: application/json' \
  -d '{"topic":"Forgotten British Meals","text":"...","chapter_limit":1}'
```

## 🔴 Security

The API keys pasted during setup are **compromised** — revoke them and put
**rotated** keys in `.env` (gitignored). Never commit `.env` or paste keys into
chat. All services read secrets from env only.

## Accuracy guarantee

Nothing unverified ships: every atomic claim is checked against its cited source
by an independent judge (`pipeline/qa.py`). A book only passes when groundedness
≥ `GROUNDEDNESS_THRESHOLD` (default 100%) and a human signs off in the review UI.
