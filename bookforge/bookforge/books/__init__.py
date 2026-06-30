"""Book definitions for the practical-guide pipeline (dev fixtures).

Each book module exposes `get() -> GuideBook` for `gen_guide.py` regression.
Production API books are planned dynamically from source via bible.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bookforge.schemas import BookBible, SpecialPlan


@dataclass
class Special:
    """A chart or checklist special page attached after a given section."""
    after_section: int
    kind: str
    heading: str
    columns: list[str] = field(default_factory=list)
    rows: int = 6
    about: str = ""
    items: int = 10

    def to_plan(self) -> SpecialPlan:
        return SpecialPlan(
            after_section=self.after_section,
            kind=self.kind,  # type: ignore[arg-type]
            heading=self.heading,
            columns=self.columns,
            rows=self.rows,
            about=self.about,
            items=self.items,
        )


@dataclass
class GuideBook:
    bible: BookBible

    @property
    def safety_key(self) -> str:
        return self.bible.safety_key or "hp"

    @property
    def cover_subject(self) -> str:
        return self.bible.cover_subject

    @property
    def how_to(self) -> dict:
        return self.bible.how_to.model_dump() if self.bible.how_to else {}

    @property
    def pro_topic(self) -> str:
        return self.bible.pro_topic

    @property
    def specials(self) -> list[Special]:
        return [
            Special(
                after_section=s.after_section, kind=s.kind, heading=s.heading,
                columns=s.columns, rows=s.rows, about=s.about, items=s.items,
            )
            for s in self.bible.specials
        ]

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
