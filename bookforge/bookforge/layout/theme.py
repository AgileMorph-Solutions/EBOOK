"""Design kit — curated theme presets, chosen per book by the PLANNING model.

Each preset is a set of design tokens (palette, fonts) emitted as CSS custom
properties. Presets are professionally chosen so the book never looks random.
There is NO keyword matching here: the planning stage shows the model a menu
described by mood/genre (menu_text) and the model picks; the only programmatic
step is validation with a neutral fallback.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Curated print-safe font library. Each role offers several vetted Google faces
# so the per-book DesignKit (bible.design) can pair a bespoke typeface set to the
# topic instead of every book sharing one stack. GOOGLE_SPECS maps a family name
# to its css2 axis spec; the @import is built dynamically from only the families
# a book actually uses (font_import_for), so we never load the whole library.
# BODY faces are chosen to have a PLAIN oval zero (Atkinson's slashed-by-design
# zero rendered "200" as "2ØØ" on title pages — keep it out of the body role).
# --------------------------------------------------------------------------- #
GOOGLE_SPECS: dict[str, str] = {
    # display / headings
    "Playfair Display": "Playfair+Display:ital,wght@0,500;0,700;0,800;0,900;1,500",
    "Fraunces": "Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,700;0,9..144,900;1,9..144,500",
    "Cormorant Garamond": "Cormorant+Garamond:ital,wght@0,500;0,600;0,700;1,500",
    "Libre Baskerville": "Libre+Baskerville:ital,wght@0,400;0,700;1,400",
    "DM Serif Display": "DM+Serif+Display:ital@0;1",
    "Spectral": "Spectral:ital,wght@0,500;0,700;0,800;1,500",
    "Archivo Black": "Archivo+Black",
    # body / text (plain zero)
    "EB Garamond": "EB+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400",
    "Atkinson Hyperlegible": "Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400;1,700",
    "Lora": "Lora:ital,wght@0,400;0,500;0,600;0,700;1,400",
    "Newsreader": "Newsreader:ital,wght@0,400;0,500;0,600;0,700;1,400",
    "Source Serif 4": "Source+Serif+4:ital,wght@0,400;0,600;0,700;1,400",
    "Inter": "Inter:wght@400;500;600;700",
    "Work Sans": "Work+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400",
    # condensed / labels / folios
    "Oswald": "Oswald:wght@400;500;600;700",
    "Barlow Condensed": "Barlow+Condensed:wght@400;500;600;700",
    "Archivo": "Archivo:wght@400;500;600;700",
    "Saira Condensed": "Saira+Condensed:wght@400;500;600;700",
}

# role -> {family name: full css font stack}. The MENU shown to the planner.
FONT_LIBRARY: dict[str, dict[str, str]] = {
    "display": {
        "Playfair Display": "'Playfair Display', Georgia, serif",
        "Fraunces": "'Fraunces', 'Times New Roman', serif",
        "Cormorant Garamond": "'Cormorant Garamond', Georgia, serif",
        "Libre Baskerville": "'Libre Baskerville', Georgia, serif",
        "DM Serif Display": "'DM Serif Display', Georgia, serif",
        "Spectral": "'Spectral', Georgia, serif",
        "Archivo Black": "'Archivo Black', 'Helvetica Neue', sans-serif",
    },
    "body": {
        "EB Garamond": "'EB Garamond', Georgia, serif",
        "Atkinson Hyperlegible": "'Atkinson Hyperlegible', 'Helvetica Neue', Arial, sans-serif",
        "Lora": "'Lora', Georgia, serif",
        "Newsreader": "'Newsreader', Georgia, serif",
        "Source Serif 4": "'Source Serif 4', Georgia, serif",
        "Inter": "'Inter', 'Helvetica Neue', Arial, sans-serif",
        "Work Sans": "'Work Sans', 'Helvetica Neue', Arial, sans-serif",
    },
    "cond": {
        "Oswald": "'Oswald', 'Arial Narrow', sans-serif",
        "Barlow Condensed": "'Barlow Condensed', 'Arial Narrow', sans-serif",
        "Archivo": "'Archivo', 'Helvetica Neue', sans-serif",
        "Saira Condensed": "'Saira Condensed', 'Arial Narrow', sans-serif",
    },
}

# Legacy default import (used as a fallback when a book uses no library families).
FONT_IMPORT = ("https://fonts.googleapis.com/css2?"
               "family=Playfair+Display:ital,wght@0,500;0,700;0,800;0,900;1,500"
               "&family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400;1,700"
               "&family=EB+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400"
               "&family=Oswald:wght@400;500;600;700&display=swap")

# High-legibility guide font stacks (overridden per preset only if needed).
_GUIDE_FONTS = {
    "display": "'Playfair Display', Georgia, serif",
    "cond": "'Oswald', 'Arial Narrow', sans-serif",
    "body": "'Atkinson Hyperlegible', 'Helvetica Neue', Arial, sans-serif",
}


def _primary_family(stack: str) -> str:
    """The first family name in a css font stack ("'Fraunces', serif" → Fraunces)."""
    first = stack.split(",")[0].strip().strip("'\"")
    return first


def font_import_for(tokens: dict[str, str]) -> str:
    """Build a Google Fonts @import URL from only the families this book uses
    (its display/body/cond stacks). Falls back to the legacy import if none of
    the families are recognized."""
    families: list[str] = []
    for role in ("display", "body", "cond"):
        fam = _primary_family(tokens.get(role, ""))
        spec = GOOGLE_SPECS.get(fam)
        if spec and spec not in families:
            families.append(spec)
    if not families:
        return FONT_IMPORT
    return ("https://fonts.googleapis.com/css2?"
            + "&".join(f"family={f}" for f in families) + "&display=swap")


def design_font_menu() -> str:
    """The font menu shown to the planning model when it designs a DesignKit."""
    lines = []
    for role, label in (("display", "DISPLAY (headings)"),
                        ("body", "BODY (running text — must read long)"),
                        ("cond", "COND (eyebrows/labels/folios)")):
        names = ", ".join(FONT_LIBRARY[role].keys())
        lines.append(f"  {label}: {names}")
    return "\n".join(lines)

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
    # --- high-legibility practical-guide presets (high contrast, big type) --- #
    # Bright, clean, hygienic — legacy cleaning-guide preset.
    "home_clean": {
        "paper": "#FAF8F4", "panel": "#E7F0EE", "panel_dark": "#143842",
        "ink": "#14222B", "accent": "#15616D", "deep": "#103039",
        "gold": "#C79A3E", "muted": "#4C5B62", "onpaper_soft": "#37454E",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        **_GUIDE_FONTS,
    },
    # Protective, serious — for pest-control books. Red reserved for danger only.
    "pest_guard": {
        "paper": "#FBFAF6", "panel": "#ECF0EA", "panel_dark": "#1E342B",
        "ink": "#14222B", "accent": "#2F5D50", "deep": "#1B3328",
        "gold": "#C79A3E", "muted": "#4C5B62", "onpaper_soft": "#37454E",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        **_GUIDE_FONTS,
    },
    # Warm burgundy/cream — legacy food/cooking guide preset.
    "senior_cookbook": {
        "paper": "#FAF6F0", "panel": "#F0E8DC", "panel_dark": "#3D2020",
        "ink": "#2A1A18", "accent": "#7B2D3B", "deep": "#4A1F28",
        "gold": "#C79A3E", "muted": "#6B5A52", "onpaper_soft": "#524640",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        **_GUIDE_FONTS,
    },
    # Vintage cream/tomato/pine, historical warmth — heritage/era-driven books.
    # Body is EB Garamond: its zero is a plain oval (Atkinson's slashed-glyph
    # zero produced the "2ØØ"/"$1O" title-page defects).
    "heritage_cookbook": {
        "paper": "#F5EDDC", "panel": "#EADDC0", "panel_dark": "#27433C",
        "ink": "#26201A", "accent": "#A93A28", "deep": "#274E45",
        "gold": "#C68A3C", "muted": "#78684C", "onpaper_soft": "#584E3C",
        "savings": "#2E7D5B", "caution": "#B5651D", "danger": "#B3261E",
        "display": "'Playfair Display', Georgia, serif",
        "cond": "'Oswald', 'Arial Narrow', sans-serif",
        "body": "'EB Garamond', Georgia, serif",
    },
}

# Guide presets the planning model may pick, described by MOOD — never by
# topic keywords. The model reasons from the book's angle to the look.
GUIDE_PRESET_MENU: dict[str, str] = {
    "home_clean": "bright, clinical calm — pale paper, deep teal, hygienic",
    "pest_guard": "protective, serious — deep greens, red reserved for danger",
    "senior_cookbook": "warm burgundy and cream — cozy, generous, homely",
    "heritage_cookbook": ("vintage cream, tomato red, pine green — historical "
                          "warmth, mid-century print feel"),
}

# Fallback when the model names an unknown preset — neutral and guide-safe
# (carries every token the guide CSS consumes).
NEUTRAL_GUIDE_PRESET = "home_clean"


def menu_text() -> str:
    """The preset menu shown to the planning model."""
    return "\n".join(f'  * "{k}": {v}' for k, v in GUIDE_PRESET_MENU.items())


# --------------------------------------------------------------------------- #
# Per-book DesignKit → tokens (with deterministic contrast validation).
# --------------------------------------------------------------------------- #
_REQUIRED_TOKENS = ("paper", "panel", "panel_dark", "ink", "accent", "deep",
                    "gold", "muted", "onpaper_soft", "savings", "caution",
                    "danger", "display", "body", "cond")


def _hex_to_rgb(h: str) -> tuple[int, int, int] | None:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _rel_lum(rgb: tuple[int, int, int]) -> float:
    def chan(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0–21.0); 0 if unparseable."""
    a, b = _hex_to_rgb(fg), _hex_to_rgb(bg)
    if not a or not b:
        return 0.0
    la, lb = _rel_lum(a), _rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def validate_kit(kit: dict[str, str] | None) -> dict[str, str] | None:
    """Validate a per-book design kit. Fills missing tokens from the neutral
    preset and enforces legibility: body ink on paper ≥ 6:1, white on dark panel
    ≥ 4.5:1, accent on paper ≥ 3:1. Returns validated tokens, or None if the kit
    is unusable (caller falls back to a preset)."""
    if not kit:
        return None
    base = PRESETS[NEUTRAL_GUIDE_PRESET]
    tokens = {k: (kit.get(k) or base.get(k, "")) for k in _REQUIRED_TOKENS}
    # colours must parse
    for k in ("paper", "panel", "panel_dark", "ink", "accent", "deep", "gold",
              "muted"):
        if _hex_to_rgb(tokens[k]) is None:
            return None
    checks = (
        contrast_ratio(tokens["ink"], tokens["paper"]) >= 6.0,
        contrast_ratio("#FFFFFF", tokens["panel_dark"]) >= 4.5,
        contrast_ratio(tokens["accent"], tokens["paper"]) >= 3.0,
        contrast_ratio(tokens["ink"], tokens["panel"]) >= 4.5,
    )
    if not all(checks):
        return None
    return tokens


