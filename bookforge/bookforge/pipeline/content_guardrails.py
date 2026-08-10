"""Content coherence guardrails for practical guides.

Keeps marketing counts in titles/subtitles aligned with generated prose and
prevents inflated tip totals in section intros and headings.
"""
from __future__ import annotations

import re

import structlog

from bookforge.schemas import BookBible, Chapter, SectionPlan

log = structlog.get_logger(__name__)

# NB: gap words must match hyphenated words (e.g. "200 Best-Loved Kitchen Tips") —
# plain \w+ silently failed there, so the subtitle promise was never parsed and
# the book shipped 1,176 entries under a "200" subtitle.
# NB: this is a static whitelist and will always be incomplete for genres we
# haven't seen yet (a "50 Franchise Concepts" or "50 Micro-SaaS Ideas" book
# hits the same gap the day it's added) — parse_exact_promised_count() below
# is the durable fix for those; this list is just a cheap, immediate widening.
_COUNT_NOUNS = (
    r"ways?|tips?|uses?|tricks?|methods?|ideas?|hacks?|recipes?|dishes?|meals?|suppers?|"
    r"dinners?|fixes?|projects?|patterns?|exercises?|lessons?|entries|habits?|steps?|rules?|"
    r"principles?|moves?|shifts?|strategies|strategy|secrets?|mistakes?|"
    r"reasons?|laws?|keys?|changes?|foods?|drinks?|workouts?|stretches|poses?|"
    r"questions?|skills?|routines?|swaps?|rituals?|practices?|"
    r"signs?|behaviors?|concepts?|indicators?|signals?|traits?|types?|facts?|truths?|examples?|"
    r"models?|frameworks?|services?|businesses?|profiles?|niches?|opportunities?|ventures?|"
    r"startups?|side\s?hustles?|gigs?"
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
# Gap words between count and noun (commas in "21 Cheap, Safe DIY Fixes",
# quotes in '100 "Normal" Adult Habits').
_GAP_WORDS = r"(?:\s+[\w\"'“”‘’.,-]+){0,6}"
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

# "Level 1 to 100 … Secrets" / "1-50 tips" — take the range end, not the start
# digit (which _TITLE_COUNT_RE would otherwise latch onto as the count).
# Anchored to a trailing count noun (same as _TITLE_COUNT_RE) — an unanchored
# range regex will happily match an unrelated numeric range in the same
# sentence, e.g. "around 150-180 pages", and misreport a page-count estimate
# as the item promise. Uses a SHORT gap (not the permissive _GAP_WORDS, which
# allows up to 6 words) — a wide gap lets the search skip straight over the
# range's own real trailing word ("pages", not a count noun) and latch onto
# an unrelated noun several words later in the same sentence.
_RANGE_GAP_WORDS = r"(?:\s+[\w\"'“”‘’.,-]+){0,3}"
_RANGE_COUNT_RE = re.compile(
    r"\b(?:levels?\s+)?(\d{1,4})\s*(?:to|-|–|—)\s*(\d{1,4})\b%s\s*(?:\+?\s*)?(?:%s)\b"
    % (_RANGE_GAP_WORDS, _COUNT_NOUNS),
    re.IGNORECASE,
)

_LEADING_COUNT_RE = re.compile(
    r"^(?:the\s+)?(?P<count>\d{1,4})\s+(?P<next>\w+)",
    re.IGNORECASE,
)
_DURATION_START_WORDS = frozenset({
    "hour", "hours", "hr", "hrs", "minute", "minutes", "min", "mins",
})
# Filler adjectives between count and list noun — skip when extracting item label.
_LABEL_SKIP_WORDS = frozenset({
    "dark", "psychological", "normal", "cheap", "forgotten", "brutal", "quick",
    "best", "deep", "simple", "easy", "hard", "adult", "ancient", "modern",
    "classic", "ultimate", "essential", "powerful", "surprising", "hidden",
    "subtle", "tiny", "micro", "real", "true", "raw", "lost", "secret",
    "deadly", "dangerous", "silent", "quiet", "loud", "weird", "strange",
    "unspoken", "invisible", "forbidden",
})
_STOP_WORDS = frozenset({
    "of", "that", "the", "a", "an", "to", "for", "and", "or", "in", "on", "at",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been",
})


def parse_leading_marketing_count(title: str) -> int | None:
    """Return a leading title count (``100 Signs…``) when no count-noun match exists.

    Rejects duration starts (``3 Hours…``), year-like counts (``2024 Guide``),
    and titles with no leading digit.
    """
    text = (title or "").strip()
    if not text:
        return None
    match = _LEADING_COUNT_RE.match(text)
    if not match:
        return None
    count = int(match.group("count"))
    if count < 1:
        return None
    if 1900 <= count <= 2099:
        return None
    next_word = match.group("next").lower().rstrip("s") if match.group("next") else ""
    if match.group("next").lower() in _DURATION_START_WORDS:
        return None
    if next_word in {"hour", "hr", "minute", "min"}:
        return None
    return count


def extract_title_item_label(title: str) -> str:
    """First meaningful list noun after a title count, default ``items``."""
    text = (title or "").strip()
    if not text:
        return "items"
    noun_re = re.compile(
        r"\b\d{1,4}\b" + _GAP_WORDS + r"\s*(?:\+?\s*)?(?P<noun>" + _COUNT_NOUNS + r")\b",
        re.IGNORECASE,
    )
    noun_match = noun_re.search(text)
    if noun_match:
        return noun_match.group("noun").lower()
    leading = _LEADING_COUNT_RE.match(text)
    if not leading:
        return "items"
    rest = text[leading.end() :].strip()
    for word in re.findall(r"\b[\w']+\b", rest):
        clean = word.strip("'\"").lower()
        if clean in _LABEL_SKIP_WORDS or clean in _STOP_WORDS:
            continue
        return clean
    return "items"


def parse_promised_count(title: str, subtitle: str) -> int | None:
    """Extract a marketing count from title or subtitle — digits ('250 ways') or
    spelled-out ('Fourteen Habits').

    Ranges like ``Level 1 to 100 Secrets`` resolve to the **end** of the range
    (100), not the start digit (1).
    """
    for text in (subtitle, title):
        if not text:
            continue
        ranged = _RANGE_COUNT_RE.search(text)
        if ranged:
            lo, hi = int(ranged.group(1)), int(ranged.group(2))
            if hi > lo >= 1:
                return hi
        m = _TITLE_COUNT_RE.search(text)
        if m:
            return int(m.group(1))
        w = _WORD_COUNT_RE.search(text)
        if w:
            n = _word_to_int(w.group(1))
            if n:
                return n
        leading = parse_leading_marketing_count(text)
        if leading is not None:
            return leading
    return None


_EXACTLY_N_RE = re.compile(r"\bexactly\s+(\d{1,4})\b", re.IGNORECASE)
_NUMBERED_ITEM_RE = re.compile(r"(?m)^\s*(\d{1,4})[.)]\s+\S")


def parse_exact_promised_count(text: str) -> int | None:
    """High-confidence EXACT item count from raw source `text` — never from
    title/subtitle, which are soft marketing copy (see parse_promised_count).

    Two signals, either being enough:
    - Explicit "create exactly N ..." / "exactly N ..." phrasing.
    - A complete sequential numbered list (1., 2., ... N.) with no gaps.

    Unlike parse_promised_count's result, this is treated as a hard cap, not
    just a floor: the plan must contain exactly this many entries, not "at
    least" this many."""
    if not text:
        return None
    m = _EXACTLY_N_RE.search(text)
    if m:
        return int(m.group(1))
    nums = [int(x) for x in _NUMBERED_ITEM_RE.findall(text)]
    if len(nums) >= 5 and nums == list(range(1, len(nums) + 1)):
        return nums[-1]
    return None


_TOC_BARE_ORDINAL_RE = re.compile(r"^\d{1,3}$")


def count_toc_sections(toc: list[str] | None) -> int:
    """Real section count for a raw TOC list, excluding standalone
    numeric-ordinal placeholder lines ("01", "02", ...) — the artifact of
    pasting a TOC formatted with the chapter number on its own line above
    its title. Those lines mark the FOLLOWING line as a header rather than
    being sections themselves (see bible.py's _parse_toc_lines, which does
    the equivalent merge for the real section-building path).

    Anything using a raw `len(toc)` as a section-count proxy — as
    plan_scope.build_plan_scope does for the coarse feasibility check —
    must use this instead, or a "01\\n<Title>" paste silently doubles the
    section count that check reasons about, producing a page-count estimate
    that disagrees with the real (correctly-normalized) build."""
    if not toc:
        return 0
    return sum(
        1 for t in toc
        if t.strip() and not _TOC_BARE_ORDINAL_RE.match(t.strip())
    )


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
    """Expand section quotas toward the promised count; densify rather than shrink.

    Never silently rewrites the marketing count downward. If the plan cannot
    honor the promise at the current profile density, raise PlanInfeasibleError
    so readiness / the user can choose raise-pages or accept a lower count.
    """
    from bookforge.errors import PlanInfeasibleError
    from bookforge.pipeline.feasibility import apply_compact_profile
    from bookforge.pipeline.page_limits import max_planned_entries

    promised = parse_promised_count(bible.title, bible.subtitle)
    planned = planned_use_count(bible.sections)
    if promised is None:
        return bible.model_copy(update={"promised_uses": planned})

    sections = list(bible.sections)
    title, subtitle = bible.title, bible.subtitle
    if promised > planned:
        sections = expand_sections_to_count(sections, promised)
        planned = planned_use_count(sections)

    if bible.profile and bible.book_kind == "practical_guide":
        target = bible.target_pages or 100
        pamphlet = target <= 15
        profile = bible.profile
        cap = max_planned_entries(
            target, profile, len(sections) or 1, pamphlet=pamphlet,
        )
        if planned > cap and not pamphlet:
            # Prefer compact density over shrinking the count.
            compact = apply_compact_profile(profile)
            cap_c = max_planned_entries(
                target, compact, len(sections) or 1, pamphlet=pamphlet,
            )
            if planned <= cap_c:
                log.info(
                    "reconcile_promise_compact",
                    promised=promised,
                    planned=planned,
                    ceiling=target,
                )
                return bible.model_copy(update={
                    "sections": sections,
                    "profile": compact,
                    "promised_uses": promised,
                })
            raise PlanInfeasibleError(
                f"promised {promised} items exceed page budget at {target} pages "
                f"(max ~{cap_c} even when shortened) — raise pages or accept a "
                f"lower count at readiness"
            )
        if planned > cap and pamphlet:
            # Pamphlets may still shrink to the explicit promise ceiling.
            if planned > promised:
                sections = shrink_sections_to_count(sections, promised)
                planned = promised

    return bible.model_copy(update={
        "sections": sections,
        "title": title,
        "subtitle": subtitle,
        "promised_uses": promised,
    })


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
    """Sync marketing copy to CURRENT actual entries, floored by the user's
    ORIGINAL promise — never permanently stuck at a transient over-generation.
    Unlike a plain "never go below the last promise" rule, this resyncs
    `promised_uses` up OR down to match actual content each call, so later
    trimming correctly lowers the stated count back toward the truth instead
    of leaving marketing copy pinned at an earlier inflated value."""
    actual = count_generated_entries(chapters)
    if actual <= 0:
        return
    floor = bible.original_promised_uses or _promised(bible)
    target = max(actual, floor) if floor > 0 else actual
    if target == bible.promised_uses:
        return
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
