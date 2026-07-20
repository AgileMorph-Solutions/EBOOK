# BookForge — Single-Pass Pipeline Plan: Bug Closure, Test Closure & Algorithm Upgrades

**Date:** 2026-07-11 · **Status:** Proposed (audits complete, awaiting implementation go-ahead)
**Scope:** `app/bookforge/bookforge/` — builds on the never-fail/speed overhaul shipped earlier today.

---

## 1. Executive summary

Earlier today we shipped the never-fail overhaul: per-book budgets, always-ship repair ladders, quota-aware parking, full checkpoint/resume, parallel generation, prompt caching. It works — all three previously-stuck production books shipped within ~90 minutes of deploy, and "failed" is now only reachable for cancelled jobs, unusable sources, provider hard-downs, or pre-content budget exhaustion.

This plan closes what three adversarial audits of the new code found, and replaces the remaining *iterate-until-it-fits* algorithms with a **single-pass architecture**:

> **plan right-sized → render once → one exact measured reconciliation → verify → ship clean**

Headline targets:

| Metric | Today (measured) | Target |
|---|---|---|
| Chromium renders per book | 17–44 (~24 min of a 34-min run) | **≤ 6** |
| Assemble passes per book | 5 | **≤ 2** |
| Page-estimate error | 1.6× undershoot (drift 0.44–0.66) | **< 0.15** |
| Judge (QA/scoring) spend per book | ~$3–6 (all Opus) | **~$0.5–1** |
| QA gate wall-clock | ~2.5 min | **~1 min** |
| Images shipped flagged-for-review | 50–70% | materially lower (text-briefs re-routed) |
| Books shipped with warnings | normal path for defects | **rare** (defects resolved, not tolerated) |
| Known safety/correctness bugs | 4 high, 4 medium, 8 low | **0** (each fix lands with a regression test) |

---

## 2. What the audits found

Three parallel audits ran against the live system and today's production logs: (A) adversarial concurrency review, (B) error-path/resume contract review, (C) algorithm inventory with measured timings.

### 2.1 High-severity bugs (fix first)

| # | Bug | Impact |
|---|---|---|
| **H1** | **No per-book execution mutex.** `/regenerate`, `/rerender`, and the auto-resume loop all gate on lock-free status reads — the same book can run twice concurrently (double-click, or resume-tick + operator action). | Interleaved checkpoint writes, duplicate LLM/image spend; cascades into H1b: the budget registry's overwrite-then-pop leaves the surviving run on the **uncapped** process-global budget. |
| **H2** | **Resume-after-mid-content-park corruption.** If a book parks during content (before `chapters.json` exists), resume **rebuilds the bible** (non-deterministic → different plan) yet merges the old plan's per-section checkpoints into it by index. | Sections ship under the wrong headings — silent content corruption on the *most common* park path (provider outage mid-content). |
| **H3** | **Safety findings lost on budget exhaustion (SAFETY).** `BudgetExceeded` during the QA gate's LLM-judge phase discards the already-computed deterministic safety findings; a synthesized `safety_passed=True` result is persisted to `qa.json`, sanitize never runs, and resume replays the "passing" result. | An unsafe instruction can ship un-neutralized. |
| **H4** | **`/regenerate` full-rerun is budget-poisoned and secretly resumes.** It resets status fields but not storage checkpoints: `spend.json` survives (a book that died at $48/$50 instantly re-fails), and surviving `bible/chapters` checkpoints mean "full rerun" actually reuses the old content. | Regenerate is broken for its primary use case. |

### 2.2 Medium severity

- **M1** — Narrative claim-verify swallows `BudgetExceeded`/cancel/deadline (missing `never_swallow` guard): a cancelled book keeps burning the whole claim fan-out.
- **M2** — Watchdog TOCTOU: can flip a just-completed `done` book back to `waiting` (re-run + spend).
- **M3** — Page-scale/vision loops save `chapters.json` without re-saving `qa.json`: every park mid-layout re-pays the full QA gate (~$1–4 per resume).
- **M4** — Two image-quota counters (`image_quota_park.json` vs status `quota_parks`) with different reset lifecycles; regenerate resets only one → the "park until quota reset" budget silently disappears on the next run.
- **M5** — Per-image checkpoint dumps the full image list under the pool lock: O(n²) serialization degrades the image pool toward serial.

### 2.3 Low severity (batched)

