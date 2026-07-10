"""Content coherence guardrails for practical guides.

Keeps marketing counts in titles/subtitles aligned with generated prose and
prevents inflated tip totals in section intros and headings.
"""
from __future__ import annotations

import re

from bookforge.schemas import BookBible, Chapter, SectionPlan

# NB: gap words must match hyphenated words (e.g. "200 Best-Loved Kitchen Tips") —
# plain \w+ silently failed there, so the subtitle promise was never parsed and
# the book shipped 1,176 entries under a "200" subtitle.
_COUNT_NOUNS = (
    r"ways?|tips?|uses?|tricks?|methods?|ideas?|hacks?|recipes?|dishes?|meals?|"
    r"fixes?|projects?|patterns?|exercises?|lessons?|entries|habits?|steps?|rules?|"
    r"principles?|moves?|shifts?|strategies|strategy|secrets?|mistakes?|"
    r"reasons?|laws?|keys?|changes?|foods?|drinks?|workouts?|stretches|poses?|"
    r"questions?|skills?|routines?|swaps?|rituals?|practices?"
)
# Spelled-out cardinals (ten … ninety-nine) so "sixty habits" is caught too.
_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_NUMBER_WORDS = {**_ONES, **_TEENS, **_TENS}
_WORDS_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
# Gap words between count and noun (commas in "21 Cheap, Safe DIY Fixes").
_GAP_WORDS = r"(?:\s+[\w',.-]+){0,6}"
_WORD_COUNT_RE = re.compile(
    r"\b((?:%s)(?:[- ](?:%s))?)\b%s\s*(?:%s)\b"
    % (_WORDS_ALT, "|".join(_ONES), _GAP_WORDS, _COUNT_NOUNS),
    re.IGNORECASE,
)


def _word_to_int(phrase: str) -> int | None:
    """'sixty' → 60, 'twenty-one' → 21. None if not a recognized cardinal."""
    parts = re.split(r"[- ]", phrase.strip().lower())
    total = 0
    for p in parts:
        if p in _NUMBER_WORDS:
            total += _NUMBER_WORDS[p]
        elif p:
            return None
    return total or None


_COUNT_RE = re.compile(
    r"\b(\d{2,4})\b%s\s*(?:\+?\s*)?(?:%s)\b" % (_GAP_WORDS, _COUNT_NOUNS),
    re.IGNORECASE,
)
_INFLATED_RE = re.compile(
    r"\b(?:over|more than|nearly|almost|about|around|up to)\s+(\d{2,4})\b",
    re.IGNORECASE,
)
_BARE_TOTAL_RE = re.compile(
    r"\b(\d{2,4})\s+(?:numbered\s+)?(?:%s)\b" % _COUNT_NOUNS,
    re.IGNORECASE,
)


_TITLE_COUNT_RE = re.compile(
    r"\b(\d{1,4})\b%s\s*(?:\+?\s*)?(?:%s)\b" % (_GAP_WORDS, _COUNT_NOUNS),
    re.IGNORECASE,
)


def parse_promised_count(title: str, subtitle: str) -> int | None:
    """Extract a marketing count from title or subtitle — digits ('250 ways') or
    spelled-out ('Fourteen Habits')."""
    for text in (subtitle, title):
        m = _TITLE_COUNT_RE.search(text or "")
        if m:
            return int(m.group(1))
        w = _WORD_COUNT_RE.search(text or "")
        if w:
            n = _word_to_int(w.group(1))
            if n:
                return n
    return None


def reconcile_promise(title: str, subtitle: str, planned: int,
                      *, tolerance: float = 0.10) -> tuple[str, str, bool]:
    """If the title/subtitle promises a count far above planned, leave copy
    unchanged — promised count wins and sections will expand to match."""
    promised = parse_promised_count(title, subtitle)
    if promised is None or planned <= 0:
        return title, subtitle, False
    if promised >= planned:
        return title, subtitle, False
    if abs(promised - planned) <= tolerance * promised:
        return title, subtitle, False

    def _swap(text: str) -> str:
        m = _TITLE_COUNT_RE.search(text)
        if not m:
            return text
        s, e = m.span(1)
        return text[:s] + str(planned) + text[e:]

    return _swap(title), _swap(subtitle), True


