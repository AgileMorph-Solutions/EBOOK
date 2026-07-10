"""Practical-guide layout director — big type, full-page-fill templates.

Emits a self-contained HTML document styled by guide.css (NOT the legacy
cookbook book.css), so the recipe path is untouched. Page order:

    cover -> title -> how-to-use -> safety -> TOC -> [per section: divider,
    opener essay, entry cards, feature boxes, compact list pages, charts,
    checklists] -> back matter

Folio numbers equal absolute page position, so the TOC (rendered into reserved
slots, sized BEFORE sections are laid out) cites correct start pages. All
labels, kickers, and matter come from the book's EditorialProfile — nothing
domain-specific is hardcoded here.
"""
from __future__ import annotations

import html
import math
import re
from pathlib import Path

import structlog

from bookforge import safety as safety_mod
from bookforge.config import settings
from bookforge.layout import theme
from bookforge.layout.build import _uri  # data-URI from a storage key
from bookforge.layout.labels import divider_title, section_eyebrow
from bookforge.schemas import (
    BlockType, BookBible, Chapter, GeneratedImage, profile_of,
)

log = structlog.get_logger(__name__)

_GUIDE_CSS = (Path(__file__).parent / "guide.css").read_text()

# TOC sizing: rows per page across 2 columns (rows are break-inside:avoid, so
# capacity is deterministic enough to reserve slots up front).
_TOC_ROWS_PER_PAGE = 44
_TOC_GROUP_COST = 2  # a group header costs ~2 row heights


def _esc(t) -> str:
    return html.escape(str(t)) if t else ""


_PASSAGE_ID_RE = re.compile(r"\bp\d{3,4}\b")
_BRACKET_PASSAGE_RE = re.compile(r"\[p\d{3,4}\]", re.IGNORECASE)


def _strip_passage_ids(text: str) -> str:
    """Remove leaked internal passage ids (e.g. "p0003", "[p0002]") and any empty
    parenthetical / dangling-separator leftovers, so reader-facing text never
    prints citations like "(p0003, p0002)" or "Adapted from []"."""
    if not text:
        return text
    t = _BRACKET_PASSAGE_RE.sub("", text)
    t = _PASSAGE_ID_RE.sub("", t)
    # tidy separators left dangling by the removed ids
    t = re.sub(r"\(\s*[,;·]\s*", "(", t)
    t = re.sub(r"\s*[,;·]\s*\)", ")", t)
    t = re.sub(r"\(\s*\)", "", t)          # drop now-empty "()"
    t = re.sub(r"\[\s*\]", "", t)          # drop now-empty "[]"
    t = re.sub(r"\s{2,}", " ", t)           # collapse doubled spaces
    t = re.sub(r"\s+([,.;:])", r"\1", t)    # no space before punctuation
    t = t.strip().strip(",;· ").strip()
    # En/em dashes in dollar ranges → ASCII hyphen (condensed fonts overlap otherwise).
    t = re.sub(r"(\$[\d,.]+)\s*[–—]\s*(\$?[\d,.]+)", r"\1-\2", t)
    # A bare "Adapted from" with no source is worse than omitting the line.
    if re.fullmatch(r"(?i)adapted from\.?", t):
        return ""
    return t


