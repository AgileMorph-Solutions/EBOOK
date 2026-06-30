"""Book 1 — The Hydrogen Peroxide Savings Bible."""
from __future__ import annotations

from bookforge.books import GuideBook, Special
from bookforge.schemas import ArtStyle, BookBible, HowToPage, SectionPlan, SpecialPlan, VoiceStyle

_SECTIONS = [
    ("Kitchen Cleaning", "The kitchen is where one cheap brown bottle saves the most — "
     "cleaning boards, sinks, counters, and appliances for pennies."),
    ("Bathroom Cleaning", "Brighten grout, freshen the bowl, and cut soap scum and "
     "mildew with the same simple 3% peroxide."),
    ("Mattress & Pillow Freshening", "Lift stains, kill odors, and freshen the bed where "
     "we spend a third of our lives."),
    ("Laundry Tricks", "Brighten whites, lift stains, and freshen loads without costly "
     "spray-and-wash bottles."),
    ("Drains & Sinks", "Clear odors and keep drains fresh without harsh, pricey "
     "chemical pours."),
    ("Odor Removal", "Neutralize smells around the home — from bins to shoes to the "
     "fridge — for next to nothing."),
    ("Stain Removal", "Tackle common stains on fabric, carpet, and hard surfaces with "
     "gentle, low-cost peroxide."),
    ("Patio, Garage & Outdoor", "Brighten outdoor surfaces, tools, and bins, and keep "
     "the outside of the home fresh."),
]


def get() -> GuideBook:
    expanded: list[tuple[str, str]] = []
    for title, summary in _SECTIONS:
        expanded.append((f"{title} — Essentials", summary))
        expanded.append((f"{title} — More Uses", f"More practical {title.lower()} tips with the same cheap brown bottle."))
        expanded.append((f"{title} — Extra Tips", f"Additional senior-friendly {title.lower()} jobs that save money."))
    sections = [
        SectionPlan(index=i + 1, title=t, summary=s,
                    full_card_quota=8, list_quota=28, image_quota=3,
                    page_budget=28)
        for i, (t, s) in enumerate(expanded)
    ]
    how_to = HowToPage(
        heading="How to Use This Book",
        lead=("This book is built to be easy on the eyes and easy to follow. "
              "Everything is in large print, one idea at a time."),
        points=[
            "Every tip is laid out the same way: what you need, the steps, and what you save.",
            "Read the Safety First page before you try anything with a chemical.",
            "Start with the section that matches the room or job you have today.",
            "You do not need special tools — most tips use what is already in your home.",
        ],
    )
    bible = BookBible(
        title="The Hydrogen Peroxide Savings Bible",
        subtitle="250 Cheap Ways Seniors Can Clean, Fix, Freshen, and Protect Their Home for Pennies",
        audience="Seniors (65+), large-print friendly",
        tone="warm, plain, reassuring",
        book_kind="practical_guide",
        target_pages=250,
        sections=sections,
        theme_name="home_clean",
        safety_key="hp",
        cover_subject=("a tidy bright home cleaning scene with a brown bottle of hydrogen "
                       "peroxide, a spray bottle, a cloth, and a lemon on a clean counter"),
        how_to=how_to,
        list_lead="Quick jobs you can do in a minute or two with the same brown bottle.",
        pro_topic="home cleaning with hydrogen peroxide",
        specials=[
            SpecialPlan(after_section=1, kind="chart", heading="Replace These Kitchen Products",
                        columns=["Store product", "Cheap peroxide method", "~Yearly saving"], rows=6),
            SpecialPlan(after_section=4, kind="checklist", heading="Weekly Cleaning Routine",
                        about="a simple weekly and monthly cleaning routine using 3% hydrogen peroxide",
                        items=10),
            SpecialPlan(after_section=24, kind="chart", heading="Your Yearly Savings at a Glance",
                        columns=["Product you stop buying", "Cheap method", "~Yearly saving"], rows=7),
        ],
        art_style=ArtStyle(
            medium="bright clean home photography",
            palette="bright clean teal, soft white, and warm wood",
            lighting="soft natural daylight",
            era="contemporary",
            composition_rules="real household items on clean uncluttered surfaces",
            negative_prompt="no text, no labels, no clutter, no faces, no hands near face",
            style_suffix="bright clean modern home photography, soft daylight, crisp focus, tidy composition",
        ),
        voice=VoiceStyle(
            narrator_persona="a patient, thrifty neighbor who has tried every trick",
            tone_register="warm, plain, encouraging",
            pov="second person",
            pacing="short sentences, one idea each",
            vocabulary="everyday words, no jargon",
            banned_patterns=["hack", "pro tip", "leverage", "utilize", "simply just",
                             "game-changer", "in conclusion", "nestled", "delve",
                             "rich tapestry", "it's worth noting", "boasts"],
        ),
    )
    return GuideBook(bible=bible)
