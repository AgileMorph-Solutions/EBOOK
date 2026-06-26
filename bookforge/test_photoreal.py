"""Cheap validation: generate 3 photoreal hero images with the new art-direction
recipe (camera/lens/light + hard no-text) + de-AI finish, so we can eyeball
whether they read as real photos (vs the old garbled-text illustrations).
~3 Imagen calls only."""
from __future__ import annotations

from bookforge.clients import images as img_client
from bookforge.clients import storage
from bookforge.pipeline import postprocess
from bookforge.schemas import ArtStyle, BookBible, ImageBrief, VoiceStyle

bible = BookBible(
    title="How America Ate", subtitle="x", audience="x", tone="x",
    art_style=ArtStyle(medium="editorial food photography",
        palette="warm cream, brick red, pine green", lighting="soft window",
        era="mid-century", composition_rules="thirds",
        negative_prompt="", style_suffix="vintage gouache illustration"),
    voice=VoiceStyle(narrator_persona="x", tone_register="x", pov="x",
        pacing="x", vocabulary="x"),
    chapters=[],
)

subjects = [
    ("a 1950s frozen TV dinner on a segmented aluminium tray: sliced turkey, peas, "
     "whipped sweet potato, on a kitchen table", "photo"),
    ("a bowl of corn flakes with milk on a simple breakfast table by a window", "photo"),
    ("a vintage American supermarket aisle with stacked grocery cans and boxes", "photo"),
]

import bookforge.pipeline.images as P
for i, (subj, kind) in enumerate(subjects):
    brief = ImageBrief(prompt=subj, kind=kind, aspect="full_bleed")
    prompt = P._build_prompt(brief, bible)
    print(f"\n[{i}] PROMPT: {prompt[:160]}...")
    try:
        raw = img_client.generate(prompt, aspect="full_bleed")
        finished = postprocess.finish(raw)
        storage.put(f"books/_test/photoreal_{i}.jpg", finished, "image/jpeg")
        print(f"[{i}] OK -> books/_test/photoreal_{i}.jpg ({len(finished)} bytes)")
    except Exception as e:
        print(f"[{i}] FAILED: {type(e).__name__}: {str(e)[:200]}")
