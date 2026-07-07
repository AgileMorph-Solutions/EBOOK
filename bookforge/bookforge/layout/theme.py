"""Design kit — curated theme presets + a topic-based picker.

Each preset is a set of design tokens (palette, fonts) emitted as CSS custom
properties. Presets are professionally chosen so the book never looks random.
The picker maps a topic/mood to the best-fitting preset (the "you pick best per
topic" mode); an explicit name overrides it.
"""
from __future__ import annotations

# Google Fonts import shared by presets.
#   display  -> Playfair Display (warm serif headings)
#   body     -> Atkinson Hyperlegible (designed by the Braille Institute for
#               low-vision readers; the strongest choice for a senior audience)
#   cond     -> Oswald (condensed labels, eyebrows, folios, step numbers)
#   EB Garamond kept available for the legacy cookbook presets.
FONT_IMPORT = ("https://fonts.googleapis.com/css2?"
               "family=Playfair+Display:ital,wght@0,500;0,700;0,800;0,900;1,500"
               "&family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400;1,700"
               "&family=EB+Garamond:ital@0;1"
               "&family=Oswald:wght@400;500;600;700&display=swap")

# Senior-readable font stacks (overridden per preset only if needed).
_SENIOR_FONTS = {
    "display": "'Playfair Display', Georgia, serif",
    "cond": "'Oswald', 'Arial Narrow', sans-serif",
    "body": "'Atkinson Hyperlegible', 'Helvetica Neue', Arial, sans-serif",
}

PRESETS: dict[str, dict[str, str]] = {
    # Mid-century American food/history — warm cream, tomato red, pine green.
    "americana": {
        "paper": "#F7F1E5", "panel": "#EDE5D4", "ink": "#221E19",
        "accent": "#B3402E", "deep": "#2C4A42", "gold": "#C68A3C",
        "muted": "#6E6250", "onpaper_soft": "#524A40",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
    # Warm vintage cookbook — closest to the reference.
    "vintage_cookbook": {
        "paper": "#F6EFE3", "panel": "#EDE3CF", "ink": "#26201A",
        "accent": "#8A3324", "deep": "#3E2C22", "gold": "#B08A3E",
        "muted": "#6E6250", "onpaper_soft": "#524640",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
    # Clean modern editorial — pale paper, bold ink, single hot accent.
    "modern_editorial": {
        "paper": "#F6F4EF", "panel": "#ECE8DF", "ink": "#161616",
        "accent": "#C93A2B", "deep": "#222222", "gold": "#9A8C6A",
        "muted": "#8A8578", "onpaper_soft": "#4A4A48",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
    # --- senior-first practical-guide presets (high contrast, big type) ----- #
    # Bright, clean, hygienic — for hydrogen-peroxide / cleaning books.
    "home_clean": {
        "paper": "#FAF8F4", "panel": "#E7F0EE", "panel_dark": "#143842",
        "ink": "#14222B", "accent": "#15616D", "deep": "#103039",
        "gold": "#C79A3E", "muted": "#4C5B62", "onpaper_soft": "#37454E",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        **_SENIOR_FONTS,
    },
    # Protective, serious — for pest-control books. Red reserved for danger only.
    "pest_guard": {
        "paper": "#FBFAF6", "panel": "#ECF0EA", "panel_dark": "#1E342B",
        "ink": "#14222B", "accent": "#2F5D50", "deep": "#1B3328",
        "gold": "#C79A3E", "muted": "#4C5B62", "onpaper_soft": "#37454E",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        **_SENIOR_FONTS,
    },
    # Warm burgundy/cream — food, cooking, recipe guides for seniors.
    "senior_cookbook": {
        "paper": "#FAF6F0", "panel": "#F0E8DC", "panel_dark": "#3D2020",
        "ink": "#2A1A18", "accent": "#7B2D3B", "deep": "#4A1F28",
        "gold": "#C79A3E", "muted": "#6B5A52", "onpaper_soft": "#524640",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        **_SENIOR_FONTS,
    },
}

_KEYWORDS = {
    "americana": ["america", "american", "u.s", "usa", "states", "diner", "mid-century"],
    "vintage_cookbook": ["british", "grandparent", "forgotten", "vintage", "old",
                         "traditional", "heritage", "recipe", "meal"],
    "modern_editorial": ["modern", "future", "tech", "startup", "minimal", "design"],
    "home_clean": ["hydrogen", "peroxide", "clean", "cleaning", "savings", "home",
                   "household", "freshen", "stain", "odor", "laundry",
                   "bathroom", "pennies", "thrifty", "disinfect", "bleach"],
    "pest_guard": ["pest", "pests", "roach", "roaches", "ant", "ants", "mouse",
                   "mice", "mosquito", "gnat", "wasp", "flea", "bed bug", "bedbug",
                   "exterminator", "infestation"],
    "senior_cookbook": ["rice", "cook", "cooking", "recipe", "recipes", "meal",
                        "meals", "dinner", "dinners", "supper", "kitchen", "food",
                        "baking", "british", "storecupboard", "budget meal"],
}


def pick(topic: str, *, name: str | None = None) -> dict[str, str]:
    if name and name in PRESETS:
        return PRESETS[name]
    t = (topic or "").lower()
    best, score = "vintage_cookbook", 0
    for preset, words in _KEYWORDS.items():
        s = sum(1 for w in words if w in t)
        if s > score:
            best, score = preset, s
    return PRESETS[best]


def css_root(tokens: dict[str, str], page_w: float, page_h: float,
             bleed: float) -> str:
    """Build the :root design-token block."""
    toks = ";".join(f"--{k}:{v}" for k, v in tokens.items())
    return (f"@import url('{FONT_IMPORT}');"
            f":root{{--page-w:{page_w}in;--page-h:{page_h}in;"
            f"--bleed:{bleed}in;--safe:{bleed + 0.62}in;--measure:34em;{toks}}}")
