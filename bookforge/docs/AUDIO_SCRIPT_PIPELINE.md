# Audio Script Pipeline

Turns a **title** into a retention-focused narration **transcript** and an
ElevenLabs **MP3**. Audio only — no video generation.

Content follows a conversational narration flow (generalized for any title):
brainstorm → refine for retention → write each item at a word band (default
265–270) in spoken voice with varied openings → TTS. Item headings use
sentence case (first word capital only).

**Flow walkthrough:** [AUDIO_SCRIPT_FLOW.md](./AUDIO_SCRIPT_FLOW.md)

## Docker

| Host URL | Service |
|----------|---------|
| `http://localhost:8011` | API (`8011→8000`) |
| `http://localhost:3010` | UI |

```bash
cd bookforge/infra
docker compose up -d --build api
```

`.env`:

```
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=bP8FJDHmWVEgXJDitdQd
API_PUBLIC_BASE_URL=http://localhost:8011
```

## API

```
GET  /audio-scripts
POST /audio-scripts
GET  /audio-scripts/{script_id}
GET  /audio-scripts/{script_id}/audio
```

### POST

```json
{
  "title": "100 Concepts That Make You See the World Differently Than 99% of People",
  "category": "psychology",
  "item_limit": 3,
  "word_count": 270,
  "overall_word_limit": null,
  "voice_id": null,
  "model_id": "eleven_multilingual_v2",
  "voice_settings": {
    "stability": 0.40,
    "similarity_boost": 0.78,
    "style": 0.20,
    "use_speaker_boost": true,
    "speed": 0.95
  },
  "skip_audio": false
}
```

- `category` (optional string): free-text niche for prompt voice. Known values (`psychology`, `relationship`, `philosophy`, `general`) get richer domain phrasing; any other text is used as-is.
- `word_count` (optional, 100–500): target words per item. Omit → SOP **265–270**, or derived from `overall_word_limit` / title duration when set. When set, rewrite band is **target ± 5**.
- `overall_word_limit` (optional, 100–150000): total word budget. When omitted, titles like `3 Hours of …` derive `minutes × 130` wpm. Without `word_count`, per-item target is `overall ÷ item_count` (clamped 100–500). When both are set and product exceeds overall, overall wins.
- Duration phrases in the title (`N hours` / `N minutes`, including spelled-out like `Three Hours`) size the script for that listening time; they are not treated as item counts.
- Defaults (when `model_id` / `voice_settings` omitted): `eleven_multilingual_v2` and the voice settings above. Pass only the fields you want to override.

### GET when done

Includes `transcript`, `items[]` (each with `word_count`), `total_words`, `audio_link`, `stages`.

## Stages

| Stage | What |
|-------|------|
| `queued` | Accepted |
| `brainstorming` | List N concepts |
| `refining_list` | Retention refine |
| `writing_items` | Write bodies in the word band |
| `generating_audio` | ElevenLabs TTS + ffmpeg concat |
| `complete` | Done |

## Artifacts

```
/home/forge/artifacts/audio-scripts/{script_id}/
  request.json
  status.json
  result.json
  narration.mp3
```

## Curl

```bash
curl -X POST http://localhost:8011/audio-scripts \
  -H 'content-type: application/json' \
  -d '{"title":"100 Concepts That Make You See the World Differently Than 99% of People","item_limit":3,"word_count":270}'

curl -s http://localhost:8011/audio-scripts/SCRIPT_ID
curl -o narration.mp3 http://localhost:8011/audio-scripts/SCRIPT_ID/audio
```

## Code map

| Piece | Path |
|-------|------|
| Stages | `pipeline/audio_script_stages.py` |
| Content | `pipeline/audio_script.py` |
| Prompts | `pipeline/audio_script_prompts.py` |
| TTS | `pipeline/audio_tts.py` + `clients/elevenlabs.py` |
| Routes | `api/main.py` (`/audio-scripts*`) |
| Flow doc | `docs/AUDIO_SCRIPT_FLOW.md` |
