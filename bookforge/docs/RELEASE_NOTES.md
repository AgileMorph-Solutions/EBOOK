# Release Notes — Dynamic 200-page Guides, Production Hardening & Job History

_Author: Tejas Deshmukh_

This release makes the **UI-driven** pipeline produce full-length, recipe-quality
books from any topic, hardens the single-node (no-Docker) deployment, and adds a
usable job-history experience.

---

## 1. Recipe-grade dynamic guides — the 200-page fix

**Problem.** The hand-authored recipe books (`bookforge/books/pest_control.py`)
produce dense ~250-page guides because they hard-set generous per-section quotas
(`full_card_quota=10, list_quota=35, image_quota=3`). The **UI path** builds the
`BookBible` dynamically in `pipeline/bible.py`, and its quota math thinned every
section down to ~3 cards / ~12 tips when spreading a page target across many
tiers — producing short, padded, whitespace-heavy pages (the QA gate flagged
"excessive empty whitespace / oversized panel / wasted space").

Both paths render through the **same** machinery (`content.write_section` →
`guide_images` → `layout/guide.build_practical_html`), so the only lever is the
bible.

**Change (`pipeline/bible.py`).**
- Scale the **section count** to the page target instead of thinning sections
  (`_base_section_target`): ~8 base topics × 2 dense tiers ≈ 16 sections for 200 pages.
- Set **recipe-grade density floors** in `_section_quotas` (~9 cards + ~32 tips +
  3 images per section).
- Prompt now asks for **distinct** base topics so long books don't repeat themselves.

**Result (measured, generated end-to-end through the UI):**

| | Old dynamic path | New dynamic path |
|---|---|---|
| Sections | few, thin | **16**, dense |
| Cards / tips per section | ~3 / ~12 | **9 / 32** |
| Example book pages | ~12 (padded) | **217** |
| QA passed | — | ✅ (groundedness 1.0, prose 0.8) |

---

## 2. Backend production hardening (`bookforge/`)

Target: robust **single-node, in-process** deployment (FastAPI `BackgroundTasks`
+ local Playwright render; no Docker/Celery required).

- **Pinned render dependency** — `playwright` added to `requirements.txt`
  (previously worked only because it was installed by hand). Run
  `playwright install chromium` once.
- **Lifespan startup** (`api/main.py`) — replaces deprecated `on_event`:
  - fails loud if `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` are missing;
  - **recovers orphaned jobs** — any job left `queued`/`running` by a prior
    crash/restart is marked `failed: "interrupted by server restart"` (no more
    forever-stuck jobs).
- **Concurrency guard** — a `BoundedSemaphore(MAX_CONCURRENT_BOOKS=2)` around the
  pipeline so parallel submissions can't exhaust Chromium/RAM; extra jobs wait as
  `queued`.
- **Rich `/health`** — `{ok, version, storage, render, anthropic_key, gemini_key,
  max_concurrent_books}` (booleans only, never secret values).
- **Failure logging** — `log.exception(...)` on pipeline errors; the catch-all
  still isolates one job's failure from the API.
- **`DELETE /books/{id}`** — archive endpoint that removes a job and its artifacts.

New setting: `MAX_CONCURRENT_BOOKS` in `config.py`.

---

## 3. UI — dark "forge" theme + job history (`app/`)

- **Cinematic dark theme** — warm-charcoal surfaces, molten-ember accent, glass
  panels, ambient glow + film grain. Implemented by re-pointing the semantic
  design tokens in `tailwind.config.ts` + `app/globals.css`, so every component
  reflowed cohesively.
- **`/jobs` history page** — full-width list with a status filter
  (All / Done / Active / Failed) and a summary line; the header "Jobs" nav points
  here.
- **Re-download from the list** — completed `JobCard`s expose a one-click download
  (no need to open the detail page).
- **Archive** — the previously-disabled button now calls `DELETE /books/{id}` and
  returns to `/jobs`.
- **Runtime API URL** — the client reads the API base from `/api/config` at request
  time (`dynamic = "force-dynamic"`), so one production build works across
  dev/staging/prod without a rebuild.

---

## 4. Running in production (single-node, no Docker)

```bash
cd bookforge
# one-time: python -m venv .venv && .venv/bin/pip install -r requirements.txt
#           .venv/bin/playwright install chromium ; cp .env.example .env (fill keys)
./run_local.sh          # builds the UI, starts API :8000 + UI :3000
# API_PORT=8000 UI_PORT=3000 SKIP_UI_BUILD=1 ./run_local.sh
```

`run_local.sh` auto-detects a local Node build, loads `.env`, runs a production
`next build`/`next start`, and starts `uvicorn` (no `--reload`). Render defaults to
local Playwright (`RENDER_URL` empty); the Node render service is optional.

---

## 5. Testing

- **Unit** (`tests/`, pytest): API upload + content guardrails — 9 passing.
- **UI unit** (`app`, vitest): 6 passing.
- **End-to-end (opt-in, real spend)** — Playwright drives the real UI:
  - `python -m tests.e2e_ui_smoke` — cheap 1-section sanity check.
  - `python -m tests.e2e_ui_heavy` — two full books; asserts `page_count ≥ 200`,
    `qa.passed`, size ≤ cap, and that the **downloaded** PDF's page count matches
    the artifact.

---

## 6. Verification performed

- `pytest tests/test_api_upload.py tests/test_content_guardrails.py` → **9 passed**.
- `npm test` (vitest) → **6 passed**; `tsc --noEmit` clean; `next build` clean
  (routes: `/`, `/jobs`, `/books/[id]`, `/api/config`).
- Live UI generation at `target_pages=200` → **217 pages**, QA passed, 18.4 MB.
- API restart → rich `/health` OK, orphan-recovery ran, history intact.
