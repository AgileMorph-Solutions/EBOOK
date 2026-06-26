# BookForge — Ebook Generation Flow

End-to-end pipeline that turns a topic + source (pasted text or YouTube URL)
into a print-ready KDP interior PDF. Same stage code is shared by the CLI
spike (`pipeline/orchestrator.py`) and the production Celery path
(`workflow/tasks.py`).

## API Surface

```
POST /books              # submit a job; returns book_id
GET  /books/{book_id}    # poll status: queued | running | done | failed
GET  /books/{book_id}/download   # download final interior.pdf
```

Submission body:

```json
{
  "topic": "string (required)",
  "text": "string (optional - pasted source)",
  "youtube_url": "string (optional - YouTube URL)",
  "chapter_limit": 1
}
```

Exactly one of `text` or `youtube_url` is required. `chapter_limit` is
optional; without it the bible planner chooses chapter count.

## Stage Flow

```
                 +-------------+
   YouTube URL   |             |
   or pasted --->| 0. Ingest   |  -> SourceDoc (passages + stable ids)
   text          |             |
                 +-------------+
                        |
                        v
                 +-------------+
                 | 1. Bible    |  -> BookBible (title, art, voice, chapters)
                 +-------------+
                        |
                        v
                 +-------------+
                 | 2. Content  |  per-chapter Claude pipeline
                 +-------------+      draft -> dev-edit -> line-edit -> de-AI
                        |             then claim extraction
                        v
                 +-------------+
                 | 3. Images   |  per image_brief: N candidates,
                 +-------------+      vision score, right-size, filmic finish
                        |
                        v
                 +-------------+
                 | 4. QA       |  parallel claim verification
                 +-------------+      groundedness + prose score
                        |
                        v
                 +-------------+
                 | 5. Layout   |  director picks page templates,
                 +-------------+      embeds images as data URIs
                        |
                        v
                 +-------------+
                 | 6. Assemble |  HTML -> render service -> PDF
                 +-------------+      linearize, size + page-count checks
                        |
                        v
                  interior.pdf
```

---

### Stage 0 — Ingest (`pipeline/ingest.py`)

Normalizes input into a `SourceDoc` with chunked passages so every downstream
claim can cite a stable passage id.

- **Text input**: whitespace-normalized, chunked.
- **YouTube input**:
  1. Pull captions via `youtube-transcript-api` (cheap, exact).
  2. If no captions, fall back to ASR: yt-dlp downloads audio, `faster-whisper`
     transcribes.
- Chunks the raw text into passages of ~1200 chars on paragraph/sentence
  boundaries. Each passage gets an id like `p0000`.

Output: `SourceDoc(topic, origin, origin_ref, text, passages)`.

### Stage 1 — Book Bible (`pipeline/bible.py`)

A single Claude call (structured output via `tool_use`) produces the global
plan:

- `title`, `subtitle`
- `art_style`: palette, `style_suffix`, `negative_prompt` (applied to every
  image)
- `voice`: tone/persona, `banned_patterns` (clichés to keep out of prose and
  image prompts)
- `chapters`: list of `ChapterPlan(index, title, summary, page_budget)`

`chapter_limit` truncates this list before content runs. The bible is the
single source of truth for every later stage.

### Stage 2 — Content (`pipeline/content.py`)

Per chapter (parallelized in Celery, sequential in the CLI):

1. **Retrieve** relevant passages via lexical TF-overlap (`grounding.py` —
   pgvector swap-in ready).
2. **Draft** the chapter as a list of typed `ContentBlock`s (heading, prose,
   `ingredients`, `method`, `pull_quote`) with optional `image_brief`s.
3. **Dev edit** for structure / pacing / cuts.
4. **Line edit** for rhythm / specificity.
5. **De-AI** pass removes signature LLM tells.
6. **Claim extraction** — *after* all edits — produces atomic `Claim`s with
   `source_passage_ids`. Citation integrity is preserved because rewrites
   can't corrupt claims.

Output: `Chapter(index, title, blocks=[ContentBlock(..., image_brief?, claims)])`.

### Stage 3 — Images (`pipeline/images.py`)

For every `ContentBlock` carrying an `image_brief`:

- Build the prompt:
  - `_build_prompt` wraps the brief subject in an art-direction recipe
    (camera/lens/light for photos; locked style for illustrations).
  - Folds `bible.art_style.negative_prompt` and `bible.voice.banned_patterns`
    into the negative.
  - If `brief.needs_in_image_text` and `brief.caption` are set, injects a
    literal-text directive ("Render the words ... clearly"). Otherwise
    appends the hard `no text / no logos / no labels` clause that kills the
    #1 AI-illustration tell.
- Generate `image_candidates_per_page` candidates via the configured
  provider (`gpt-image-1` or Imagen).
- Vision-score each candidate (relevance 60% + aesthetic 40%); reject NSFW;
  keep the highest-ranked even if minor artifacts (so a page is never
  imageless over a strict score).
- Right-size to the placed box at 300 DPI (per-aspect box dims so feature
  band photos are not portrait-sized).
- Apply filmic post-process (`postprocess.finish`): subtle grade + grain +
  vignette for cohesive look across all generated art.