45s per-minute-quota sleep inside pool workers (blocks cancel; shorter than the 60s window it waits on) · stale QA/images reuse + duplicate issue-append in the final vision pass · `_DONE_CLEARS` misses `quota_parks`/`watchdog_alerted` · OpenAI 429s parsed with Google's midnight-Pacific reset horizon · dead error classes (`ImageCoverageError`, `ContentQAError` have no raise sites) · unlocked lazy client init · benign heartbeat-throttle race · legacy fit-report global unguarded on the render-service path.

**Verified safe** (attacked, no bug): pool/context propagation, `_inflight` semaphore vs retries, lock ordering (no deadlock), atomic status writes, spend-before-park ordering, qa.json sha self-invalidation, cancel coverage, never-swallow coverage everywhere except M1.

### 2.4 Test gaps (9)

The fresh-book **parallel content path has never executed** — not in tests, not live (all three validation books resumed past content). Also untested: final vision-QA loop (monkeypatched out), cache warm-up, watchdog tick, resume-slot fairness, image partial-ship end-to-end, deadline-inside-pool, both recovery endpoints, and the budget-exhausted QA safety path.

### 2.5 Where the time actually goes (measured, production logs)

Full-book Chromium render ≈ 24–36s. Per book today: lost-diners **34m14s / ~44 renders / 5 assemble passes**; cowboy 18m35s / 17 renders; great-depression 9m08s / 9 renders.

Root cause of the render storm: the **pre-render page estimator undershoots ~1.6×** (predicted 196–210 pages, rendered 326–348), so the pipeline discovers the real page count by rendering, trims a little (`removed=9,1,1` — a linear crawl), and re-renders — up to 4×3×4 nested loop iterations. The vision-QA revise then *re-inflated* one book 301→304 (corrections fighting each other). Secondary costs: every judge task (claim verify ≤120/book, prose scoring every chapter, ~115 image scores, ~30 vision pages) is hard-wired to **Opus**; the fit JS runs a 22-iteration binary search with forced reflow per probe on every region of every render; retrieval is TF-overlap with no IDF; 50–70% of images ship flagged (dominant reject: Imagen cannot render text).

---

## 3. The plan

Three phases. Phase 1 is small-diff correctness/safety work; Phase 2 locks it in with tests; Phase 3 is the architecture change that removes loops as the normal mode of operation.

### Phase 1 — Bug fixes (ordered, each with its regression test)

1. **H3 (safety).** `qa.run_guide_gate` catches `BudgetExceeded` from the judge phase *internally* and still returns a `QAResult` built from the real deterministic findings (adds a "judges skipped: budget exhausted" issue). The gate never raises budget errors; sanitize always sees true `safety_passed`. Resume path: after a `qa.json` hit with `safety_passed=False`, re-run `sanitize_unsafe_steps` (deterministic, idempotent) and re-save chapters.
2. **H1 (mutex) + budget simplification.** Atomic per-book claim set under `_active_jobs_lock`, claimed in `_execute_pipeline` and both rerender closures; endpoints 409 on conflict; resume defers. `cost.py`: delete the book-id registry — store the `Budget` object directly in the ContextVar (workers inherit it via `ctx_pool`); the collision class disappears structurally.
3. **H2 (resume).** Reuse `cached_bible` whenever it exists (never rebuild over checkpoints — also saves tokens); clear per-section checkpoints whenever a fresh bible *is* built.
4. **H4 (regenerate).** New `checkpoints.clear_for_full_rerun(book_id)` deletes spend/sections/qa/quota-park/bible/chapters/images metadata **and** generated image files (a new plan must not adopt old images by filename); called only on the full-rerun branch.
5. **M1–M5** as specified: never-swallow guard in narrative verify; watchdog compare-and-set under lock; `save_qa` beside every `save_chapters` in the scale/vision loops; regenerate clears the quota-park file; image checkpoint debounced (every 5 slots, dump outside the lock).
6. **L1–L8** in one batch commit (sliced quota sleep with cancel checkpoints, vision-pass issue rebuild, done-state clears, provider-aware quota parsing, dead-class deletion, init lock, heartbeat lock, service-path global).

### Phase 2 — Test closure

