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

## Security

API keys live only in `bookforge/.env`, which is gitignored — never commit secrets
or paste them into chat. All services read secrets from the environment.

See [`bookforge/README.md`](bookforge/README.md) for full module-level details.