Output: `list[GeneratedImage(block_ref, storage_key, w, h, score, flagged_for_review)]`.

### Stage 4 — QA (`pipeline/qa.py`)

Parallel verification (8 concurrent) of every extracted `Claim` against its
cited passage. Result includes:

- `groundedness` (fraction supported)
- `unsupported_claims` list
- `prose_quality_score`
- `passed = groundedness >= GROUNDEDNESS_THRESHOLD and prose >= 0.7`

A failing QA does not block the PDF being produced — it sets
`artifact.qa.passed = false` and the consumer decides what to do.

### Stage 5 — Layout (`layout/build.py`)

The director maps content to a small library of fixed-size page templates:

| Template | When | Visual |
|---|---|---|
| `cover` | once, page 1 | full-bleed photo + title block + badge |
| `divider` | start of each chapter | color-field opener (stripe + big serif) |
| `feature(overlay)` | first photo of a chapter | full-bleed photo + title overlaid + body |
| `feature(band)` | subsequent photos | accent title band + photo + 2-col body |
| `quote` | `pull_quote` blocks | centered pull-quote on a panel |
| `text` | text-only / continuation | designed text page (drop cap optional) |

Key behaviors:

- **Image embedding**: each image is encoded as a `data:image/jpeg;base64`
  URI so the render service runs offline (no remote fetches).
- **One-col threshold**: short feature bodies (<350 chars) get a 1-col body
  so type does not float in an empty 2-col grid.
- **Spillover**: when a body exceeds the soft cap for its template
  (1400 chars for 2-col feature, 2000 for textpage), the tail flows onto
  one or more continuation `text` pages instead of being silently clipped.
- **Page-budget logging**: per chapter, the actual page count is compared
  against `ChapterPlan.page_budget`; drift is logged.
- **Theme**: `theme.pick(...)` deterministically maps title keywords to a
  curated design-token preset (no random design).

Output: single HTML document with inline CSS and inline images.

### Stage 6 — Assemble (`pipeline/assemble.py`)

- POST HTML to the render service (`render/server.js`: Express + Puppeteer +
  Paged.js on port 4000). The renderer uses `waitUntil: "load"` (no network
  with inline data URIs), `emulateMediaType("print")`, and prints with
  `preferCSSPageSize: true`.
- Linearize the PDF with `pikepdf` for fast web serving.
- Check `file_size_mb` against `max_file_size_mb` budget.
- Check observed page count against `sum(chapter.page_budget)`; flag if
  drift > 30%.
- Store as `books/<book_id>/interior.pdf` in the local artifact volume.

Output: `BookArtifact(book_id, interior_pdf_key, page_count, file_size_mb, qa)`.

---

## Job Lifecycle

```
POST /books
   |
   v
status.json  =>  { status: "queued" }
   |
   v
(BackgroundTask, thread pool)
   |
   v
status.json  =>  { status: "running" }
   |
   v   stages 0 -> 6 (above)
   |
   v
status.json  =>  { status: "done", artifact: {...} }   on success
                  { status: "failed", error: "..." }   on exception
```

Disk-persisted `status.json` per book id under
`/home/forge/artifacts/books/<book_id>/status.json` so the API survives
restarts. `GET /books/{id}` reads that file; `GET /books/{id}/download`
streams `interior.pdf`.

## Infrastructure

| Service | Role |
|---|---|
| `api` (FastAPI, :8000) | submission + status + download endpoints, runs pipeline in-process via `BackgroundTasks` + thread pool |
| `render` (Node Express + Puppeteer + Paged.js, :4000) | HTML -> print PDF |
| `redis` | Celery broker (production mode only) |
| `postgres` | metadata + future pgvector RAG |
| local volume `artifacts` | source of truth for all generated bytes |

Storage is **local fs only** (`BOOKFORGE_USE_S3=0`,
`BOOKFORGE_LOCAL_STORE=/home/forge/artifacts`). S3/MinIO has been removed.

## Cost & Safety Rails

- `cost.py` carries a thread-safe accumulator. Every Claude and image call
  charges into it; exceeding `PER_BOOK_BUDGET_USD` raises mid-pipeline.
- `GROUNDEDNESS_THRESHOLD` (default 1.0) gates `qa.passed`.
- `MAX_FILE_SIZE_MB` gates the assembled PDF.

## Key Design Decisions

- **Structured output everywhere.** Claude never returns prose to parse —
  every stage uses a Pydantic schema registered as a `tool_use` schema, so
  outputs are validated as data.
- **Claims after editing.** Claim extraction runs after dev-edit, line-edit
  and de-AI passes, so rewrites cannot break citations.
- **Spike/production parity.** `pipeline/orchestrator.py` (CLI) and
  `workflow/tasks.py` (Celery) call the same stage functions. Adding a stage
  means touching both files.
- **Fixed designed pages, not reflow.** Each page is a self-contained
  composition with explicit positions. The render service prints them 1:1
  via `preferCSSPageSize`. Long bodies spill onto continuation pages rather
  than triggering CSS column overflow.
- **No remote fetches at render time.** Images are inlined as base64 data
  URIs in the HTML.