def kit_from_design(design) -> dict[str, str] | None:
    """Turn a schemas.DesignKit (or None) into a validated token dict."""
    if design is None:
        return None
    raw = design.model_dump() if hasattr(design, "model_dump") else dict(design)
    return validate_kit({k: raw.get(k) for k in _REQUIRED_TOKENS})


def pick(topic: str, *, name: str | None = None, design=None) -> dict[str, str]:
    """Resolve the book's design tokens. Prefers a validated per-book DesignKit
    (bespoke, cinematic); else a preset chosen by NAME; else the neutral preset.
    `topic` is kept for call-site compatibility, never keyword-matched."""
    del topic
    kit = kit_from_design(design)
    if kit:
        return kit
    if name and name in PRESETS:
        return PRESETS[name]
    return PRESETS[NEUTRAL_GUIDE_PRESET]


def css_root(tokens: dict[str, str], page_w: float, page_h: float,
             bleed: float) -> str:
    """Build the :root design-token block, importing only the fonts this book
    uses (derived from its display/body/cond stacks)."""
    toks = ";".join(f"--{k}:{v}" for k, v in tokens.items())
    import_url = font_import_for(tokens)
    # --safe stays for top/bottom offsets; left/right use the mirrored pair
    # (KDP needs ≥0.625in gutter at 301-500 pages — 0.75in inner / 0.5in outer).
    return (f"@import url('{import_url}');"
            f":root{{--page-w:{page_w}in;--page-h:{page_h}in;"
            f"--bleed:{bleed}in;--safe:{bleed + 0.62}in;"
            f"--safe-inner:{bleed + 0.75}in;--safe-outer:{bleed + 0.5}in;"
            f"--measure:34em;{toks}}}")
