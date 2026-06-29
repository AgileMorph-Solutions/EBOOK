"""Quality-proof sample for Book 1 (Hydrogen Peroxide), Phase B.

Hand-seeds safe, accurate content that exercises every guide template, renders a
PDF + per-page PNGs (via Playwright), so we can verify senior-readable BIG text
and full page coverage before any paid full generation.

Run:  .venv/bin/python -m scripts.make_sample
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from bookforge.clients import storage
from bookforge.layout import guide, render
from bookforge.schemas import (
    ArtStyle, BlockType, BookBible, Chapter, ChecklistItem, ChecklistModel,
    ContentBlock, GeneratedImage, SectionPlan, StepItem, TableModel, UseEntry,
    VoiceStyle,
)

OUT = Path("artifacts/sample")
_PALETTE = [(21, 97, 109), (46, 125, 91), (32, 56, 70), (181, 101, 29), (16, 48, 57)]


def _placeholder(key: str, label: str, idx: int, w: int = 1200, h: int = 900) -> GeneratedImage:
    """A soft gradient placeholder so image regions are visible during layout QA."""
    base = _PALETTE[idx % len(_PALETTE)]
    img = Image.new("RGB", (w, h), base)
    px = img.load()
    for y in range(h):  # vertical gradient
        f = 0.55 + 0.45 * (y / h)
        row = (int(base[0] * f), int(base[1] * f), int(base[2] * f))
        for x in range(w):
            px[x, y] = row
    d = ImageDraw.Draw(img)
    d.text((40, h - 60), f"[ photo: {label} ]", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    storage.put(key, buf.getvalue(), "image/jpeg")
    return GeneratedImage(block_ref=key, storage_key=key, width=w, height=h, score=0.9)


def _bible() -> BookBible:
    return BookBible(
        title="The Hydrogen Peroxide Savings Bible",
        subtitle="250 Cheap Ways Seniors Can Clean, Fix, Freshen, and Protect Their Home for Pennies",
        audience="Seniors (65+), large-print friendly",
        tone="warm, plain, reassuring",
        book_kind="practical_guide",
        art_style=ArtStyle(
            medium="bright clean instructional photography", palette="bright clean teal and white",
            lighting="soft daylight", era="contemporary",
            composition_rules="real household items on plain backgrounds",
            negative_prompt="no text, no clutter", style_suffix="clean bright home photography"),
        voice=VoiceStyle(narrator_persona="a patient neighbor", tone_register="warm, plain",
                         pov="second person", pacing="short sentences", vocabulary="everyday words"),
        sections=[
            SectionPlan(index=1, title="Kitchen Cleaning", summary="The kitchen is where peroxide "
                        "earns its keep — cleaning boards, sinks, and more for pennies."),
            SectionPlan(index=2, title="Bathroom Cleaning", summary="Brighten grout, freshen the "
                        "bowl, and cut soap scum with one cheap brown bottle."),
        ],
    )


def _kitchen() -> Chapter:
    return Chapter(index=1, title="Kitchen Cleaning", blocks=[
        ContentBlock(type=BlockType.section_intro, text=(
            "The kitchen is where a single brown bottle of 3% hydrogen peroxide saves you the most. "
            "It cleans, brightens, and freshens the spots you use every day — for just pennies a time.")),
        ContentBlock(type=BlockType.use_card, heading="Disinfect a wooden cutting board", entries=[UseEntry(
            number=1, title="Disinfect a wooden cutting board",
            intro="Raw food leaves germs in the grooves of a board. Plain 3% peroxide cleans it without harsh smells.",
            what_you_need=["3% hydrogen peroxide", "A clean cloth or paper towel", "Warm water"],
            steps=[StepItem(text="Wash the board with warm soapy water and rinse it."),
                   StepItem(text="Pour a little 3% peroxide straight onto the surface."),
                   StepItem(text="Spread it with a cloth and let it sit for five minutes."),
                   StepItem(text="Rinse with warm water and stand the board up to dry.")],
            caution="Use 3% only. Do not mix it with vinegar in a closed bottle.",
            savings="Replaces antibacterial sprays (about $5 each).")]),
        ContentBlock(type=BlockType.use_card, heading="Make the kitchen sink shine", entries=[UseEntry(
            number=2, title="Make the kitchen sink shine",
            intro="A daily-use sink holds more germs than most people think. Peroxide brightens it and leaves it fresh.",
            what_you_need=["3% hydrogen peroxide", "A spray bottle", "A soft sponge"],
            steps=[StepItem(text="Rinse away any food bits and crumbs."),
                   StepItem(text="Spray 3% peroxide over the whole sink basin."),
                   StepItem(text="Wait ten minutes so it can do the work."),
                   StepItem(text="Wipe with a soft sponge and rinse well.")],
            savings="Replaces sink cleaners (about $4 each).")]),
        ContentBlock(type=BlockType.use_list, heading="More Quick Kitchen Uses", text=(
            "Short jobs you can do in a minute or two with the same brown bottle."), entries=[
            UseEntry(number=3, title="Wipe the fridge shelves",
                     intro="Spray, wait a moment, and wipe to lift spills and odors."),
            UseEntry(number=4, title="Freshen a smelly sponge",
                     intro="Soak the sponge in peroxide for ten minutes, then rinse."),
            UseEntry(number=5, title="Clean the microwave",
                     intro="Spray inside, wait five minutes, and wipe the grime away."),
            UseEntry(number=6, title="Brighten food containers",
                     intro="Spray stained tubs, wait, and the marks rinse off."),
            UseEntry(number=7, title="Shine the stovetop",
                     intro="Spray cooled burners, wait, and wipe off the grease."),
            UseEntry(number=8, title="Clean the trash can",
                     intro="Spray the inside, wait, and rinse to kill odors."),
            UseEntry(number=9, title="Disinfect dish rags",
                     intro="Soak rags for fifteen minutes before washing."),
            UseEntry(number=10, title="Clean the can opener",
                     intro="Spray the blade and wheel, then wipe and dry."),
            UseEntry(number=11, title="Refresh the dishwasher",
                     intro="Wipe the seal and run an empty hot cycle."),
            UseEntry(number=12, title="Whiten the countertop",
                     intro="Spray sealed stone, wait, and rinse the brighter surface."),
            UseEntry(number=13, title="Clean reusable bags",
                     intro="Spray the insides, wait, and wipe out the crumbs."),
            UseEntry(number=14, title="Scrub the sink drain",
                     intro="Pour around the drain to cut odor at the source.")]),
        ContentBlock(type=BlockType.chart, heading="Replace These Kitchen Products", text=(
            "One cheap bottle does the work of several store sprays."), table=TableModel(
            columns=["Store product", "Cheap peroxide method", "~Yearly saving"],
            rows=[["Antibacterial spray ($5)", "3% peroxide in a spray bottle", "$30"],
                  ["Sink cleaner ($4)", "Spray, wait 10 min, wipe", "$24"],
                  ["Cutting-board wipes ($6)", "Pour, wait, rinse", "$22"],
                  ["Stainless polish ($7)", "Peroxide + soft cloth", "$18"],
                  ["Microwave wipes ($5)", "Spray, wait, wipe", "$20"],
                  ["Drain deodorizer ($6)", "Pour peroxide around drain", "$26"]],
            note="Savings are rough estimates based on monthly use.")),
    ])


def _bathroom() -> Chapter:
    return Chapter(index=2, title="Bathroom Cleaning", blocks=[
        ContentBlock(type=BlockType.section_intro, text=(
            "Bathrooms collect grime, mildew, and stains. Hydrogen peroxide brightens grout, "
            "freshens the bowl, and cuts soap scum — all from one cheap brown bottle.")),
        ContentBlock(type=BlockType.use_card, heading="Whiten and disinfect grout", entries=[UseEntry(
            number=31, title="Whiten and disinfect grout",
            intro="Grout darkens with mildew over time. Peroxide lifts the stain and leaves it bright.",
            what_you_need=["3% hydrogen peroxide", "An old toothbrush", "A spray bottle"],
            steps=[StepItem(text="Spray 3% peroxide along the grout lines."),
                   StepItem(text="Let it sit for ten minutes to break down the stain."),
                   StepItem(text="Scrub gently with the old toothbrush."),
                   StepItem(text="Rinse with warm water and let it dry.")],
            caution="Test one tile first. Never mix peroxide with bleach.",
            savings="Replaces grout pens and sprays (about $6 each).")]),
        ContentBlock(type=BlockType.use_card, heading="Freshen the toilet bowl", entries=[UseEntry(
            number=32, title="Freshen the toilet bowl",
            intro="Skip the expensive blue liquids. Peroxide cleans and brightens the bowl for pennies.",
            what_you_need=["3% hydrogen peroxide (about half a cup)", "A toilet brush"],
            steps=[StepItem(text="Pour about half a cup of 3% peroxide into the bowl."),
                   StepItem(text="Let it sit for twenty minutes."),
                   StepItem(text="Scrub under the rim with the brush."),
                   StepItem(text="Flush to rinse it all away.")],
            savings="Replaces bowl cleaners (about $4 each).")]),
        ContentBlock(type=BlockType.checklist, heading="Weekly Bathroom Reset", text=(
            "A short routine keeps the bathroom bright with almost no effort."),
            checklist=ChecklistModel(items=[
                ChecklistItem(text="Spray grout lines and wipe", cadence="weekly"),
                ChecklistItem(text="Wipe the sink and taps with peroxide", cadence="weekly"),
                ChecklistItem(text="Freshen the toilet bowl", cadence="weekly"),
                ChecklistItem(text="Wipe light switches and handles", cadence="weekly"),
                ChecklistItem(text="Mist the shower walls after use", cadence="weekly"),
                ChecklistItem(text="Wipe the mirror and frame", cadence="weekly"),
                ChecklistItem(text="Deep-clean the shower door", cadence="monthly"),
                ChecklistItem(text="Refresh the bath mat", cadence="monthly"),
                ChecklistItem(text="Disinfect the toilet brush holder", cadence="monthly"),
                ChecklistItem(text="Clean the exhaust fan cover", cadence="monthly")])),
    ])


def main() -> None:
    chapters = [_kitchen(), _bathroom()]
    images = [
        _placeholder("books/sample/cover.jpg", "cover", 4, 1400, 1800),
        _placeholder("books/sample/1.1.jpg", "cutting board", 0),
        _placeholder("books/sample/1.2.jpg", "kitchen sink", 1),
        _placeholder("books/sample/2.1.jpg", "grout", 2),
        _placeholder("books/sample/2.2.jpg", "toilet bowl", 3),
    ]
    # entry-card heroes are looked up by block_ref "chapter.block"
    for gi, ref in zip(images[1:], ["1.1", "1.2", "2.1", "2.2"]):
        gi.block_ref = ref

    html = guide.build_practical_html(
        _bible(), chapters, images, cover_key="books/sample/cover.jpg",
        pro={"heading": "When to Call a Professional",
             "lead": "Most jobs in this book are easy and safe. A few situations are better "
                     "left to an expert. Call a professional if any of these apply.",
             "signs": ["A spill involves an unknown chemical you cannot identify.",
                       "You see mold spreading across a large area of a wall or ceiling.",
                       "A surface is damaged and needs repair, not just cleaning.",
                       "You feel dizzy or short of breath while cleaning — stop and get air."],
             "reassure": "There is no shame in calling for help. Spending a little on the right "
                         "expert can save you money and worry in the long run."})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hp_sample.html").write_text(html)
    pdf = render.html_to_pdf(html)
    (OUT / "hp_sample.pdf").write_bytes(pdf)
    print(f"PDF: {OUT/'hp_sample.pdf'}  ({len(pdf)/1_048_576:.2f} MB)")
    pngs = render.html_to_pngs(html, OUT / "png", scale=2.0)
    print(f"PNGs: {len(pngs)} pages -> {OUT/'png'}")


if __name__ == "__main__":
    main()