class GuideDirector:
    def __init__(self, bible: BookBible):
        self.bible = bible
        self.profile = profile_of(bible)
        self.title = _esc(bible.title)
        self.pages: list[str] = []

    # ---- helpers ---- #
    def _pos(self) -> int:
        return len(self.pages) + 1

    def _chrome(self, folio: int) -> str:
        return (f'<div class="grunhead">{self.title}</div>'
                f'<div class="gfolio">{folio}</div>')

    def _body(self, inner: str, *, folio: int) -> str:
        """A standard numbered body page: chrome + a fit region."""
        return (f'<section class="page">{self._chrome(folio)}'
                f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

    @staticmethod
    def _fitmin(level: int) -> str:
        """Escalated fit floor for bounded page types: the audit-driven repair
        first concedes a slightly smaller floor (11.5pt), then the readability
        minimum (11pt) — reflow-style repairs are preferred where they exist;
        this is the typographic concession rung of the ladder.

        Level 4 is the TERMINAL RESCUE floor (9pt — small print, still well
        above KDP's 7pt preflight minimum): it fits ~1.9x the lines of the
        12.5pt nominal floor, so combined with each template's structural
        reflow no schema-bounded content can overflow it. A book never fails
        for layout; a rescued page is logged loudly instead."""
        if level >= 4:
            return ' data-fitmin="9"'
        if level >= 2:
            return ' data-fitmin="11"'
        if level == 1:
            return ' data-fitmin="11.5"'
        return ""

    # ---- front matter ---- #
    @staticmethod
    def _echoes(a: str, b: str) -> bool:
        """True when two cover lines say substantially the same thing — the
        LLM-planned kicker/foot often paraphrase the subtitle, and three
        near-identical lines clutter the cover band (vision-QA finding)."""
        wa = {w for w in a.lower().split() if len(w) > 3}
        wb = {w for w in b.lower().split() if len(w) > 3}
        if not wa or not wb:
            return False
        return len(wa & wb) / min(len(wa), len(wb)) >= 0.6

    def cover(self, uri: str | None, title: str, sub: str, kicker: str, foot: str) -> None:
        img = f'<img src="{uri}"/>' if uri else ""
        if foot and (self._echoes(foot, sub) or self._echoes(foot, kicker)):
            foot = ""
        if kicker and self._echoes(kicker, sub):
            kicker = ""
        foot_html = f'<div class="cfoot">{_esc(foot)}</div>' if foot else ""
        kick_html = f'<div class="ckicker">{_esc(kicker)}</div>' if kicker else ""
        self.pages.append(
            f'<section class="page gcover">'
            f'<div class="cphoto">{img}</div>'
            f'<div class="cband">{kick_html}'
            f'<div class="cmid"><div class="ctitle">{_esc(title)}</div>'
            f'<div class="csub">{_esc(sub)}</div></div>'
            f'{foot_html}</div></section>')

    def title_page(self, title: str, sub: str, imprint: str) -> None:
        self.pages.append(
            f'<section class="page gtitle"><div class="wrap">'
            f'<div class="t">{_esc(title)}</div><div class="rule"></div>'
            f'<div class="s">{_esc(sub)}</div>'
            f'<div class="imp">{_esc(imprint)}</div></div></section>')

    def how_to_use(self, heading: str, lead: str, points: list[str],
                   level: int = 0) -> None:
        """Welcome page. Repair ladder (level = audit escalation): 0 = the
        roomy 4-card page; 1 = compact cards + lower fit floor; 2+ = the points
        split across two pages so no bullet is ever clipped."""
        compact = " howto-compact" if level >= 1 else ""
        fitmin = self._fitmin(level)

        def page(pts: list[str], start: int, cont: bool) -> str:
            lis = "".join(f"<li>{_esc(p)}</li>" for p in pts)
            title = _esc(heading)
            lead_html = "" if cont else f'<div class="glead">{_esc(lead)}</div>'
            head = (f'<div class="ghead"><div class="kick">Welcome</div>'
                    f'<div class="gh2">{title}</div>{lead_html}</div>')
            # keep the numbered badges continuous across the split
            counter = f' style="counter-reset: h {start}"' if start else ""
            fill = "fill-stack" if cont else "fill-spread"
            cont_cls = " howto-continued" if cont else ""
            inner = f'{head}<ol class="gbodyfill {fill}"{counter}>{lis}</ol>'
            return (f'<section class="page howto{compact}{cont_cls}" data-entry="howto">'
                    f'{self._chrome(self._pos())}'
                    f'<div class="gbody-box"><div class="gfit"{fitmin}>{inner}</div>'
                    f'</div></section>')

        if level >= 2 and len(points) > 2:
            half = (len(points) + 1) // 2
            self.pages.append(page(points[:half], 0, False))
            self.pages.append(page(points[half:], half, True))
        else:
            self.pages.append(page(points, 0, False))

    def safety_page(self, lead: str, never_mix: list[tuple[str, str]]) -> None:
        cards = "".join(
            f'<div class="nm"><div class="pair">{_esc(a)}</div>'
            f'<div class="why">{_esc(b)}</div></div>' for a, b in never_mix)
        self.pages.append(
            f'<section class="page gsafety">'
            f'<div class="band"><div class="bt"><div class="bk">Please read first</div>'
            f'<h2>Safety First</h2></div></div>'
            f'<div class="body"><p class="lead">{_esc(lead)}</p>'
            f'<div class="nevermix-label">Never do these</div>'
            f'<div class="nm-grid">{cards}</div></div></section>')

    # ---- TOC (multi-page, part-grouped, reserved before sections) ---- #
    @staticmethod
    def toc_page_count(groups: list[tuple[str, list]]) -> int:
        rows = sum(len(rws) + (_TOC_GROUP_COST if hdr else 0)
                   for hdr, rws in groups)
        return max(1, math.ceil(rows / _TOC_ROWS_PER_PAGE))

    def toc_pages(self, groups: list[tuple[str, list[tuple[str, int]]]],
                  page_slots: int) -> list[str]:
        """Render the TOC into exactly `page_slots` pages. groups = [(header,
        [(label, page), ...]), ...]; each row is unbreakable so a page number
        can never orphan onto its own line."""
        # Flatten to (header-or-None, label, page) stream, then re-chunk.
        stream: list[tuple[str | None, str, int]] = []
        for header, rows in groups:
            first = True
            # rows ascending by page so folios read monotonically down the TOC
            for lbl, pg in sorted(rows, key=lambda r: r[1]):
                stream.append((header if first else None, lbl, pg))
                first = False

        per_page = max(8, math.ceil(
            (len(stream) + _TOC_GROUP_COST * len(groups)) / page_slots))
        pages: list[str] = []
        i = 0
        for pi in range(page_slots):
            chunk = stream[i:i + per_page] if pi < page_slots - 1 else stream[i:]
            i += len(chunk)
            blocks, cur_rows, cur_head = "", "", None

            def flush(head, rows_html):
                nonlocal blocks
                if rows_html:
                    h = f'<div class="gt">{_esc(head)}</div>' if head else ""
                    blocks += f'<div class="grp">{h}{rows_html}</div>'

            for header, lbl, pg in chunk:
                if header is not None:
                    flush(cur_head, cur_rows)
                    cur_head, cur_rows = header, ""
                cur_rows += (
                    f'<div class="row"><span class="tt">{_esc(lbl)}</span>'
                    f'<span class="dots"></span><span class="pg">{pg}</span></div>')
            flush(cur_head, cur_rows)
            pages.append(
                f'<section class="page toc">'
                f'<div class="head"><div class="kick">The table of</div>'
                f'<h2>Contents</h2><div class="rule"></div></div>'
                f'<div class="cols">{blocks}</div></section>')
        return pages

    # ---- section pages ---- #
    def divider(self, number: int, title: str, kicker: str,
                hero_uri: str | None = None, part_title: str = "") -> None:
        img = f'<div class="dphoto-inner"><img src="{hero_uri}"/></div>' if hero_uri else ""
        cls = "page gdivider has-img" if hero_uri else "page gdivider"
        part = (f'<div class="part">{_esc(part_title)}</div>' if part_title else "")
        self.pages.append(
            f'<section class="{cls}"><div class="dphoto">{img}</div>'
            f'<div class="dpanel"><div class="dpanel-inner">{part}'
            f'<div class="num">Chapter {number}</div>'
            f'<div class="t">{_esc(title)}</div><div class="rule"></div>'
            f'<div class="k">{_esc(kicker)}</div></div></div></section>')

    # One essay page holds ~330 words at the base size; longer essays paginate
    # (a single overflowing page shrinks to the fit floor and then collides
    # with the folio — the page-7/18 defect vision QA flagged).
    _ESSAY_WORDS_FIRST = 330
    _ESSAY_WORDS_CONT = 420

    def essay_page(self, eyebrow: str, heading: str, text: str,
                   pull_quote: str = "", key: str = "",
                   squeeze: bool | int = False) -> None:
        """`key` tags the pages as data-entry="essay:{key}" for the render
        audit; `squeeze` is the audit-driven repair level — fewer words per
        page for an essay that MEASURED as overflowing at the fit floor
        (level 1 = 0.7×, level 2+ = 0.55×)."""
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        if len(paras) == 1:
            # split a wall of text at sentence boundaries into 2-4 paragraphs
            import re
            sents = re.split(r"(?<=[.!?]) +", paras[0])
            if len(sents) >= 6:
                per = max(2, len(sents) // max(2, min(4, len(sents) // 3)))
                paras = [" ".join(sents[i:i + per])
                         for i in range(0, len(sents), per)]

        # Paginate paragraphs, but BALANCE across pages so a continuation never
        # ends after two lines (a ~500-word essay becomes 2 even pages, not a
        # full page + a near-empty tail). Decide the page count from the total,
        # then fill to an even per-page word target.
        total_words = sum(len(p.split()) for p in paras)
        per_page = self._ESSAY_WORDS_FIRST
        if int(squeeze) >= 2:
            per_page = int(per_page * 0.55)
        elif int(squeeze) == 1:
            per_page = int(per_page * 0.7)
        npages = max(1, math.ceil(total_words / per_page))
        even_budget = math.ceil(total_words / npages)
        chunks: list[list[str]] = [[]]
        used = 0
        for p in paras:
            w = len(p.split())
            if chunks[-1] and used + w > even_budget and len(chunks) < npages:
                chunks.append([])
                used = 0
            chunks[-1].append(p)
            used += w

        # A single-chunk (short) essay is vertically centered so its empty space
        # reads as balanced, deliberate margin rather than a top-loaded page with
        # a blank bottom half. Multi-chunk essays fill and stay top-aligned.
        single = len(chunks) == 1
        for i, chunk in enumerate(chunks):
            folio = self._pos()
            body = "".join(f"<p>{_esc(p)}</p>" for p in chunk)
            if i == 0:
                head = (f'<div class="ghead"><div class="eyebrow">{_esc(eyebrow)}</div>'
                        f'<div class="gh2">{_esc(heading)}</div></div>')
                cls = "page gtext dropcap essaypage"
                fill = ("gbodyfill essay-body fill-center" if single and not pull_quote
                        else "gbodyfill essay-body")
            else:
                head = f'<div class="ghead"><div class="eyebrow">{_esc(eyebrow)}</div></div>'
                cls = "page gtext essaypage continued"
                fill = "gbodyfill essay-body fill-start"
            inner = (f'{head}<div class="{fill}">{body}</div>')
            attr = self._entry_attr(f"essay:{key}") if key else ""
            self.pages.append(
                f'<section class="{cls}"{attr}>{self._chrome(folio)}'
                f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

        # Pull quotes on their own page so a full essay tail never clips the quote
        # off the page bottom (vision QA blocking defect on long essays).
        if pull_quote:
            folio = self._pos()
            head = f'<div class="ghead"><div class="eyebrow">{_esc(eyebrow)}</div></div>'
            inner = (f'{head}<div class="gbodyfill essay-body essay-pull fill-center">'
                     f'<blockquote>{_esc(pull_quote)}</blockquote></div>')
            attr = self._entry_attr(f"essay:{key}") if key else ""
            self.pages.append(
                f'<section class="page gtext essaypage essay-pull"{attr}>'
                f'{self._chrome(folio)}'
                f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

    def feature_page(self, eyebrow: str, boxes: list[tuple[str, str]],
                     pull_quote: str = "", key: str = "",
                     level: int = 0) -> None:
        """1-2 feature boxes (+ optional display quote) as a designed spread —
        sidebars never ship as near-empty solo pages."""
        folio = self._pos()
        quote = (f'<div class="feature-quote">{_esc(pull_quote)}</div>'
                 if pull_quote else "")
        cards = "".join(
            f'<div class="feature-box"><div class="fb-head">{_esc(h)}</div>'
            f'<div class="fb-body">{_esc(b)}</div></div>' for h, b in boxes)
        # A lone sidebar becomes a generous, vertically-centered panel (not a small
        # box marooned in white); two boxes fill and space evenly down the page.
        if len(boxes) <= 1:
            stack_cls = "gbodyfill feature-stack fill-center feature-solo"
        else:
            stack_cls = "gbodyfill feature-stack fill-stack feature-multi"
        inner = (f'<div class="ghead"><div class="eyebrow">{_esc(eyebrow)}</div></div>'
                 f'<div class="{stack_cls}">{quote}{cards}</div>')
        self.pages.append(
            f'<section class="page featurepage"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit"{self._fitmin(level)}>{inner}</div></div></section>')

    def _entry_parts(self, entry) -> dict[str, str]:
        es = self.profile.entry_schema
        # Strip any leaked internal passage ids from every reader-facing string.
        need = "".join(f"<li>{_esc(_strip_passage_ids(x))}</li>"
                       for x in entry.what_you_need)
        steps = "".join(f"<li>{_esc(_strip_passage_ids(s.text))}</li>"
                        for s in entry.steps)
        meta = ""
        if es.show_meta and (entry.serves or entry.time):
            bits = " · ".join(x for x in (entry.serves, entry.time) if x)
            meta = f'<div class="entry-meta">{_esc(bits)}</div>'
        cl = []
        if entry.savings and es.show_savings:
            cl.append(f'<div class="callout savings"><span class="cl-label">You save</span> '
                      f'{_esc(_strip_passage_ids(entry.savings))}</div>')
        if entry.caution:
            cl.append(f'<div class="callout caution"><span class="cl-label">Careful</span> '
                      f'{_esc(_strip_passage_ids(entry.caution))}</div>')
        foot_bits = []
        if entry.variation:
            foot_bits.append(f'<span class="ef-label">Variation</span> '
                             f'{_esc(_strip_passage_ids(entry.variation))}')
        if entry.note:
            foot_bits.append(f'<span class="ef-label">Note</span> '
                             f'{_esc(_strip_passage_ids(entry.note))}')
        if entry.source_line:
            foot_bits.append(f'<span class="ef-src">'
                             f'{_esc(_strip_passage_ids(entry.source_line))}</span>')
        # Entry number is a separate fixed-pt badge — NOT prefixed into the title
        # text (where it inherited the 2em title × per-page fit and ran huge).
        num_badge = (f'<span class="entry-num">{_esc(entry.number)}</span>'
                     if entry.number else "")
        # Big quantified lists (recipes) read as two columns so 7-9 ingredients
        # still fit beside the hero photo instead of running off the page.
        need_cols = " cols2" if len(entry.what_you_need) > 6 else ""
        return {
            "need_card": (f'<div class="entry-need"><div class="need-label">'
                          f'{_esc(es.need_label)}</div>'
                          f'<ul class="need-list{need_cols}">{need}</ul></div>') if need else "",
            "steps_panel": (f'<div class="entry-steps"><div class="steps-label">'
                            f'{_esc(es.steps_label)}</div>'
                            f'<ol class="steps-list">{steps}</ol></div>') if steps else "",
            "meta": meta,
            "callouts": (f'<div class="entry-callouts">{"".join(cl)}</div>'
                         if cl else ""),
            "foot": (f'<div class="entry-foot">{" · ".join(foot_bits)}</div>'
                     if foot_bits else ""),
            "intro": (f'<p class="entry-intro">{_esc(_strip_passage_ids(entry.intro))}</p>'
                      if entry.intro else ""),
            "num": num_badge,
            "title": _strip_passage_ids(entry.title),
        }

    def _entry_title_html(self, p: dict[str, str]) -> str:
        """Title row = fixed-pt number badge + title text (constant size)."""
        return (f'<div class="entry-title">{p["num"]}'
                f'<span class="entry-title-text">{_esc(p["title"])}</span></div>')

    def entry_card(self, section: str, entry, hero_uri: str | None,
                   key: str = "", force_spread: bool = False,
                   level: int = 0) -> None:
        """Entry pages. Default to a single card per page; only spill to a
        two-page spread when the entry genuinely won't fit one page (a long,
        many-step, many-ingredient recipe). Short entries — even in a dense
        profile — no longer split needlessly (the "every card is 2 pages" defect).

        `key` is stamped on every emitted page as data-entry so the render
        audit can name the entry that overflowed; `level` is the audit-driven
        escalation (the char-count heuristic is only the first guess):
          0  heuristic single/spread
          1  two-page spread
          2  flowed: presentation page + blocks packed over N working pages
          3+ flowed with a denser per-page budget"""
        if force_spread:
            level = max(level, 1)
        p = self._entry_parts(entry)
        if level >= 2:
            self._entry_flow(section, entry, p, hero_uri, key, level=level)
        elif level >= 1 or self._entry_needs_spread(entry):
            self._entry_spread(section, p, hero_uri, key)
        else:
            self._entry_single(section, p, hero_uri, key)

    def _entry_needs_spread(self, entry) -> bool:
        """Heuristic (no browser available at build time): estimate the vertical
        lines the entry's body would need and only request the 2-page spread when
        it clearly overflows one page. Dense profiles (recipes) get a slightly
        lower bar since their pages also carry a photo + quantified list."""
        step_lines = sum(max(1, math.ceil(len(s.text) / 55)) for s in entry.steps)
        need_lines = len(entry.what_you_need)
        intro_lines = math.ceil(len(entry.intro or "") / 65)
        total = step_lines + need_lines + intro_lines
        limit = 22 if self.profile.pages_per_entry >= 1.5 else 30
        return total > limit or len(entry.steps) > 9

    @staticmethod
    def _entry_attr(key: str) -> str:
        return f' data-entry="{_esc(key)}"' if key else ""

    def _entry_single(self, section: str, p: dict[str, str],
                      hero_uri: str | None, key: str = "") -> None:
        folio = self._pos()
        hero = (f'<figure class="entry-hero grow"><img src="{hero_uri}"/></figure>'
                if hero_uri else "")
        mid = (f'<div class="entry-mid">{hero}{p["need_card"]}</div>'
               if (hero or p["need_card"]) else "")
        inner = (f'<div class="entry-fit">'
                 f'<div class="eyebrow">{_esc(section)}</div>'
                 f'{self._entry_title_html(p)}'
                 f'{p["meta"]}{p["intro"]}{mid}{p["steps_panel"]}'
                 f'{p["callouts"]}{p["foot"]}</div>')
        self.pages.append(
            f'<section class="page"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit" data-fitmin="14">'
            f'{inner}</div></div></section>')

    def _entry_spread(self, section: str, p: dict[str, str],
                      hero_uri: str | None, key: str = "") -> None:
        # Page A — the presentation page: title, meta, headnote, photo, list.
        folio = self._pos()
        hero = (f'<figure class="entry-hero spread-hero grow">'
                f'<img src="{hero_uri}"/></figure>' if hero_uri else "")
        mid = (f'<div class="entry-mid">{hero}{p["need_card"]}</div>'
               if (hero or p["need_card"]) else "")
        inner_a = (f'<div class="entry-fit">'
                   f'<div class="eyebrow">{_esc(section)}</div>'
                   f'{self._entry_title_html(p)}'
                   f'{p["meta"]}{p["intro"]}{mid}</div>')
        self.pages.append(
            f'<section class="page"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit" data-fitmin="14">'
            f'{inner_a}</div></div></section>')
        # Page B — the working page: method, callouts, notes. No repeated full
        # title; just a small "(continued)" tag so the eye isn't hit twice.
        folio = self._pos()
        inner_b = (f'<div class="entry-fit entry-continued">'
                   f'<div class="eyebrow">{_esc(section)}</div>'
                   f'{p["steps_panel"]}{p["callouts"]}{p["foot"]}</div>')
        self.pages.append(
            f'<section class="page"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit" data-fitmin="14">'
            f'{inner_b}</div></div></section>')

    # Rough per-block line estimates for the flowed layout (page capacity at the
    # 13pt fit floor is ~26 body lines; the audit re-measures, so these only
    # need to be conservative, not exact).
    _FLOW_LINES_PER_PAGE = 22

    def _entry_flow(self, section: str, entry, p: dict[str, str],
                    hero_uri: str | None, key: str = "",
                    level: int = 2) -> None:
        """Level-2+ repair: presentation page + content blocks PACKED across as
        many working pages as they need. Steps stay atomic and keep continuous
        numbering (<ol start=N>); need-list, callouts, and foot are atomic
        blocks. Any entry fits at some N, so a too-long entry can no longer
        defeat the layout — only densify it.

        Level 3+ also sheds the intro from the presentation page into the flow:
        page A's fixed load (title + meta + intro + hero) is otherwise immune
        to escalation, and a long intro beside a tall hero can overflow page A
        no matter how the continuation pages are packed."""
        es = self.profile.entry_schema
        factor = 0.8 ** max(0, level - 2)
        shed_intro = level >= 3
        flow_fitmin = ' data-fitmin="9"' if level >= 4 else ' data-fitmin="14"'
        folio = self._pos()
        hero = (f'<figure class="entry-hero spread-hero grow">'
                f'<img src="{hero_uri}"/></figure>' if hero_uri else "")
        mid = f'<div class="entry-mid">{hero}</div>' if hero else ""
        intro_a = "" if shed_intro else p["intro"]
        inner_a = (f'<div class="entry-fit">'
                   f'<div class="eyebrow">{_esc(section)}</div>'
                   f'{self._entry_title_html(p)}'
                   f'{p["meta"]}{intro_a}{mid}</div>')
        self.pages.append(
            f'<section class="page"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit"{flow_fitmin}>'
            f'{inner_a}</div></div></section>')

        # ---- atomic blocks with conservative line estimates ---- #
        est_item = lambda t, w: max(1, math.ceil(len(t) / w))
        blocks: list[tuple[str, int, object]] = []
        if shed_intro and p["intro"]:
            blocks.append(("intro", est_item(entry.intro or "", 60) + 1, p["intro"]))
        if p["need_card"]:
            lines = 2 + sum(est_item(x, 38) for x in entry.what_you_need)
            if len(entry.what_you_need) > 6:
                lines = 2 + math.ceil((lines - 2) / 2)  # 2-col list
            blocks.append(("need", lines, p["need_card"]))
        for i, s in enumerate(entry.steps):
            blocks.append(("step", est_item(s.text, 48) + 1, (i, s.text)))
        if p["callouts"]:
            blocks.append(("callouts", 3, p["callouts"]))
        if p["foot"]:
            blocks.append(("foot", 3, p["foot"]))

        budget = max(8, int(self._FLOW_LINES_PER_PAGE * factor))
        pages: list[list[tuple[str, int, object]]] = [[]]
        used = 0
        for blk in blocks:
            if pages[-1] and used + blk[1] > budget:
                pages.append([])
                used = 0
            pages[-1].append(blk)
            used += blk[1]

        for chunk in pages:
            folio = self._pos()
            body = ""
            step_items: list[tuple[int, str]] = []

            def flush_steps() -> None:
                nonlocal body
                if not step_items:
                    return
                start = step_items[0][0]
                lis = "".join(f"<li>{_esc(_strip_passage_ids(t))}</li>"
                              for _, t in step_items)
                counter_style = f' style="counter-reset: s {start}"' if start else ""
                label = _esc(es.steps_label)
                body += (f'<div class="entry-steps"><div class="steps-label">'
                         f'{label}</div><ol class="steps-list"{counter_style}>'
                         f'{lis}</ol></div>')
                step_items.clear()

            for kind, _, payload in chunk:
                if kind == "step":
                    step_items.append(payload)  # type: ignore[arg-type]
                else:
                    flush_steps()
                    body += payload  # type: ignore[operator]
            flush_steps()
            inner = (f'<div class="entry-fit entry-continued">'
                     f'<div class="eyebrow">{_esc(section)}</div>{body}</div>')
            self.pages.append(
                f'<section class="page"{self._entry_attr(key)}>{self._chrome(folio)}'
                f'<div class="gbody-box"><div class="gfit"{flow_fitmin}>'
                f'{inner}</div></div></section>')

    def _list_item(self, e) -> str:
        tx = e.intro or (e.steps[0].text if e.steps else "")
        return (f'<div class="list-item"><div class="ln">'
                f'<span class="num">{e.number}</span>'
                f'<span><span class="nm">{_esc(e.title)}.</span> '
                f'<span class="tx">{_esc(tx)}</span></span></div></div>')

    def list_page(self, section: str, heading: str, lead: str, entries,
                  key: str = "", level: int = 0) -> None:
        folio = self._pos()
        lead_html = f'<div class="glead">{_esc(lead)}</div>' if lead else ""
        # A short list is rendered as one vertically-centered, compact column so a
        # 2-item chunk never prints as two lines above 80% white space; a full list
        # keeps the two-column fill layout.
        if len(entries) <= 4:
            items = "".join(self._list_item(e) for e in entries)
            body = (f'<div class="gbodyfill fill-center list-compact">'
                    f'<div class="list-col">{items}</div></div>')
        else:
            half = (len(entries) + 1) // 2
            left = "".join(self._list_item(e) for e in entries[:half])
            right = "".join(self._list_item(e) for e in entries[half:])
            body = (f'<div class="gbodyfill fill-row">'
                    f'<div class="list-col">{left}</div>'
                    f'<div class="list-col">{right}</div></div>')
        inner = (f'<div class="ghead"><div class="eyebrow">{_esc(section)}</div>'
                 f'<div class="gh2">{_esc(heading)}</div>{lead_html}</div>{body}')
        self.pages.append(
            f'<section class="page listpage"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit"{self._fitmin(level)}>{inner}</div></div></section>')

    def chart_page(self, section: str, heading: str, lead: str, table,
                   key: str = "", level: int = 0) -> None:
        """Reference chart. Repair ladder: level 1 = lower fit floor only (a
        near-miss); level >= 2 = SPLIT the rows across `level` pages, each
        repeating the header, balanced so no near-empty trailing page (the
        structural reflow a big table needs — a 50-row seasons chart can never
        fit one page at any readable size); level >= 4 adds the rescue floor."""
        def row_html(row) -> str:
            tds = ""
            for c in row:
                cell = _esc(c)
                # A blank cell in a tracker grid renders as a real tickable
                # checkbox square (so a "tick each square" page shows a grid).
                if cell.strip():
                    tds += f"<td>{cell}</td>"
                else:
                    tds += '<td class="tick"><span class="check-box"></span></td>'
            return f"<tr>{tds}</tr>"

        ths = "".join(f"<th>{_esc(c)}</th>" for c in table.columns)
        npages = max(1, min(int(level), 6)) if level >= 2 else 1
        size = math.ceil(len(table.rows) / npages)
        chunks = [table.rows[i:i + size] for i in range(0, len(table.rows), size)] or [[]]
        fitmin = self._fitmin(1 if 2 <= level < 4 else level)
        for ci, chunk in enumerate(chunks):
            folio = self._pos()
            trs = "".join(row_html(r) for r in chunk)
            head = _esc(heading) if len(chunks) == 1 else \
                f"{_esc(heading)} ({ci + 1} of {len(chunks)})"
            note = (f'<div class="chart-note">{_esc(table.note)}</div>'
                    if table.note and ci == len(chunks) - 1 else "")
            lead_html = (f'<div class="glead">{_esc(lead)}</div>'
                         if lead and ci == 0 else "")
            fill = "fill-start"
            inner = (f'<div class="ghead"><div class="eyebrow">{_esc(section)}</div>'
                     f'<div class="gh2">{head}</div>{lead_html}</div>'
                     f'<div class="gbodyfill {fill}"><table class="gtable">'
                     f'<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>{note}</div>')
            self.pages.append(
                f'<section class="page chartpage"{self._entry_attr(key)}>{self._chrome(folio)}'
                f'<div class="gbody-box"><div class="gfit"{fitmin}>{inner}</div></div></section>')

    def checklist_page(self, section: str, heading: str, lead: str, checklist,
                       key: str = "", level: int = 0) -> None:
        folio = self._pos()
        groups: dict[str, list] = {}
        order: list[str] = []
        for it in checklist.items:
            key = it.cadence or ""
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(it)
        cols = ""
        total_items = len(checklist.items)
        row_fill = "fill-stack"
        for grp_key in order:
            head = f'<div class="ct">{_esc(grp_key.title())}</div>' if grp_key else ""
            rows = "".join(
                f'<div class="check-row"><span class="check-box"></span>'
                f'<span>{_esc(it.text)}</span></div>' for it in groups[grp_key])
            cols += f'<div class="check-grp">{head}<div class="check-rows {row_fill}">{rows}</div></div>'
        lead_html = f'<div class="glead">{_esc(lead)}</div>' if lead else ""
        compact = " checkpage-compact" if total_items < 8 else ""
        inner = (f'<div class="ghead"><div class="eyebrow">{_esc(section)}</div>'
                 f'<div class="gh2">{_esc(heading)}</div>{lead_html}</div>'
                 f'<div class="gbodyfill fill-row">{cols}</div>')
        self.pages.append(
            f'<section class="page checkpage{compact}"{self._entry_attr(key)}>{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit"{self._fitmin(level)}>{inner}</div></div></section>')

    def pro_page(self, heading: str, lead: str, signs: list[str], reassure: str,
                 level: int = 0) -> None:
        folio = self._pos()
        lis = "".join(f"<li>{_esc(s)}</li>" for s in signs)
        compact = " propage-compact" if len(signs) < 6 else ""
        body_fill = "fill-stack" if len(signs) < 6 else "fill-spread"
        inner = (f'<div class="ghead"><div class="gh2">{_esc(heading)}</div>'
                 f'<div class="plead">{_esc(lead)}</div></div>'
                 f'<div class="gbodyfill {body_fill}"><ul>{lis}</ul>'
                 f'<div class="reassure">{_esc(reassure)}</div></div>')
        self.pages.append(
            f'<section class="page propage{compact}" data-entry="pro">{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit"{self._fitmin(level)}>{inner}</div></div></section>')

    def render(self) -> str:
        tokens = theme.pick(
            self.bible.title + " " + self.bible.subtitle,
            name=self.bible.theme_name or None,
            design=getattr(self.bible, "design", None),
        )
        root = theme.css_root(
            tokens,
            settings.trim_width_in + 2 * settings.bleed_in,
            settings.trim_height_in + 2 * settings.bleed_in,
            settings.bleed_in,
        )
        log.info("guide_layout_built", pages=len(self.pages))
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<title>{self.title}</title><style>{root}{_GUIDE_CSS}</style>'
                f'</head><body>{"".join(self.pages)}</body></html>')


# Neutral front-matter fallbacks (overridable). The real pipeline supplies
# these from the bible; nothing here may carry a previous book's domain.
_DEFAULT_HOWTO_POINTS = [
    "Every entry is laid out the same way, so you always know where to look.",
    "Read the Safety First page before you begin.",
    "Start with the chapter that matches what you want to do today.",
    "Use the table of contents to jump straight to any entry.",
]


def _divider_hero(img_map: dict, ch_index: int) -> str | None:
    """Divider image lookup: new refs are '{i}.divider'; legacy manifests used
    '{i}.0' (safe there — block 0 was always the imageless section intro)."""
    return img_map.get(f"{ch_index}.divider") or img_map.get(f"{ch_index}.0")


def build_practical_html(
    bible: BookBible,
    chapters: list[Chapter],
    images: list[GeneratedImage],
    *,
    cover_key: str | None = None,
    how_to: dict | None = None,
    safety: dict | None = None,
    pro: dict | None = None,
    repairs: set[str] | dict[str, int] | None = None,
) -> str:
    """Assemble a full practical-guide HTML document with a real TOC.

    `repairs` maps keys the render audit MEASURED as overflowing a page to an
    escalation level (a plain set means level 1 everywhere). Keys: entry
    "{chapter_index}.{block_index}" (1 = spread, 2+ = flowed over N pages),
    "essay:{chapter_index}" (tighter word budgets), "howto" (compact, then
    split), "feature:/list:/chart:/check:" + "pro" (lower fit floors). See
    assemble._render_practical for the repair loop."""
    if isinstance(repairs, (set, frozenset, list, tuple)):
        repairs = dict.fromkeys(repairs, 1)
    lvl = (repairs or {}).get

    profile = profile_of(bible)
    img_map = {gi.block_ref: _uri(gi.storage_key) for gi in images}
    section_plans = {s.index: s for s in bible.sections}
    d = GuideDirector(bible)

    cover_uri = _uri(cover_key) if cover_key else next(iter(img_map.values()), None)
    d.cover(cover_uri, bible.title, bible.subtitle,
            kicker=profile.cover_kicker or profile.genre.title(),
            foot=profile.cover_foot or bible.tone)
    d.title_page(bible.title, bible.subtitle, imprint="A BookForge Practical Guide")

    ht = how_to or {}
    d.how_to_use(ht.get("heading", "How to Use This Book"),
                 ht.get("lead", "This book is built to be easy on the eyes and easy to "
                        "follow. Everything is in large print, one idea at a time."),
                 ht.get("points", _DEFAULT_HOWTO_POINTS),
                 level=lvl("howto", 0))

    sf = safety or {}
    never_mix = sf.get("never_mix") or safety_mod.cards_for_bible(bible)
    if never_mix:
        d.safety_page(
            sf.get("lead", "The steps in this book are safe when you follow them "
                   "exactly. Please read these warnings before you begin."),
            never_mix)

    # --- TOC slots: sized BEFORE sections so folios are correct --- #
    part_groups: dict[str, list] = {}
    part_order: list[str] = []
    for ch in chapters:
        plan = section_plans.get(ch.index)
        part = (plan.part_title if plan and plan.part_title else "Chapters")
        if part not in part_groups:
            part_groups[part] = []
            part_order.append(part)
        part_groups[part].append(ch)

    ref_count = sum(
        1 for ch in chapters for b in ch.blocks
        if (b.type == BlockType.chart and b.table)
        or (b.type == BlockType.checklist and b.checklist)
    ) + (1 if pro else 0)
    estimate_groups = ([(p, part_groups[p]) for p in part_order]
                       + ([("Reference", list(range(ref_count)))] if ref_count else []))
    toc_slot = len(d.pages)
    toc_slots = GuideDirector.toc_page_count(estimate_groups)
    for _ in range(toc_slots):
        d.pages.append("")  # reserved

    section_rows: dict[str, list[tuple[str, int]]] = {p: [] for p in part_order}
    ref_rows: list[tuple[str, int]] = []

    ci = 0
    for ch in chapters:
        ci += 1
        plan = section_plans.get(ch.index)
        tight_budget = bool(plan and plan.page_budget <= 2)
        kicker = plan.summary if plan and plan.summary else ""
        history_block = None
        pull_quote = ""
        for b in ch.blocks:
            if b.type == BlockType.section_intro and b.text:
                kicker = b.text
            elif b.type == BlockType.history and b.text and history_block is None:
                history_block = b
            elif b.type == BlockType.pull_quote and b.text and not pull_quote:
                pull_quote = b.text
        part = (plan.part_title if plan and plan.part_title else "Chapters")
        section_rows.setdefault(part, []).append((ch.title, len(d.pages) + 1))
        d.divider(ci, divider_title(ch.title), kicker,
                  hero_uri=_divider_hero(img_map, ch.index),
                  part_title=part if part != "Chapters" else "")

        eyebrow = section_eyebrow(ch.title)
        pending_sidebars: list[tuple[str, str]] = []
        quote_used = False
        if history_block is not None:
            has_sidebars = any(b.type == BlockType.sidebar and b.text
                               for b in ch.blocks)
            essay_eyebrow = part if part != "Chapters" else profile.genre.title()
            d.essay_page(essay_eyebrow, ch.title, history_block.text,
                         pull_quote="" if has_sidebars or tight_budget else pull_quote,
                         key=str(ch.index),
                         squeeze=lvl(f"essay:{ch.index}", 0))
            quote_used = not has_sidebars and not tight_budget

        for bi, b in enumerate(ch.blocks):
            ref = f"{ch.index}.{bi}"
            hero = img_map.get(ref)
            if b.type == BlockType.use_card and b.entries:
                d.entry_card(eyebrow, b.entries[0], hero, key=ref,
                             level=lvl(ref, 0))
                if pending_sidebars:
                    d.feature_page(eyebrow, pending_sidebars,
                                   pull_quote="" if quote_used else pull_quote,
                                   key=f"feature:{ref}",
                                   level=lvl(f"feature:{ref}", 0))
                    quote_used = True
                    pending_sidebars = []
            elif b.type == BlockType.sidebar and b.text:
                pending_sidebars.append((b.heading or "In Focus", b.text))
            elif b.type == BlockType.use_list and b.entries and len(b.entries) >= 4:
                # A quick-list under 4 items makes a near-empty page and just
                # echoes the cards — skip it (planning also zeroes small lists).
                if pending_sidebars:
                    d.feature_page(eyebrow, pending_sidebars,
                                   pull_quote="" if quote_used else pull_quote,
                                   key=f"feature:{ref}",
                                   level=lvl(f"feature:{ref}", 0))
                    quote_used = True
                    pending_sidebars = []
                list_level = lvl(f"list:{ref}", 0)
                # Repaired lists re-chunk with fewer items per page before the
                # fit floor concedes anything.
                per = {0: 14, 1: 11}.get(list_level, 9)
                # Balance across pages so a trailing chunk is never a near-empty
                # 1-2 item page (e.g. 15 items -> 8+7, not 14+1).
                n = len(b.entries)
                npages = max(1, math.ceil(n / per))
                size = math.ceil(n / npages)
                chunks = [b.entries[i:i + size] for i in range(0, n, size)]
                base = b.heading or f"More {eyebrow}"
                for ci2, chunk in enumerate(chunks):
                    head = base if len(chunks) == 1 else f"{base} ({ci2 + 1} of {len(chunks)})"
                    d.list_page(eyebrow, head, b.text if ci2 == 0 else "", chunk,
                                key=f"list:{ref}", level=list_level)
            elif b.type == BlockType.chart and b.table:
                ref_rows.append((b.heading or "Chart", len(d.pages) + 1))
                d.chart_page(eyebrow, b.heading or "Reference Chart", b.text, b.table,
                             key=f"chart:{ref}", level=lvl(f"chart:{ref}", 0))
            elif b.type == BlockType.checklist and b.checklist:
                ref_rows.append((b.heading or "Checklist", len(d.pages) + 1))
                d.checklist_page(eyebrow, b.heading or "Checklist", b.text, b.checklist,
                                 key=f"check:{ref}", level=lvl(f"check:{ref}", 0))
        if pending_sidebars:
            d.feature_page(eyebrow, pending_sidebars,
                           pull_quote="" if quote_used else pull_quote,
                           key=f"feature:{ch.index}.end",
                           level=lvl(f"feature:{ch.index}.end", 0))

    if pro:
        heading = pro.get("heading") or profile.back_matter_heading
        ref_rows.append((heading, len(d.pages) + 1))
        d.pro_page(heading, pro.get("lead", ""), pro.get("signs", []),
                   pro.get("reassure", ""), level=lvl("pro", 0))

    groups: list[tuple[str, list[tuple[str, int]]]] = []
    if len(part_order) == 1:
        groups.append(("Chapters", section_rows[part_order[0]]))
    else:
        groups.extend((p, section_rows[p]) for p in part_order if section_rows[p])
    if ref_rows:
        groups.append(("Reference", ref_rows))
    for i, page_html in enumerate(d.toc_pages(groups, toc_slots)):
        d.pages[toc_slot + i] = page_html

    return d.render()