def shrink_sections_to_count(
    sections: list[SectionPlan], target: int,
) -> list[SectionPlan]:
    """Reduce list_quota first, then full_card_quota, to hit `target` uses."""
    out = [s.model_copy() for s in sections]
    for s in out:
        s.list_quota = 0
    current = planned_use_count(out)
    if current <= target:
        return out

    n = len(out) or 1
    remaining = target
    trimmed: list[SectionPlan] = []
    for i, s in enumerate(out):
        slots = n - i
        share = max(0, remaining // slots) if i < n - 1 else remaining
        cards = min(s.full_card_quota, share)
        trimmed.append(s.model_copy(update={"full_card_quota": cards, "list_quota": 0}))
        remaining -= cards
    return trimmed


def expand_sections_to_count(
    sections: list[SectionPlan], target: int,
) -> list[SectionPlan]:
    """Raise full_card_quota (then list_quota) until planned count reaches target."""
    out = [s.model_copy() for s in sections]
    current = planned_use_count(out)
    if current >= target:
        return out

    extra = target - current
    n = len(out) or 1
    # Distribute extra full cards first (deeper sections).
    for i, s in enumerate(out):
        if extra <= 0:
            break
        slots_left = n - i
        add = extra // slots_left if i < n - 1 else extra
        out[i] = s.model_copy(update={"full_card_quota": s.full_card_quota + add})
        extra -= add

    current = planned_use_count(out)
    if current < target:
        out = adjust_list_quotas(out, target - current)
    return out


def adjust_list_quotas(
    sections: list[SectionPlan], extra: int,
) -> list[SectionPlan]:
    """Distribute `extra` additional quick-list slots across sections."""
    if extra <= 0 or not sections:
        return [s.model_copy() for s in sections]
    out = [s.model_copy() for s in sections]
    per, rem = divmod(extra, len(out))
    for i, s in enumerate(out):
        s.list_quota += per + (1 if i < rem else 0)
    return out


def rewrite_promised_number(
    title: str, subtitle: str, target: int,
) -> tuple[str, str]:
    """Replace marketing counts in title/subtitle with `target`."""
    return _rewrite_counts(title, target), _rewrite_counts(subtitle, target)


def reconcile_promise_book(bible: BookBible) -> BookBible:
    """Expand section quotas to match the promised marketing count."""
    promised = parse_promised_count(bible.title, bible.subtitle)
    planned = planned_use_count(bible.sections)
    if promised is None:
        return bible.model_copy(update={"promised_uses": planned})

    sections = list(bible.sections)
    if promised > planned:
        sections = expand_sections_to_count(sections, promised)
        planned = planned_use_count(sections)
        bible = bible.model_copy(update={"sections": sections})

    return bible.model_copy(update={"promised_uses": promised})


def _rewrite_counts(text: str, target: int) -> str:
    """Replace every count token (digit or spelled-out, e.g. '14'/'fourteen'
    before a count noun) with `target`, so all planner-authored copy states ONE
    number. Leaves non-count numbers untouched."""
    if not text:
        return text

    def _swap(m: "re.Match") -> str:
        return m.group(0).replace(m.group(1), str(target), 1)

    text = _TITLE_COUNT_RE.sub(_swap, text)
    text = _WORD_COUNT_RE.sub(_swap, text)
    return text


def propagate_promised_count(bible: BookBible) -> None:
    """Make every planner-authored field state the ONE promised count — title,
    subtitle, cover straplines, the how-to page, section/part titles, list lead.
    The chapter BODY is handled separately by the QA fix-loop
    (find_count_contradictions). Fixes the "title 50 / kicker 14 / part
    'THE FOURTEEN HABITS'" incoherence."""
    target = _promised(bible)
    if target <= 0:
        return
    bible.title = _rewrite_counts(bible.title, target)
    bible.subtitle = _rewrite_counts(bible.subtitle, target)
    if bible.profile:
        bible.profile.cover_kicker = _rewrite_counts(bible.profile.cover_kicker, target)
        bible.profile.cover_foot = _rewrite_counts(bible.profile.cover_foot, target)
    if bible.how_to:
        bible.how_to.lead = _rewrite_counts(bible.how_to.lead, target)
        bible.how_to.points = [_rewrite_counts(p, target) for p in bible.how_to.points]
    for s in bible.sections:
        s.title = _rewrite_counts(s.title, target)
        s.part_title = _rewrite_counts(s.part_title, target)
    bible.list_lead = _rewrite_counts(bible.list_lead, target)


def planned_use_count(sections: list[SectionPlan]) -> int:
    """Sum of full cards + quick uses the book will actually generate."""
    return sum(s.full_card_quota + s.list_quota for s in sections)


def count_generated_entries(chapters: list[Chapter]) -> int:
    """Count use_card + use_list entries actually present in chapter content."""
    from bookforge.schemas import BlockType

    total = 0
    for ch in chapters:
        for block in ch.blocks:
            if block.type in (BlockType.use_card, BlockType.use_list):
                total += len(block.entries or [])
    return total


def finalize_marketing_counts(bible: BookBible, chapters: list[Chapter]) -> None:
    """Sync marketing copy to actual entries, never below the promised count."""
    actual = count_generated_entries(chapters)
    if actual <= 0:
        return
    floor = _promised(bible)
    target = max(actual, floor) if floor > 0 else actual
    bible.promised_uses = target
    propagate_promised_count(bible)


def _promised(bible: BookBible) -> int:
    if bible.promised_uses is not None:
        return bible.promised_uses
    return parse_promised_count(bible.title, bible.subtitle) or planned_use_count(bible.sections)


def _planned(bible: BookBible) -> int:
    return planned_use_count(bible.sections)


def coherence_rules(bible: BookBible) -> str:
    """Prompt block injected into practical-guide writers."""
    from bookforge.schemas import profile_of

    promised = _promised(bible)
    planned = _planned(bible)
    noun = profile_of(bible).entry_schema.entry_noun
    return (
        f'BOOK COHERENCE: This book is titled "{bible.title}" with subtitle '
        f'"{bible.subtitle}". It contains exactly {promised} {noun}s — that is the '
        f"ONE true total. NEVER state any other count anywhere in prose, whether "
        f"in digits or spelled out (do not write \"sixty {noun}s\" if the book has "
        f"{promised}). Section headings must match their topic. "
        f"Use correct, consistent spelling for every ingredient, product, and "
        f"proper-noun name — one canonical name per thing, book-wide. "
        f"No invented words."
    )


def find_count_contradictions(
    chapters: list[Chapter], bible: BookBible,
) -> list[str]:
    """Every prose mention of a total (digit OR spelled-out) that disagrees with
    the book's promised count — precise faults for the QA fix-loop to rewrite."""
    promised = _promised(bible)
    if promised <= 0:
        return []
    out: list[str] = []
    for loc, text in _collect_text(chapters):
        seen: set[int] = set()
        for m in _COUNT_RE.finditer(text):
            seen.add(int(m.group(1)))
        for m in _WORD_COUNT_RE.finditer(text):
            n = _word_to_int(m.group(1))
            if n:
                seen.add(n)
        for count in seen:
            if count != promised and count >= 5:
                out.append(
                    f"{loc}: prose says {count} but the book has {promised} — "
                    f"rewrite to the correct total: {text[:70]!r}")
    return out


def _collect_text(chapters: list[Chapter]) -> list[tuple[str, str]]:
    """Return (location, text) pairs from chapter blocks."""
    out: list[tuple[str, str]] = []
    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            loc = f"ch{ch.index}.{bi}({block.type.value})"
            if block.text:
                out.append((loc, block.text))
            if block.heading:
                out.append((loc, block.heading))
            for entry in block.entries or []:
                for attr in ("title", "intro", "savings"):
                    t = getattr(entry, attr, "") or ""
                    if t:
                        out.append((loc, t))
    return out


def _is_drift(count: int, promised: int, planned: int) -> bool:
    """True if count is wildly off the promised or planned totals."""
    if count == promised:
        return False
    anchor = planned
    if abs(planned - promised) / max(promised, 1) <= 0.25:
        anchor = promised
    if count == anchor:
        return False
    if count > anchor * 1.15:
        return True
    if count < anchor * 0.5 and count >= 20:
        return True
    return False


def scan_chapters_for_count_drift(
    chapters: list[Chapter], bible: BookBible,
) -> list[str]:
    """Post-write warnings when prose mentions inflated tip totals."""
    promised = _promised(bible)
    planned = _planned(bible)
    issues: list[str] = []

    for loc, text in _collect_text(chapters):
        for pat in (_COUNT_RE, _INFLATED_RE, _BARE_TOTAL_RE):
            for m in pat.finditer(text):
                count = int(m.group(1))
                if _is_drift(count, promised, planned):
                    issues.append(
                        f"{loc}: mentions {count} tips/ways but book promises "
                        f"{promised} (planned {planned}): {text[:80]!r}"
                    )
    return issues
