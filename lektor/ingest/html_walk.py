"""Turn (X)HTML into RawItems. Used by the EPUB reader and the HTML fallback.

Understands EPUB 3 and DPUB-ARIA semantics:

- page breaks: ``epub:type="pagebreak"`` or ``role="doc-pagebreak"``, with the
  label in ``title``, ``aria-label`` or the element text
- note references: ``epub:type="noteref"`` / ``role="doc-noteref"``
- footnotes and endnotes: ``epub:type`` footnote, endnote, rearnote or the
  matching ``doc-`` roles; an ``endnotes`` section marks endnotes
- figures with ``figcaption``; publisher alt text becomes the description
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from bs4 import BeautifulSoup, NavigableString, Tag

from ..script.model import escape_inline
from .common import RawItem

BLOCK_TAGS = {"p", "div", "section", "article", "aside", "blockquote", "header", "footer", "main",
              "figure", "table", "ul", "ol", "dl", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
              "nav", "body", "hr", "dt", "dd", "address"}
SKIP_TAGS = {"script", "style", "head", "title", "meta", "link", "svg", "math", "noscript", "template"}
NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}
NOTE_ROLES = {"doc-footnote", "doc-endnote"}
LEADING_NOTE_NUM_RE = re.compile(r"^\s*(\[?\d{1,4}\]?[.)]?|[*†‡§])\s+")


def _types(tag: Tag) -> set[str]:
    t = tag.get("epub:type") or ""
    roles = tag.get("role") or ""
    return set(t.split()) | set(roles.split())


def is_pagebreak(tag: Tag) -> bool:
    ts = _types(tag)
    return "pagebreak" in ts or "doc-pagebreak" in ts


def page_label(tag: Tag) -> Optional[str]:
    for attr in ("title", "aria-label"):
        v = tag.get(attr)
        if v and v.strip():
            return re.sub(r"\s+", "", v.strip())
    txt = tag.get_text(" ", strip=True)
    if txt:
        return re.sub(r"\s+", "", txt)
    m = re.search(r"(?:page|pg|p)[-_]?([0-9ivxlcdm]+)$", tag.get("id", ""), re.I)
    return m.group(1) if m else None


def is_noteref(tag: Tag) -> bool:
    ts = _types(tag)
    return "noteref" in ts or "doc-noteref" in ts


def is_note(tag: Tag) -> bool:
    ts = _types(tag)
    return bool(ts & NOTE_TYPES) or bool(ts & NOTE_ROLES)


def note_id_from(value: str) -> str:
    v = value.split("#")[-1]
    v = re.sub(r"[^A-Za-z0-9_.-]", "-", v).strip("-")
    return v or "note"


class HtmlWalker:
    def __init__(self) -> None:
        self.items: list[RawItem] = []
        self.page: Optional[str] = None
        self._pending_page: Optional[str] = None
        self.in_endnotes = 0
        self.title: Optional[str] = None

    # -- inline text --------------------------------------------------------
    def inline_text(self, node: Tag) -> str:
        """Readable text of an element, with inline page markers and note refs."""
        parts: list[str] = []
        PLACE = "\x00"

        def walk(n):
            for child in n.children:
                if isinstance(child, NavigableString):
                    if child.__class__.__name__ in ("Comment", "Doctype", "ProcessingInstruction", "CData"):
                        continue
                    parts.append(str(child))
                    continue
                if not isinstance(child, Tag) or child.name in SKIP_TAGS:
                    continue
                if is_pagebreak(child):
                    label = page_label(child)
                    if label:
                        parts.append(f" {PLACE}page {label}{PLACE} ")
                        # Applied after the block is added: the block itself
                        # starts on the earlier page.
                        self._pending_page = label
                    continue
                if is_noteref(child) or (child.name == "sup" and child.find("a", href=True) and
                                          child.get_text(strip=True).isdigit()):
                    a = child if child.name == "a" else child.find("a", href=True)
                    href = a.get("href", "") if a else ""
                    nid = note_id_from(href) if href else child.get_text(strip=True)
                    parts.append(f"{PLACE}fn {nid}{PLACE}")
                    continue
                if child.name == "br":
                    parts.append(" ")
                    continue
                if child.name == "img":
                    continue
                walk(child)

        walk(node)
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        text = escape_inline(text)
        text = re.sub(r"\x00page ([^\x00]+)\x00", r"[[page \1]]", text)
        text = re.sub(r"\s*\x00fn ([^\x00]+)\x00", r"[^\1]", text)
        return re.sub(r"\s+", " ", text).strip()

    def _has_block_children(self, tag: Tag) -> bool:
        return any(isinstance(c, Tag) and (c.name in BLOCK_TAGS) and not is_pagebreak(c) for c in tag.children)

    # -- blocks -------------------------------------------------------------
    def add(self, **kw) -> None:
        kw.setdefault("page", self.page)
        self.items.append(RawItem(**kw))
        if self._pending_page:
            self.page = self._pending_page
            self._pending_page = None

    def walk(self, node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                if child.strip() and child.__class__.__name__ == "NavigableString":
                    self.add(kind="para", text=escape_inline(re.sub(r"\s+", " ", child.strip())))
                continue
            if not isinstance(child, Tag) or child.name in SKIP_TAGS:
                continue
            self.block(child)

    def block(self, tag: Tag) -> None:
        name = tag.name
        types = _types(tag)
        if is_pagebreak(tag):
            label = page_label(tag)
            if label:
                self.page = label
                self.add(kind="page", text=label, page=label)
            return
        if name == "nav" or "toc" in types or "doc-toc" in types or "landmarks" in types or "page-list" in types:
            return
        if "endnotes" in types or "doc-endnotes" in types or "rearnotes" in types:
            self.in_endnotes += 1
            self.walk(tag)
            self.in_endnotes -= 1
            return
        if is_note(tag) and name != "a":
            text = self.inline_text(tag)
            text = LEADING_NOTE_NUM_RE.sub("", text, count=1)
            nid = note_id_from(tag.get("id") or "") if tag.get("id") else f"n{len(self.items)}"
            endnote = self.in_endnotes > 0 or "endnote" in types or "rearnote" in types or "doc-endnote" in types
            if text:
                self.add(kind="footnote", note_id=nid, text=text, endnote=endnote)
            return
        if self.in_endnotes and name == "li" and tag.get("id"):
            text = LEADING_NOTE_NUM_RE.sub("", self.inline_text(tag), count=1)
            self.add(kind="footnote", note_id=note_id_from(tag["id"]), text=text, endnote=True)
            return
        if re.fullmatch(r"h[1-6]", name) or (tag.get("role") == "heading" and tag.get("aria-level")):
            level = int(name[1]) if name.startswith("h") else int(tag.get("aria-level", 2))
            text = self.inline_text(tag)
            text = re.sub(r"\[\^[^\]]+\]", "", text).strip()
            if text:
                self.add(kind="heading", text=text, level=level)
            return
        if name == "figure":
            cap = tag.find("figcaption")
            caption = self.inline_text(cap) if cap else ""
            img = tag.find("img")
            alt = (img.get("alt") or "").strip() if img else ""
            self.add(kind="figure", caption=caption, text=escape_inline(alt) if _real_alt(alt) else "")
            return
        if name == "img":
            alt = (tag.get("alt") or "").strip()
            self.add(kind="figure", caption="", text=escape_inline(alt) if _real_alt(alt) else "")
            return
        if name == "table":
            self.table(tag)
            return
        if name in ("ul", "ol"):
            ordered = name == "ol"
            n = int(tag.get("start", 1)) if str(tag.get("start", "1")).isdigit() else 1
            for li in tag.find_all("li", recursive=False):
                nested = [c for c in li.children if isinstance(c, Tag) and c.name in ("ul", "ol")]
                for c in nested:
                    c.extract()
                text = self.inline_text(li)
                if text:
                    self.add(kind="list_item", text=text, ordered=ordered, marker=f"{n}." if ordered else "-")
                n += 1
                for c in nested:
                    self.block(c)
            return
        if name == "dl":
            for c in tag.find_all(["dt", "dd"], recursive=False):
                t = self.inline_text(c)
                if t:
                    self.add(kind="para", text=t)
            return
        if name in ("hr",):
            return
        if name in BLOCK_TAGS and self._has_block_children(tag):
            self.walk(tag)
            return
        if name in BLOCK_TAGS or name in ("span", "a", "em", "strong", "i", "b"):
            # A single paragraph-like block.
            for img in tag.find_all("img"):
                alt = (img.get("alt") or "").strip()
                self.add(kind="figure", caption="", text=escape_inline(alt) if _real_alt(alt) else "")
            text = self.inline_text(tag)
            if text:
                self.add(kind="para", text=text)
            return
        self.walk(tag)

    def table(self, tag: Tag) -> None:
        rows: list[list[str]] = []
        header_rows = 0
        caption_tag = tag.find("caption")
        caption = self.inline_text(caption_tag) if caption_tag else ""
        thead = tag.find("thead")
        for tr in tag.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            row = [self.inline_text(c) for c in cells]
            if not any(row):
                continue
            in_head = thead is not None and tr.find_parent("thead") is thead
            if (in_head or all(c.name == "th" for c in cells)) and header_rows == len(rows):
                header_rows += 1
            rows.append(row)
        if rows:
            self.add(kind="table", rows=rows, header_rows=header_rows, caption=caption)


def _real_alt(alt: str) -> bool:
    if not alt or len(alt) < 4:
        return False
    if re.fullmatch(r"[\w-]+\.(png|jpe?g|gif|svg|webp)", alt, re.I):
        return False
    return alt.lower() not in {"image", "figure", "picture", "photo", "illustration"}


def walk_html(html: str, walker: Optional[HtmlWalker] = None) -> HtmlWalker:
    soup = BeautifulSoup(html, "html.parser")
    w = walker or HtmlWalker()
    if w.title is None and soup.title and soup.title.string:
        w.title = soup.title.string.strip()
    body = soup.body or soup
    w.walk(body)
    return w


def items_from_html_strings(htmls: Iterable[str]) -> list[RawItem]:
    w = HtmlWalker()
    for h in htmls:
        walk_html(h, w)
    return w.items
