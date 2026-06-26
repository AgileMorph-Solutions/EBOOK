"""Design-only preview: render the themed templates using on-disk images +
representative ~200-word sections. NO LLM/Imagen calls. Run in the spike
container (needs RENDER_URL + mounted volume)."""
from __future__ import annotations

import glob
import os

from bookforge.clients import storage
from bookforge.layout import build, render
from bookforge.pipeline import postprocess
from bookforge.schemas import (ArtStyle, BlockType, BookBible, Chapter,
                               ChapterPlan, ContentBlock, GeneratedImage, VoiceStyle)

root = os.environ.get("BOOKFORGE_LOCAL_STORE", "./artifacts")
jpgs = sorted(glob.glob(f"{root}/books/*/ch*.jpg"))[:3]
# Apply the de-AI photographic finish (grade+grain+vignette) to each source image.
keys = []
for i, p in enumerate(jpgs):
    finished = postprocess.finish(open(p, "rb").read())
    k = f"books/_preview/graded_{i}.jpg"
    storage.put(k, finished, "image/jpeg")
    keys.append(k)
print("graded images:", keys)

bible = BookBible(
    title="How America Ate", subtitle="Forgotten Foods That Shaped a Nation",
    audience="curious cooks", tone="warm, literary",
    art_style=ArtStyle(medium="editorial food photography", palette="warm",
        lighting="soft", era="mid-century", composition_rules="thirds",
        negative_prompt="text", style_suffix="vintage film"),
    voice=VoiceStyle(narrator_persona="a food historian", tone_register="warm",
        pov="3rd", pacing="measured", vocabulary="sensory"),
    chapters=[ChapterPlan(index=1, title="A Table Set by Machines",
        summary="How frozen trays, flaked cereal, and the supermarket quietly "
                "rewrote what an American dinner looked like in the span of a "
                "single lifetime.", beats=["x"], page_budget=12)],
)

LONG = ("In 1953 the Swanson company introduced the frozen TV Dinner in a segmented "
        "aluminum tray, built around turkey with cornbread dressing, peas, and sweet "
        "potatoes. The story most often told is that the company was trying to use up "
        "an enormous surplus of Thanksgiving turkey, and whether or not that is exactly "
        "true, it catches the logic of the age. The meal could go straight from the "
        "freezer to the oven and, twenty-five minutes later, to a tray table in front "
        "of the television. It asked almost nothing of the cook. What it offered "
        "instead was time, and the quiet permission to eat dinner while watching a "
        "screen rather than facing one another across a table. Within a few years the "
        "format was everywhere, and the word dinner had quietly acquired a second "
        "meaning: not a thing you made, but a thing you bought, already decided.")

ch = Chapter(index=1, title="A Table Set by Machines", blocks=[
    ContentBlock(type=BlockType.intro, heading="Cooked, or Made?",
        text="A quiet truth sits at the center of the American kitchen. Between the "
             "1890s and the 1950s, the food on the country's table was shaped less by "
             "chefs than by inventors, companies, and the blunt arithmetic of war. The "
             "recipes came later; first came the machines.\n\n" + LONG),
    ContentBlock(type=BlockType.history, heading="The Frozen Tray", text=LONG),
    ContentBlock(type=BlockType.history, heading="Battle Creek's Flakes", text=LONG),
    ContentBlock(type=BlockType.pull_quote,
        text="The foods that mattered most were the ones nobody bothered to romanticize."),
    ContentBlock(type=BlockType.history, heading="The Store That Ate the Grocer", text=LONG),
])

# images on blocks 1,2,4 (intro stays text, quote at 3)
images = [GeneratedImage(block_ref=f"1.{i}", storage_key=keys[j], width=1024, height=1536)
          for j, i in enumerate([1, 2, 4])]

html = build.build_html(bible, [ch], images, cover_key=keys[0])
pdf = render.html_to_pdf(html)
storage.put("books/_preview/themed.pdf", pdf, "application/pdf")
print("PREVIEW PDF bytes:", len(pdf), "->", f"{root}/books/_preview/themed.pdf")