Nine suites (extend `tests/test_robustness_overhaul.py`, new `tests/test_endpoints.py`): fresh-book parallel content (pool fan-out, fatal abort, renumbering, per-section checkpoints) · final vision-QA decision table · watchdog including completed-during-tick · resume-slot fairness · image partial-ship end-to-end with quota-park counting · deadline raised inside a pool worker → park → checkpoint-intact resume · both recovery endpoints (checkpoint clearing, 409s) · H3 safety regression · H2 cached-bible regression.

### Phase 3 — Single-pass architecture ("no loops")

**A. Calibrated page estimator + exact one-shot trim** *(kills ~70% of runtime)*
- Offline `scripts/calibrate_density.py`: regress actual rendered page counts on card/quick/photo/section counts across the 30 books already on disk; correct the constants in `bible._guide_density` / `page_limits.estimate_guide_pages`. Zero LLM cost; re-run when layout CSS changes.
- Runtime: compute **measured pages-per-unit from the first render** and trim the exact overage in one pass (`ceil(overage / measured_ppu × 1.1)`), replacing the ratio formula that crawled one unit per pass. Books land in the page band after one trim + one verify render.
- Bound the vision-revise re-inflation: if the post-revise page count exceeds the ceiling, one measured trim (no image refresh) → re-assemble → ship. No further rounds.

**B. Judge model routing** *(cost + latency, minimal risk — these are classification tasks)*
`VERIFY_MODEL=sonnet-4-6` (gates the book — keep mid-tier) · `PROSE_JUDGE_MODEL`, `IMAGE_SCORE_MODEL`, `VISION_QA_MODEL` → `haiku-4-5`. Thresholds unchanged; spot-check the first book's scores against Opus on a sample. ~$3–6 → ~$0.5–1 per book; QA gate ≈2.5min → ≈1min.

**C. QA fix-loop on resume.** Extract gate+revise+sanitize into one helper used by fresh *and* resume paths — today resumed books gate once and ship sub-floor prose silently (all three books today shipped `passed=False`). Quality issues get **fixed**, not annotated.

**D. Fit-JS probe reduction + render instrumentation.** Binary-search iterations 22 → ~10 (0.1pt precision needs 9 for the widest range); add `build/fit/pdf` phase timing logs so the next optimization round is data-driven.

**E. Image quality.** Route `needs_in_image_text=True` briefs to gpt-image-1 (renders text correctly; garbled text is the dominant reject — 32/41/11 per book yesterday); keep Imagen for photographic no-text work. Optional: raise the 1.4× upscale cap to 1.8 with mild sharpening (43 soft "upscale-capped" heroes observed), behind config.
 
**F. BM25 retrieval** (~20 lines, no deps). Add IDF + length normalization to `grounding.retrieve` so common words stop dominating passage ranking — better claim attribution and verification recall.

**Explicitly rejected for now:** embeddings/pgvector (infra), moving content drafting off Opus (quality risk), parallel Chromium (memory risk; A removes the pressure), Anthropic Batch API (async, wrong for interactive latency), cross-book judge caches (staleness risk).

---

## 4. Verification

1. **Unit:** full pytest — 169 existing + the 9 new suites; every Phase-1 fix ships with its regression test in the same commit.
2. **Calibration check:** the corrected estimator must predict the three books shipped today (326–348 rendered pages) within ±10%.
3. **Live smoke (~$2–4):** one small fresh book (target ≈20 pages, 2 sections) — first-ever exercise of the fresh path end-to-end: parallel section pool visible in logs, `cache_read_input_tokens > 0`, ≤2 assemble passes / ≤6 renders, `done` with zero warnings.
4. **Loop-count regression:** per-book render count from logs ≤6 (vs 17–44); `page_budget_check` drift <0.15 (vs 0.44–0.66).
5. **Chaos drills** (off-hours, scripted): kill -9 mid-content → resume on cached bible + partial sections; `/regenerate` during a pending resume → 409/serialized; $1 budget cap → partial ship **with safety sanitize intact**.
6. **Deploy** = service restart (working tree is the deployment); watch the first real book end-to-end.

## 5. Operational notes

- The org's Anthropic API monthly spend limit was hit today — the live smoke test and any real generation need limit headroom; the Phase-1/2 code work and the calibration script cost nothing.
- Today's overhaul is **not yet committed to git** — commit before starting this work (the working tree is the live deployment).
- Out of scope, flagged: API endpoints (including DELETE) are unauthenticated on the public origin — recommend a separate auth pass.
