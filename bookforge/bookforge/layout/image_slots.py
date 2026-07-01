"""Print image slot dimensions — single source of truth for crop targets.

Each placement maps to pixel dimensions at 300 DPI for an 8.5×11 trim page.
Used by pipeline.images when right-sizing generated art to its HTML slot.
"""
from __future__ import annotations

from typing import Literal

Placement = Literal["card", "divider", "cover"]

# Text panel width on section dividers (trim inches); photo fills the rest.
DIVIDER_PANEL_IN = 3.45

# (width_px, height_px) at 300 DPI — divider width = page trim − panel + bleed
_SLOT_PX: dict[Placement, tuple[int, int]] = {
    "card": (1200, 1200),
    "divider": (1575, 3375),    # ~5.25in × 11.25in bleed box
    "cover": (2550, 1800),
}

_SLOT_RATIO: dict[Placement, str] = {
    "card": "1 / 1",
    "divider": "5.05 / 11",
    "cover": "17 / 12",
}

# Map ImageBrief.aspect values to a default placement when none is given.
_ASPECT_DEFAULT: dict[str, Placement] = {
    "square": "card",
    "portrait": "divider",
    "full_bleed": "cover",
    "wide": "cover",
}


def box_for(placement: Placement) -> tuple[int, int]:
    return _SLOT_PX[placement]


def placement_for_aspect(aspect: str, *, hint: Placement | None = None) -> Placement:
    if hint:
        return hint
    return _ASPECT_DEFAULT.get(aspect, "card")


def box_for_brief(aspect: str, *, placement: Placement | None = None) -> tuple[int, int]:
    """Return target (w, h) pixels for an image brief."""
    return box_for(placement_for_aspect(aspect, hint=placement))
