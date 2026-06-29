"""Book 2 — The Senior Pest Control Savings Guide."""
from __future__ import annotations

from bookforge.books import GuideBook, Special
from bookforge.schemas import ArtStyle, BookBible, SectionPlan, VoiceStyle

# (title, summary, divider_subject). The divider_subject describes a CLEAN,
# pest-free, "problem-already-prevented" scene — never the pest itself — because
# the title alone ("Roach Prevention") biases the image model into drawing the
# pest. Each is on-palette (forest green / warm wood / soft white) and topical.
_SECTIONS = [
    ("Roach Prevention", "Keep roaches out with cheap household steps — before you ever "
     "need to call an exterminator.",
     "a spotless modern kitchen with sealed cabinets, clear clean countertops and a tidy "
     "sink, bright and immaculate, wide view"),
    ("Ant Control", "Stop ant trails and find the entry points using items you already "
     "have at home.",
     "a clean wiped kitchen countertop by a bright window with a spotless sink and sealed "
     "glass storage jars, tidy and immaculate"),
    ("Mosquitoes & Gnats", "Cut down summer biters and kitchen gnats around the home and "
     "yard, simply and cheaply.",
     "a tidy backyard patio with clean window screens, neat outdoor furniture and no "
     "standing water, fresh and bright in soft daylight"),
    ("Mouse Prevention", "Seal, tidy, and deter mice so they never settle in — the cheapest "
     "pest control there is.",
     "a neat organized pantry with food in sealed glass containers on clean shelves and "
     "swept baseboards, tidy and bright"),
    ("Wasp Prevention", "Discourage wasps from nesting near doors, eaves, and the patio, "
     "safely and early.",
     "a clean, well-kept home porch and doorway with tidy eaves and neat potted plants, "
     "bright and freshly painted"),
    ("Fleas & Bed Bugs", "Careful, honest guidance on fleas and bed bugs — what helps at "
     "home, and when to call a professional.",
     "a freshly made bed with crisp clean white linens in a bright, tidy, immaculate "
     "bedroom with light wood floor"),
    ("Peroxide & Household Helpers", "How 3% hydrogen peroxide and other cheap staples help "
     "with cleanup and prevention.",
     "neat cleaning supplies on a clean counter — a spray bottle, a brown bottle and "
     "folded cloths arranged tidily, bright and simple"),
    ("Cheap Combinations", "Safe ways to use baking soda, vinegar, borax, salt, and dish "
     "soap — and what never to mix.",
     "baking soda, white vinegar, a lemon and a spray bottle arranged on a clean bright "
     "countertop, tidy still life"),
    ("Outdoor Pest Prevention", "Keep the yard, bins, and entry points from inviting pests "
     "indoors in the first place.",
     "a tidy backyard with clean sealed trash bins, a neat entryway and a well-kept lawn, "
     "bright daylight, wide view"),
]


def get() -> GuideBook:
    sections = [
        SectionPlan(index=i + 1, title=t, summary=s, divider_subject=dv,
                    full_card_quota=3, list_quota=10, image_quota=2, page_budget=6)
        for i, (t, s, dv) in enumerate(_SECTIONS)
    ]
    bible = BookBible(
        title="The Senior Pest Control Savings Guide",
        subtitle="How to Fight Roaches, Ants, Mice, Mosquitoes, Wasps, Gnats, Fleas & Bed Bugs Without Paying Hundreds",
        audience="Seniors (65+), large-print friendly",
        tone="warm, calm, reassuring",
        book_kind="practical_guide",
        target_pages=60,
        sections=sections,
        art_style=ArtStyle(
            medium="clean protective home photography",
            palette="calm forest green, warm wood, and soft white",
            lighting="soft natural daylight",
            era="contemporary",
            composition_rules="tidy, sealed, well-kept home details on clean surfaces",
            negative_prompt="no insects, no bugs, no pests, no text, no clutter, no faces",
            style_suffix="clean bright protective home photography, soft daylight, tidy composition",
        ),
        voice=VoiceStyle(
            narrator_persona="a calm, practical neighbor who keeps a tidy, pest-free home",
            tone_register="warm, calm, plain",
            pov="second person",
            pacing="short sentences, one idea each",
            vocabulary="everyday words, no jargon",
            banned_patterns=["hack", "pro tip", "leverage", "utilize", "simply just",
                             "game-changer", "in conclusion", "nestled", "delve",
                             "rich tapestry", "it's worth noting", "boasts", "infestation hellscape"],
        ),
    )
    return GuideBook(
        bible=bible,
        safety_key="pest",
        cover_subject=("a clean, tidy, well-kept home entryway and kitchen with sealed "
                       "cabinets, a caulk gun, a spray bottle, and bay leaves on a clean counter"),
        how_to={
            "heading": "How to Use This Book",
            "lead": ("This book helps you stop pests with cheap, simple steps before you "
                     "pay for an exterminator. Everything is in large, clear print."),
            "points": [
                "Each tip lists what you need, the steps, and what it can save you.",
                "Read the Safety First page before using borax or any chemical.",
                "Start with the pest you are dealing with today.",
                "Some problems need a professional — the book tells you when.",
            ],
        },
        pro_topic="home pest control",
        specials=[
            Special(after_section=1, kind="checklist", heading="Monthly Pest-Proofing Checklist",
                    about="a simple monthly routine to keep the home sealed and unattractive to pests",
                    items=12),
            Special(after_section=9, kind="chart", heading="Exterminator Bill Avoidance Tracker",
                    columns=["Pest problem", "Cheap DIY first step", "~Cost avoided"], rows=7),
        ],
    )
