"""Book definitions for the practical-guide pipeline.

Each book module exposes `get() -> GuideBook`: a fully specified BookBible
(art style, senior voice, and the exact TOC sections with per-section quotas)
plus the front/back matter and special pages (charts, checklists, pro page).
Defining sections here (rather than asking the model to plan them) guarantees
every table-of-contents topic is covered; Claude writes the actual content.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bookforge.schemas import BookBible


@dataclass
class Special:
    """A chart or checklist special page attached after a given section."""
    after_section: int          # section index it follows (0 = front matter)
    kind: str                   # "chart" | "checklist"
    heading: str
    # chart:
    columns: list[str] = field(default_factory=list)
    rows: int = 6
    # checklist:
    about: str = ""
    items: int = 10


@dataclass
class GuideBook:
    bible: BookBible
    safety_key: str             # "hp" | "pest"
    cover_subject: str
    how_to: dict                # {heading, lead, points}
    pro_topic: str
    specials: list[Special] = field(default_factory=list)

    def specials_for(self, section_index: int) -> list[Special]:
        return [s for s in self.specials if s.after_section == section_index]


def load(name: str) -> GuideBook:
    if name in ("hydrogen_peroxide", "hp"):
        from bookforge.books import hydrogen_peroxide
        return hydrogen_peroxide.get()
    if name in ("pest_control", "pest"):
        from bookforge.books import pest_control
        return pest_control.get()
    raise ValueError(f"unknown book: {name}")
