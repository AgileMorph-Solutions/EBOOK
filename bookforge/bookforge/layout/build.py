"""Stage 4 — layout director + themed, fixed designed pages.

Maps content to a small library of polished page templates with rhythm/variety:
  cover -> [per chapter: divider -> feature/quote/text pages]
Templates:
  * cover            full-bleed photo + title block + badge
  * divider          color-field chapter opener (stripe + big serif title)
  * feature(overlay) full-bleed photo + title overlaid + body  (chapter's 1st photo)
  * feature(band)    accent title band + photo + 2-col body     (workhorse)
  * quote            centered pull-quote on a panel
  * text             designed text page (drop cap) for text-only blocks
Every image is used exactly once; photo-bearing pages are always balanced/full.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

import structlog

from bookforge.clients import storage
from bookforge.config import settings
from bookforge.layout import theme
from bookforge.schemas import BlockType, BookBible, Chapter, GeneratedImage

log = structlog.get_logger(__name__)

_CSS = (Path(__file__).parent / "book.css").read_text()


def _uri(key: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(storage.get(key)).decode()


def _esc(t: str) -> str:
    return html.escape(t or "")


def _paras(text: str) -> str:
    return "".join(f"<p>{_esc(p)}</p>" for p in text.split("\n\n") if p.strip())


def _list(text: str, tag: str, cls: str) -> str:
    items = "".join(f"<li>{_esc(x)}</li>" for x in text.split("\n") if x.strip())
    return f'<{tag} class="{cls}">{items}</{tag}>'


def _body_html(block) -> str:
    if block.type == BlockType.ingredients:
        return _list(block.text, "ul", "ingredients")
    if block.type == BlockType.method:
        return _list(block.text, "ol", "method")
    return _paras(block.text)


def _split_paras(text: str, cap: int) -> tuple[str, str]:
    """Split prose at a paragraph boundary so the head fits within `cap` chars
    of plain text. Returns (head_text, tail_text). If text already fits, tail
    is empty."""
    if len(text) <= cap:
        return text, ""
    paras = [p for p in text.split("\n\n") if p.strip()]
    head: list[str] = []
    used = 0
    for i, p in enumerate(paras):
        if head and used + len(p) > cap:
            return "\n\n".join(head), "\n\n".join(paras[i:])
        head.append(p)
        used += len(p) + 2
    return text, ""


# Soft caps on plain-text chars per page template (empirically tuned to fit
# without clipping at body 11.5pt / line-height 1.6).
_CAP_FEATURE_2COL = 1400
_CAP_FEATURE_1COL = 700
_CAP_TEXTPAGE = 2000


class _Director:
    def __init__(self, bible: BookBible):
        self.title = _esc(bible.title)
        self.pages: list[str] = []
        self.folio = 0

    def _chrome(self) -> str:
        self.folio += 1
        return (f'<div class="runhead">{self.title}</div>'
                f'<div class="folio">{self.folio}</div>')

    # ---- templates ---- #
    def cover(self, uri: str, title: str, sub: str, badge: str, kicker: str) -> None:
        kick = f'<div class="kick">{_esc(kicker)}</div>' if kicker else ""
        sub_html = f'<div class="sub">{_esc(sub)}</div>' if sub else ""
        self.pages.append(f"""<section class="page cover">
          <img class="bleed-img" src="{uri}"/><div class="scrim-b"></div>
          <div class="badge">{_esc(badge)}</div>
          <div class="block">{kick}<h1>{_esc(title)}</h1>{sub_html}</div></section>""")

    def divider(self, number: int, title: str, kicker: str) -> None:
        self.pages.append(f"""<section class="page divider">
          <div class="stripe"></div>
          <div class="inner"><div class="eyebrow">Chapter {number}</div>
            <div class="title">{_esc(title)}</div><div class="rule"></div>
            <div class="kicker">{_esc(kicker)}</div></div></section>""")

    def feature(self, eyebrow: str, heading: str, uri: str, body: str,
                overlay: bool, plain_len: int = 0) -> None:
        cls = "page feature overlay" if overlay else "page feature"
        body_cls = "body one-col" if plain_len and plain_len < 350 else "body"
        if overlay:
            head = (f'<div class="titleover"><span class="eyebrow">{_esc(eyebrow)}</span>'
                    f'<h2>{_esc(heading)}</h2></div>')
            photo = f'<div class="photo"><img src="{uri}"/><div class="scrim"></div></div>'
            band = ""
        else:
            band = (f'<div class="band"><div><span class="eyebrow">{_esc(eyebrow)}</span>'
                    f'<h2>{_esc(heading)}</h2></div></div>')
            photo = f'<div class="photo"><img src="{uri}"/></div>'
            head = ""
        self.pages.append(f'<section class="{cls}">{self._chrome()}{band}{photo}{head}'
                          f'<div class="{body_cls}">{body}</div></section>')

    def quote(self, text: str, by: str) -> None:
        self.pages.append(f"""<section class="page quotepage">
          <div class="q"><div class="mark">&ldquo;</div>
            <blockquote>{_esc(text)}</blockquote>
            <div class="by">{_esc(by)}</div></div></section>""")

    def text(self, eyebrow: str, heading: str, body: str, dropcap: bool) -> None:
        col = "col dropcap" if dropcap else "col"
        eb = f'<div class="kicker-head">{_esc(eyebrow)}</div>' if eyebrow else ""
        hd = f'<div class="heading">{_esc(heading)}</div>' if heading else ""
        self.pages.append(f'<section class="page textpage">{self._chrome()}'
                          f'<div class="{col}">{eb}{hd}{body}'
                          f'<div style="clear:both"></div></div></section>')


def build_html(bible: BookBible, chapters: list[Chapter],
               images: list[GeneratedImage], cover_key: str | None = None) -> str:
    img_map = {gi.block_ref: _uri(gi.storage_key) for gi in images}
    plans = {p.index: p for p in bible.chapters}
    d = _Director(bible)

    # COVER — use an explicit cover image, else the first available image.
    cover_uri = _uri(cover_key) if cover_key else (
        next(iter(img_map.values()), None))
    if cover_uri:
        d.cover(cover_uri, bible.title, bible.subtitle, "Forgotten\n& Found",
                kicker="A Field Guide" if bible.subtitle else "")

    for ch_no, ch in enumerate(chapters, start=1):
        ch_start = len(d.pages)
        d.divider(ch_no, ch.title, plans.get(ch.index).summary if ch.index in plans else "")
        first_photo = True
        pending_text: list[str] = []   # plain prose accumulated between photos
        pending_blocks: list[str] = []  # non-prose html (lists) waiting

        for bi, block in enumerate(ch.blocks):
            key = f"{ch.index}.{bi}"
            if block.type == BlockType.pull_quote:
                d.quote(block.text, bible.title)
                continue
            if key in img_map:
                # Pour pending prose + this block's text into the feature, spilling
                # leftover prose onto continuation textpage(s).
                if block.type in (BlockType.ingredients, BlockType.method):
                    # lists go in their own textpage right after the feature
                    cap = _CAP_FEATURE_2COL
                    prose = "\n\n".join(pending_text)
                    pending_text.clear()
                    head, tail = _split_paras(prose, cap)
                    body_html = _paras(head) if head else ""
                    d.feature(ch.title, block.heading or ch.title, img_map[key],
                              body_html, overlay=first_photo, plain_len=len(head))
                    first_photo = False
                    if tail:
                        d.text(ch.title, "", _paras(tail), dropcap=False)
                    pending_blocks.append(_body_html(block))
                else:
                    prose = "\n\n".join(pending_text + [block.text])
                    pending_text.clear()
                    head, tail = _split_paras(prose, _CAP_FEATURE_2COL)
                    body_html = _paras(head)
                    d.feature(ch.title, block.heading or ch.title, img_map[key],
                              body_html, overlay=first_photo, plain_len=len(head))
                    first_photo = False
                    if tail:
                        # spillover onto plain text page(s)
                        rest = tail
                        while rest:
                            head2, rest = _split_paras(rest, _CAP_TEXTPAGE)
                            d.text(ch.title, "", _paras(head2), dropcap=False)
            else:
                if block.type in (BlockType.ingredients, BlockType.method):
                    pending_blocks.append(_body_html(block))
                else:
                    pending_text.append(block.text)

        # Flush any prose that never found a photo into textpage(s).
        if pending_text:
            rest = "\n\n".join(pending_text)
            first_text = True
            while rest:
                head, rest = _split_paras(rest, _CAP_TEXTPAGE)
                d.text(ch.title, "", _paras(head), dropcap=first_text)
                first_text = False
        for blk_html in pending_blocks:
            d.text(ch.title, "", blk_html, dropcap=False)

        plan = plans.get(ch.index)
        if plan:
            used = len(d.pages) - ch_start
            if used > plan.page_budget + 1 or used < plan.page_budget - 1:
                log.info("page_budget_drift", chapter=ch.index,
                         budget=plan.page_budget, used=used)

    root = theme.css_root(theme.pick(bible.title + " " + bible.subtitle),
                          settings.trim_width_in + 2 * settings.bleed_in,
                          settings.trim_height_in + 2 * settings.bleed_in,
                          settings.bleed_in)
    log.info("layout_built", pages=len(d.pages), folios=d.folio)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{_esc(bible.title)}</title>'
            f'<style>{root}{_CSS}</style></head>'
            f'<body>{"".join(d.pages)}</body></html>')
