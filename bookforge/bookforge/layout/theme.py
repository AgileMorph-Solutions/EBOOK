"""Design kit — curated theme presets + a topic-based picker.

Each preset is a set of design tokens (palette, fonts) emitted as CSS custom
properties. Presets are professionally chosen so the book never looks random.
The picker maps a topic/mood to the best-fitting preset (the "you pick best per
topic" mode); an explicit name overrides it.
"""
from __future__ import annotations

# Google Fonts import shared by presets (display + serif body + condensed).
FONT_IMPORT = ("https://fonts.googleapis.com/css2?"
               "family=Playfair+Display:ital,wght@0,500;0,700;0,800;0,900;1,500"
               "&family=EB+Garamond:ital@0;1"
               "&family=Oswald:wght@400;500;600;700&display=swap")

PRESETS: dict[str, dict[str, str]] = {
    # Mid-century American food/history — warm cream, tomato red, pine green.
    "americana": {
        "paper": "#F2E8D0", "panel": "#E7D8B6", "ink": "#241F18",
        "accent": "#B5392A", "deep": "#274E45", "gold": "#C68A3C",
        "muted": "#7C6A4C", "onpaper_soft": "#5C5240",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
    # Warm vintage cookbook — closest to the reference.
    "vintage_cookbook": {
        "paper": "#ECDCB5", "panel": "#E3CF9E", "ink": "#2B2218",
        "accent": "#8B2B17", "deep": "#5A3A1E", "gold": "#B5832E",
        "muted": "#7A6A4E", "onpaper_soft": "#5A4E38",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
    # Clean modern editorial — pale paper, bold ink, single hot accent.
    "modern_editorial": {
        "paper": "#F6F4EF", "panel": "#ECE8DF", "ink": "#1A1A1A",
        "accent": "#D2402F", "deep": "#222222", "gold": "#9A8C6A",
        "muted": "#8A8578", "onpaper_soft": "#4A4A48",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
}

_KEYWORDS = {
    "americana": ["america", "american", "u.s", "usa", "states", "diner", "mid-century"],
    "vintage_cookbook": ["british", "grandparent", "forgotten", "vintage", "old",
                         "traditional", "heritage", "recipe", "meal"],
    "modern_editorial": ["modern", "future", "tech", "startup", "minimal", "design"],
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
            f"--bleed:{bleed}in;--safe:{bleed + 0.62}in;{toks}}}")
