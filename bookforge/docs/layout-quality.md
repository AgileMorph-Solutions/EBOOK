# Layout quality system — design rationale (research-backed)

How BookForge guarantees no clipped/overflowing print output while always
shipping a book. Cross-checked (2026-07) against industry practice: TeX/
Knuth-Plass optimization, Paged.js fragmentation hooks, Antenna House / XSL-FO
copy-fitting loops, InDesign Server VDP copyfit (Meadows/Typefi), newspaper
copyfitting, CMS schema budgets, and Amazon KDP preflight.

## The ladder (cheapest first — the industry-standard ordering)

1. **Content born layout-safe** (generation-time contracts): schema/prompt
   budgets on every block (bullet words, step counts/lengths, need-item
   counts) + deterministic trims (`layout_budget.py`). Industry: CMS
   schema-level character/count budgets; structured-output validation as a
   hard gate. Caveat the research confirms: character counts are an
   *unreliable* proxy for rendered size (proportional fonts) — so budgets are
   only the first line; the real gate measures rendered pixels.
2. **Measured reflow** (free, no visual concession): entry → spread → flowed
   over N pages (`data-entry` keys, `<ol start>` continuity), essays
   re-paginate, lists re-chunk, how-to compacts then splits. Industry: "keep
   font size constant and flow onto additional pages beats shrinking text"
   (VDP whitespace management); Paged.js measure-then-overflow chunking.
3. **Typographic concession** (bounded): fit-floor steps 12.5 → 11.5 → 11pt
   (KDP's hard floor is 7pt — we stay far above). Industry copyfit ladders
   also use tracking/leading/horizontal-scale squeezes; we deliberately use
   only font-size steps to keep the large-print promise (add tracking/leading
   rungs only if fitmin steps prove insufficient in practice).
4. **Targeted regeneration** (LLM, `MAX_LAYOUT_REVISIONS`): audit defects map
   to per-chapter rewrite instructions with hard word budgets
   (`orchestrator._assemble_with_layout_revise`). Industry: validate-then-
   retry loops feeding validation errors back to the generator.
5. **Fail loudly** (`LayoutError`, permanent): only when 1-4 are exhausted.
   Industry: preflight gates block bad files from production; "not all
   detectable problems are automatically correctable."

## Why measure-then-repair (not predict-only)

- Greedy line breaking in browsers + flaky upstream Chromium print bugs mean
  a layout can only be *proven* correct by measuring the rendered result —
  hence the in-page audit on every render (`_LAYOUT_FIT_JS`), identical on
  the Playwright path and `render/server.js` (JSON protocol).
- Binary-search fit with explicit termination (no errors / max iterations /
  no progress) is the standard automated copy-fitting architecture (Antenna
  House overflow auto-shrink; XSLT render-measure-adjust loops). Ours:
  `shrinkToFit` + the leveled `repairs` escalation in
  `assemble._render_practical` (progress-or-abort, max level 4, max 6 rounds).
- Detection is asymmetric by industry norm: overset (clipping) is gated hard;
  underfill is a soft warning (page-budget drift check).

## Print-safety invariants (KDP-aligned)

- Never below 11pt body type (KDP minimum: 7pt; large-print promise: 12.5pt
  nominal floor, concessions only under repair).
- No text outside the trim-safe area: full-page audit + `pdf_geometry` gate.
- One `<section class="page">` per PDF sheet — no cross-page line splitting,
  which sidesteps Chromium's known mid-line clip bug at page boundaries.
- Fonts embedded via the render stack; page size pinned viewport==sheet at
  96 css-px/in so print geometry is deterministic.

## Ops

- `POST /books/{id}/rerender`: re-render any failed/done book from its
  `render_manifest.json` (zero LLM/image cost) and mark it done — the
  recovery path after layout-code fixes.
- Tests: `tests/test_layout_gates.py`.
