# Audio Script Flow

How a title becomes a transcript + MP3. Companion to
[AUDIO_SCRIPT_PIPELINE.md](./AUDIO_SCRIPT_PIPELINE.md) (API/ops reference).

## End-to-end flow

```mermaid
flowchart TD
  post[POST_/audio-scripts] --> resolve[resolve_item_count_and_word_band]
  resolve --> brainstorm[brainstorming]
  brainstorm --> refine[refining_list]
  refine --> write[writing_items]
  write --> audio{skip_audio?}
  audio -->|no| tts[generating_audio_ElevenLabs]
  audio -->|yes| doneTx[complete_transcript_only]
  tts --> doneMp3[complete_transcript_plus_mp3]
  doneTx --> get[GET_/audio-scripts/id]
  doneMp3 --> get
  doneMp3 --> getAudio[GET_/audio-scripts/id/audio]
```

## Request knobs (plain language)

| Field | What it does |
|-------|----------------|
| `title` | Theme for the narration. A **leading number** (e.g. `100 Signs…`, `100 Concepts…`, `30 Tricks…`, `100 ways…`) sets **that many sections**. If it says `3 Hours of…` / `45 Minutes of…`, the script is sized for that **listening time** (~130 words/minute) instead of treating the number as an item count. **Generic titles** (no count, no duration) default to **~3 sections** at SOP density. |
| `category` | Free-text niche for prompt voice (e.g. `psychology`, `true crime`). Known niches get richer domain phrasing; anything else is used as-is. |
| `item_limit` | Exact item/section count when set (API-only; not shown on the create form). Omit → count from the title’s promised number, or a duration-derived count when the title is time-based. |
| `word_count` | Target words **per section** (UI: **Words per section**). Omit → SOP **265–270**. Allowed range: **100–500**. |
| `paragraph_count` | Total paragraphs across the **full transcript**, distributed across sections. Must be ≥ section count. Omit → natural paragraph breaks. |
| `skip_audio` | Transcript only (no ElevenLabs / no MP3). |
| `voice_id` / `model_id` / `voice_settings` | Optional TTS overrides (defaults documented in the pipeline doc). |

### `item_limit` example

Title: `100 Ways to Do Something` + `item_limit: 2` → **2 items** written and narrated.
Title: `3 Hours of …` + `item_limit: 10` → **10 items** (explicit limit overrides the “3” in the title).
The stored title text is unchanged.

### `word_count` example

- Omitted → each item prompted and rewrite-checked for **265–270** words.
- `"word_count": 200` → each item targeted at **200** words (accept band **195–205**).
- `"word_count": 270` → each item targeted at **270** words (accept band **265–275**).

### Generic title example

Title: `How to think clearly` (no count, no duration):

- Section count → **3** (from internal ~1000-word budget at ~300 words/section)
- Per section → **265–270** words (SOP band)
- Paragraph breaks are flexible (word band only, no fixed paragraph count)

### Duration title example

Title: `3 Hours of Emotional Control Secrets…` (no `item_limit` / `word_count`):

- Parses **180 minutes** (not “3 items”).
- Item count ≈ `23400 / 267` ≈ **88** items at SOP density.
- Per-item target → **265–270** words (SOP band) → ~3 hours of narration at 130 wpm.

Title: `100 Secrets for 3 Hours of Deep Focus` → keeps **100** items at the SOP band.

### Count-from-title example

Title: `100 Signs Someone Secretly Dislikes You` (no `item_limit` / `word_count`):

- Parses **100** from the leading count (works for signs, behaviors, concepts, tricks, ways, etc.).
- Section count → **100**
- Per section → **265–270** words (SOP band)
- Prompts include a **TITLE COUNT CONTRACT** so brainstorm/refine/write deliver the full list

## Stages

| Stage | What happens |
|-------|----------------|
| `queued` | Job accepted |
| `brainstorming` | LLM lists N concept titles (sentence case) |
| `refining_list` | List sharpened for retention |
| `writing_items` | Each item written conversationally to the word band (rewrite if out of band) |
| `generating_audio` | Per-item ElevenLabs TTS → ffmpeg concat → one MP3 |
| `complete` | Done |

Progress `done` / `total` uses the resolved item count as `total`.

## Artifacts

```
artifacts/audio-scripts/{script_id}/
  request.json    # original POST body (includes word_count when set)
  status.json     # stage + progress
  result.json     # transcript, items[].word_count, total_words
  narration.mp3   # present unless skip_audio
```

## Example curl

```bash
curl -X POST http://localhost:8011/audio-scripts \
  -H 'content-type: application/json' \
  -d '{
    "title": "100 Concepts That Make You See the World Differently Than 99% of People",
    "category": "psychology",
    "item_limit": 3,
    "word_count": 270,
    "skip_audio": false
  }'

curl -s http://localhost:8011/audio-scripts/SCRIPT_ID
curl -o narration.mp3 http://localhost:8011/audio-scripts/SCRIPT_ID/audio
```

When status is `done`, the GET body includes `transcript`, `items` (each with `word_count`), `total_words`, and `audio_link` (unless `skip_audio`).
