"""Practical-guide layout director — senior-first, full-page-fill templates.

Emits a self-contained HTML document styled by guide.css (NOT the legacy
cookbook book.css), so the recipe path is untouched. Page order:

    cover -> title -> how-to-use -> safety -> TOC -> [per section: divider,
    entry cards, compact list pages, charts, checklists] -> when-to-call-a-pro

Folio numbers equal absolute page position, so the TOC (rendered into a
reserved slot) cites correct start pages for each section.
"""
from __future__ import annotations

import html
from pathlib import Path

import structlog

from bookforge.config import settings
from bookforge.layout import theme
from bookforge.layout.build import _uri  # data-URI from a storage key
from bookforge.layout.labels import divider_title, section_eyebrow
from bookforge.schemas import BlockType, BookBible, Chapter, GeneratedImage

log = structlog.get_logger(__name__)

_GUIDE_CSS = (Path(__file__).parent / "guide.css").read_text()


def _esc(t) -> str:
    return html.escape(str(t)) if t else ""


class GuideDirector:
    def __init__(self, bible: BookBible):
        self.bible = bible
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

    # ---- front matter ---- #
    def cover(self, uri: str | None, title: str, sub: str, kicker: str, foot: str) -> None:
        img = f'<img src="{uri}"/>' if uri else ""
        self.pages.append(
            f'<section class="page gcover">'
            f'<div class="cphoto">{img}</div>'
            f'<div class="cband"><div class="ckicker">{_esc(kicker)}</div>'
            f'<div class="cmid"><div class="ctitle">{_esc(title)}</div>'
            f'<div class="csub">{_esc(sub)}</div></div>'
            f'<div class="cfoot">{_esc(foot)}</div></div></section>')

    def title_page(self, title: str, sub: str, imprint: str) -> None:
        self.pages.append(
            f'<section class="page gtitle"><div class="wrap">'
            f'<div class="t">{_esc(title)}</div><div class="rule"></div>'
            f'<div class="s">{_esc(sub)}</div>'
            f'<div class="imp">{_esc(imprint)}</div></div></section>')

    def how_to_use(self, heading: str, lead: str, points: list[str]) -> None:
        lis = "".join(f"<li>{_esc(p)}</li>" for p in points)
        inner = (f'<div class="ghead"><div class="kick">Welcome</div>'
                 f'<div class="gh2">{_esc(heading)}</div>'
                 f'<div class="glead">{_esc(lead)}</div></div>'
                 f'<ol class="gbodyfill fill-spread">{lis}</ol>')
        self.pages.append(
            f'<section class="page howto">{self._chrome(self._pos())}'
            f'<div class="gbody-box"><div class="gfit">{inner}</div>'
            f'</div></section>')

    def safety_page(self, lead: str, never_mix: list[tuple[str, str]]) -> None:
        cards = "".join(
            f'<div class="nm"><div class="pair">{_esc(a)}</div>'
            f'<div class="why">{_esc(b)}</div></div>' for a, b in never_mix)
        self.pages.append(
            f'<section class="page gsafety">'
            f'<div class="band"><div class="bt"><div class="bk">Please read first</div>'
            f'<h2>Safety First</h2></div></div>'
            f'<div class="body"><p class="lead">{_esc(lead)}</p>'
            f'<div class="nevermix-label">Never mix these</div>'
            f'<div class="nm-grid">{cards}</div></div></section>')

    def toc(self, groups: list[tuple[str, list[tuple[str, int]]]]) -> str:
        """Return TOC HTML (rendered into a reserved slot). groups = [(header,
        [(label, page), ...]), ...]."""
        blocks = ""
        for header, rows in groups:
            rws = "".join(
                f'<div class="row"><span class="tt">{_esc(lbl)}</span>'
                f'<span class="dots"></span><span class="pg">{pg}</span></div>'
                for lbl, pg in rows)
            head = f'<div class="gt">{_esc(header)}</div>' if header else ""
            blocks += f'<div class="grp">{head}{rws}</div>'
        return (f'<section class="page toc">'
                f'<div class="head"><div class="kick">The table of</div>'
                f'<h2>Contents</h2><div class="rule"></div></div>'
                f'<div class="cols">{blocks}</div></section>')

    # ---- section pages ---- #
    def divider(self, number: int, title: str, kicker: str,
                hero_uri: str | None = None) -> None:
        img = f'<div class="dphoto-inner"><img src="{hero_uri}"/></div>' if hero_uri else ""
        cls = "page gdivider has-img" if hero_uri else "page gdivider"
        self.pages.append(
            f'<section class="{cls}"><div class="dphoto">{img}</div>'
            f'<div class="dpanel"><div class="dpanel-inner">'
            f'<div class="num">Section {number}</div>'
            f'<div class="t">{_esc(title)}</div><div class="rule"></div>'
            f'<div class="k">{_esc(kicker)}</div></div></div></section>')

    def entry_card(self, section: str, entry, hero_uri: str | None) -> None:
        folio = self._pos()
        need = "".join(f"<li>{_esc(x)}</li>" for x in entry.what_you_need)
        steps = "".join(f"<li>{_esc(s.text)}</li>" for s in entry.steps)
        hero = (f'<figure class="entry-hero grow"><img src="{hero_uri}"/></figure>'
                if hero_uri else "")
        need_card = (f'<div class="entry-need"><div class="need-label">What You Need</div>'
                     f'<ul class="need-list">{need}</ul></div>') if need else ""
        mid = f'<div class="entry-mid">{hero}{need_card}</div>' if (hero or need_card) else ""
        cl = []
        if entry.savings:
            cl.append(f'<div class="callout savings"><span class="cl-label">You save</span> '
                      f'{_esc(entry.savings)}</div>')
        if entry.caution:
            cl.append(f'<div class="callout caution"><span class="cl-label">Careful</span> '
                      f'{_esc(entry.caution)}</div>')
        callouts = f'<div class="entry-callouts">{"".join(cl)}</div>' if cl else ""
        intro = f'<p class="entry-intro">{_esc(entry.intro)}</p>' if entry.intro else ""
        num = f"{entry.number}. " if entry.number else ""
        steps_panel = (f'<div class="entry-steps"><div class="steps-label">Steps</div>'
                       f'<ol class="steps-list">{steps}</ol></div>') if steps else ""
        inner = (f'<div class="entry-fit">'
                 f'<div class="eyebrow">{_esc(section)}</div>'
                 f'<div class="entry-title">{_esc(num + entry.title)}</div>'
                 f'{intro}{mid}{steps_panel}{callouts}</div>')
        self.pages.append(
            f'<section class="page">{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit" data-fitmin="13">'
            f'{inner}</div></div></section>')

    def _list_item(self, e) -> str:
        tx = e.intro or (e.steps[0].text if e.steps else "")
        return (f'<div class="list-item"><div class="ln">'
                f'<span class="num">{e.number}</span>'
                f'<span><span class="nm">{_esc(e.title)}.</span> '
                f'<span class="tx">{_esc(tx)}</span></span></div></div>')

    def list_page(self, section: str, heading: str, lead: str, entries) -> None:
        folio = self._pos()
        half = (len(entries) + 1) // 2
        left = "".join(self._list_item(e) for e in entries[:half])
        right = "".join(self._list_item(e) for e in entries[half:])
        lead_html = f'<div class="glead">{_esc(lead)}</div>' if lead else ""
        inner = (f'<div class="ghead"><div class="eyebrow">{_esc(section)}</div>'
                 f'<div class="gh2">{_esc(heading)}</div>{lead_html}</div>'
                 f'<div class="gbodyfill fill-row">'
                 f'<div class="fill-spread">{left}</div><div class="fill-spread">{right}</div></div>')
        self.pages.append(
            f'<section class="page listpage">{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

    def chart_page(self, section: str, heading: str, lead: str, table) -> None:
        folio = self._pos()
        ths = "".join(f"<th>{_esc(c)}</th>" for c in table.columns)
        trs = ""
        for row in table.rows:
            tds = "".join(f"<td>{_esc(c)}</td>" for c in row)
            trs += f"<tr>{tds}</tr>"
        note = f'<div class="chart-note">{_esc(table.note)}</div>' if table.note else ""
        lead_html = f'<div class="glead">{_esc(lead)}</div>' if lead else ""
        inner = (f'<div class="ghead"><div class="eyebrow">{_esc(section)}</div>'
                 f'<div class="gh2">{_esc(heading)}</div>{lead_html}</div>'
                 f'<div class="gbodyfill fill-center"><table class="gtable">'
                 f'<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>{note}</div>')
        self.pages.append(
            f'<section class="page chartpage">{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

    def checklist_page(self, section: str, heading: str, lead: str, checklist) -> None:
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
        for key in order:
            head = f'<div class="ct">{_esc(key.title())}</div>' if key else ""
            rows = "".join(
                f'<div class="check-row"><span class="check-box"></span>'
                f'<span>{_esc(it.text)}</span></div>' for it in groups[key])
            cols += f'<div class="check-grp">{head}<div class="check-rows fill-spread">{rows}</div></div>'
        lead_html = f'<div class="glead">{_esc(lead)}</div>' if lead else ""
        inner = (f'<div class="ghead"><div class="eyebrow">{_esc(section)}</div>'
                 f'<div class="gh2">{_esc(heading)}</div>{lead_html}</div>'
                 f'<div class="gbodyfill fill-row">{cols}</div>')
        self.pages.append(
            f'<section class="page checkpage">{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

    def pro_page(self, heading: str, lead: str, signs: list[str], reassure: str) -> None:
        folio = self._pos()
        lis = "".join(f"<li>{_esc(s)}</li>" for s in signs)
        inner = (f'<div class="ghead"><div class="gh2">{_esc(heading)}</div>'
                 f'<div class="plead">{_esc(lead)}</div></div>'
                 f'<div class="gbodyfill fill-spread"><ul>{lis}</ul>'
                 f'<div class="reassure">{_esc(reassure)}</div></div>')
        self.pages.append(
            f'<section class="page propage">{self._chrome(folio)}'
            f'<div class="gbody-box"><div class="gfit">{inner}</div></div></section>')

    def render(self) -> str:
        tokens = theme.pick(
            self.bible.title + " " + self.bible.subtitle,
            name=self.bible.theme_name or None,
        )
        root = theme.css_root(
            tokens,
            settings.trim_width_in + 2 * settings.bleed_in,
            settings.trim_height_in + 2 * settings.bleed_in,
            settings.bleed_in,
        )
        log.info("guide_layout_built", pages=len(self.pages))
        return (f'<!doctype html><html><head><meta charset="utf-8">'
                f'<title>{self.title}</title><style>{root}{_GUIDE_CSS}</style>'
                f'</head><body>{"".join(self.pages)}</body></html>')


# Default front/back matter (overridable). The real pipeline supplies these from
# the safety canon + content stage; sensible defaults keep layout self-contained.
_DEFAULT_HOWTO_POINTS = [
    "Each tip is laid out the same way: what you need, the steps, and what you save.",
    "Read the Safety First page before you try anything with a chemical.",
    "Start with the section that matches the room or problem you have today.",
    "You do not need special tools. Most tips use what is already in your home.",
]


def build_practical_html(
    bible: BookBible,
    chapters: list[Chapter],
    images: list[GeneratedImage],
    *,
    cover_key: str | None = None,
    how_to: dict | None = None,
    safety: dict | None = None,
    pro: dict | None = None,
) -> str:
    """Assemble a full practical-guide HTML document with a real TOC."""
    img_map = {gi.block_ref: _uri(gi.storage_key) for gi in images}
    section_plans = {s.index: s for s in bible.sections}
    d = GuideDirector(bible)

    cover_uri = _uri(cover_key) if cover_key else next(iter(img_map.values()), None)
    d.cover(cover_uri, bible.title, bible.subtitle,
            kicker="A Senior's Money-Saving Guide",
            foot="Simple steps · Big savings · Plain English")
    d.title_page(bible.title, bible.subtitle, imprint="A BookForge Practical Guide")

    ht = how_to or {}
    d.how_to_use(ht.get("heading", "How to Use This Book"),
                 ht.get("lead", "This book is built to be easy on the eyes and easy to "
                        "follow. Everything is in large print, one idea at a time."),
                 ht.get("points", _DEFAULT_HOWTO_POINTS))

    sf = safety or {}
    d.safety_page(
        sf.get("lead", "These tips are safe when you follow them exactly. A few common "
               "household mixes are dangerous — please never combine the pairs below."),
        sf.get("never_mix", [
            ("Peroxide + Vinegar", "In a closed bottle this makes an irritating acid. Use one, rinse, then the other."),
            ("Peroxide + Bleach", "Releases harmful fumes. Keep them far apart."),
            ("Bleach + Ammonia", "Makes a toxic gas. Never mix any cleaners blindly."),
            ("Peroxide + Your eyes/mouth", "Never swallow it or use it as a mouthwash."),
        ]))

    toc_slot = len(d.pages)
    d.pages.append("")  # reserve one TOC page

    section_rows: list[tuple[str, int]] = []
    ref_rows: list[tuple[str, int]] = []

    for ci, ch in enumerate(chapters, start=1):
        plan = section_plans.get(ch.index)
        # section intro (if any) becomes the divider kicker
        kicker = plan.summary if plan and plan.summary else ""
        for b in ch.blocks:
            if b.type == BlockType.section_intro and b.text:
                kicker = b.text
                break
        section_rows.append((ch.title, len(d.pages) + 1))
        d.divider(ci, divider_title(ch.title), kicker, hero_uri=img_map.get(f"{ch.index}.0"))

        eyebrow = section_eyebrow(ch.title)
        for bi, b in enumerate(ch.blocks):
            ref = f"{ch.index}.{bi}"
            hero = img_map.get(ref)
            if b.type == BlockType.use_card and b.entries:
                d.entry_card(eyebrow, b.entries[0], hero)
            elif b.type == BlockType.use_list and b.entries:
                per = 14  # items per list page — keeps big text, full but not crammed
                chunks = [b.entries[i:i + per] for i in range(0, len(b.entries), per)]
                base = b.heading or f"More {eyebrow} Uses"
                for ci2, chunk in enumerate(chunks):
                    head = base if len(chunks) == 1 else f"{base} ({ci2 + 1} of {len(chunks)})"
                    d.list_page(eyebrow, head, b.text if ci2 == 0 else "", chunk)
            elif b.type == BlockType.chart and b.table:
                ref_rows.append((b.heading or "Chart", len(d.pages) + 1))
                d.chart_page(eyebrow, b.heading or "Reference Chart", b.text, b.table)
            elif b.type == BlockType.checklist and b.checklist:
                ref_rows.append((b.heading or "Checklist", len(d.pages) + 1))
                d.checklist_page(eyebrow, b.heading or "Checklist", b.text, b.checklist)

    if pro:
        ref_rows.append((pro.get("heading", "When to Call a Professional"), len(d.pages) + 1))
        d.pro_page(pro.get("heading", "When to Call a Professional"),
                   pro.get("lead", ""), pro.get("signs", []), pro.get("reassure", ""))

    groups = [("Sections", section_rows)]
    if ref_rows:
        groups.append(("Charts & Checklists", ref_rows))
    d.pages[toc_slot] = d.toc(groups)

    return d.render()
