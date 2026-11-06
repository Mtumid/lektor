"""Shared ingest model and clean-up passes.

Every reader turns its format into a flat list of :class:`RawItem` objects
in reading order. :func:`build_script` then applies the same clean-up to all
formats:

1. drop page furniture (running headers and footers, printed page numbers)
2. repair line end hyphenation that survived the reader
3. merge paragraphs that continue across a page break (with an inline
   page marker at the join)
4. decide heading levels (heuristics plus ``heading_rules`` in book.yaml)
5. mark sections such as the table of contents as skip blocks
6. give figures and tables stable ids
7. turn the result into a :class:`~lektor.script.model.Script`

The author's words are never changed beyond removing furniture and
rejoining hyphenated words, as required by CDPA s31B (see
``docs/accessibility-and-copyright.md``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ..script.model import (
    INLINE_PAGE_RE,
    Figure,
    Footnote,
    Heading,
    ListBlock,
    ListItem,
    PageMarker,
    Paragraph,
    Script,
    Skip,
    Table,
)
from ..script.writer import write_block


@dataclass
class RawItem:
    kind: str  # heading, para, list_item, figure, table, footnote, page, furniture
    text: str = ""
    page: Optional[str] = None  # print page label this item starts on
    level: Optional[int] = None  # heading level suggested by the reader
    ordered: bool = False
    marker: str = ""
    caption: str = ""
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 1
    note_id: str = ""
    endnote: bool = False
    # Reader hints: font size relative to body text, position on the page
    size_ratio: Optional[float] = None
    bold: bool = False
    at_top: bool = False
    at_bottom: bool = False
    physical_page: Optional[int] = None
    # The reader may already know this block continues the previous one
    continues: bool = False


SENTENCE_END_RE = re.compile(r"[.!?:;…\"'”’)\]]\s*$")
ROMAN_RE = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?(\d{1,4}|[ivxlcdm]{1,8})\s*$", re.I)
NUMBERED_HEADING_RE = re.compile(r"^\s*(\d{1,3})(?:[.:)]|\s*[–—-])?\s+\S")
PART_HEADING_RE = re.compile(
    r"^\s*(part|book|volume|section)\s+([ivxlcdm]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.I,
)
CHAPTER_HEADING_RE = re.compile(r"^\s*(chapter|strategy|lesson|unit)\s+(\w+)", re.I)
DECIMAL_HEADING_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3})+)\.?\s+\S")
MATTER_HEADINGS = {
    "contents", "table of contents", "introduction", "preface", "foreword", "prologue",
    "epilogue", "afterword", "acknowledgements", "acknowledgments", "index", "bibliography",
    "references", "notes", "endnotes", "glossary", "about the author", "about the authors",
    "further reading", "appendix", "conclusion", "dedication", "copyright",
}
SUSPENDED_HYPHEN_WORDS = {"and", "or", "nor", "to", "through", "versus", "vs", "than"}
HYPHEN_JOIN_RE = re.compile(r"(\w+)-\s+([a-z]\w*)")


LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
             "\ufb05": "st", "\ufb06": "st", "\u00ad": "", "\u00a0": " ", "\u2009": " ", "\u200b": ""}
_LIG_RE = re.compile("|".join(LIGATURES))


def fix_glyphs(text: str) -> str:
    """Undo typesetting artefacts: ligature glyphs, soft hyphens, odd spaces."""
    return _LIG_RE.sub(lambda m: LIGATURES[m.group(0)], text) if text else text


def is_page_number_text(text: str) -> bool:
    return bool(PAGE_NUMBER_RE.match(text)) and (text.strip().isdigit() or bool(ROMAN_RE.match(text.strip())))


def norm_furniture(text: str) -> str:
    """Normalise a line for repeated header/footer detection."""
    t = re.sub(r"\d+", "#", text.lower())
    t = re.sub(r"\b[ivxlcdm]+\b", "#", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# 1. Furniture
# ---------------------------------------------------------------------------
def drop_furniture(items: list[RawItem], n_pages: int) -> tuple[list[RawItem], int]:
    """Remove running headers/footers and printed page numbers.

    A short line counts as furniture when it sits at the top or bottom of a
    page and (after replacing digits) repeats on at least a quarter of the
    pages, or when it is just a page number.
    """
    counts: Counter[str] = Counter()
    pages_for: dict[str, set] = {}
    for it in items:
        if it.kind in ("para", "heading") and (it.at_top or it.at_bottom) and len(it.text) < 90:
            key = norm_furniture(it.text)
            pages_for.setdefault(key, set()).add(it.physical_page)
    threshold = max(3, int(0.25 * max(n_pages, 1)))
    repeated = {k for k, pages in pages_for.items() if len(pages) >= threshold}
    out, dropped = [], 0
    for it in items:
        if it.kind == "furniture":
            dropped += 1
            continue
        if it.kind in ("para", "heading") and (it.at_top or it.at_bottom):
            if is_page_number_text(it.text) or norm_furniture(it.text) in repeated:
                dropped += 1
                continue
        out.append(it)
    return out, dropped


# ---------------------------------------------------------------------------
# 2. Hyphenation
# ---------------------------------------------------------------------------
def word_counts(items: list[RawItem]) -> Counter[str]:
    c: Counter[str] = Counter()
    for it in items:
        for w in re.findall(r"[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]", it.text):
            c[w.lower()] += 1
    return c


def join_hyphenated(left: str, right: str, counts: Counter[str]) -> str:
    """Join a word split across lines as ``left-`` / ``right``.

    Keep the hyphen when the hyphenated form occurs elsewhere and the closed
    form does not (``music-making``); otherwise close it up (``produc-tion``).
    """
    if right.lower() in SUSPENDED_HYPHEN_WORDS:
        return f"{left}- {right}"  # "left- and right-hand"
    hyph = f"{left}-{right}".lower()
    closed = f"{left}{right}".lower()
    if counts.get(hyph, 0) > counts.get(closed, 0):
        return f"{left}-{right}"
    if left[-1:].isupper() or left.isdigit():
        return f"{left}-{right}"
    return f"{left}{right}"


def repair_hyphenation(text: str, counts: Counter[str]) -> str:
    """Repair ``word- word`` left behind when lines were joined with a space."""
    return HYPHEN_JOIN_RE.sub(lambda m: join_hyphenated(m.group(1), m.group(2), counts), text)


# ---------------------------------------------------------------------------
# 3. Paragraphs across page breaks
# ---------------------------------------------------------------------------
def merge_across_pages(items: list[RawItem], counts: Counter[str]) -> list[RawItem]:
    out: list[RawItem] = []
    for it in items:
        # Footnotes printed at the foot of the page sit between the two
        # halves of a paragraph; look past them.
        k = len(out) - 1
        while k >= 0 and out[k].kind == "footnote":
            k -= 1
        prev = out[k] if k >= 0 else None
        if (
            it.kind == "para"
            and prev is not None
            and prev.kind == "para"
            and it.page != prev.page
            and it.page is not None
            and (it.continues or (
                not SENTENCE_END_RE.search(prev.text)
                and it.text[:1].islower()
            ))
        ):
            marker = f"[[page {it.page}]]"
            if prev.text.endswith("-") and it.text[:1].islower():
                m = re.search(r"(\w+)-$", prev.text)
                left = m.group(1) if m else ""
                first, _, rest = it.text.partition(" ")
                joined = join_hyphenated(left, first, counts)
                prev.text = prev.text[: len(prev.text) - len(left) - 1] + joined + f" {marker} " + rest
            else:
                prev.text = f"{prev.text} {marker} {it.text}"
            prev.text = prev.text.strip()
            continue
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# 4. Heading levels
# ---------------------------------------------------------------------------
def heading_class(text: str) -> str:
    t = text.strip()
    if PART_HEADING_RE.match(t):
        return "part"
    if DECIMAL_HEADING_RE.match(t):
        return "decimal"
    if CHAPTER_HEADING_RE.match(t) or NUMBERED_HEADING_RE.match(t):
        return "numbered"
    if t.lower().rstrip(":. ") in MATTER_HEADINGS or t.lower().startswith("appendix"):
        return "matter"
    return "plain"


def assign_heading_levels(items: list[RawItem], rules: list[tuple[re.Pattern, int]]) -> int:
    """Decide the level of every heading; return the chapter level.

    Numbered headings (``12. Title``, ``Chapter 3``) are the chapters.
    ``Part`` headings and plain headings that introduce a run of numbered
    headings sit one level above them. Other plain headings sit below.
    When nothing is numbered, the reader's own levels (font size) are used.
    """
    heads = [i for i, it in enumerate(items) if it.kind == "heading"]
    classes = {i: heading_class(items[i].text) for i in heads}
    numbered = [i for i in heads if classes[i] == "numbered"]
    has_parts = any(c == "part" for c in classes.values())

    if len(numbered) >= 2:
        # Plain headings followed (before any other content heading) by a
        # numbered heading, with little text in between, are containers.
        container = set()
        for idx, i in enumerate(heads):
            if classes[i] != "plain":
                continue
            nxt = heads[idx + 1] if idx + 1 < len(heads) else None
            between = [items[k] for k in range(i + 1, nxt if nxt else len(items)) if items[k].kind == "para"]
            bigger = (items[i].size_ratio or 0) > max((items[n].size_ratio or 0) for n in numbered) + 0.05
            if nxt is not None and classes[nxt] in ("numbered", "part") and (len(between) <= 3 or bigger):
                container.add(i)
        top = 1 if (has_parts or container) else 0
        chapter_level = top + 1
        for i in heads:
            c = classes[i]
            if c == "part" or i in container:
                lvl = 1
            elif c in ("numbered", "matter"):
                lvl = chapter_level
            elif c == "decimal":
                depth = items[i].text.strip().split()[0].rstrip(".").count(".")
                lvl = min(6, chapter_level + depth)
            elif i < min(numbered + [j for j in heads if classes[j] == "part" or j in container]):
                # Plain headings before the first chapter are front matter.
                lvl = chapter_level
            else:
                lvl = min(6, chapter_level + 1)
            items[i].level = lvl
    else:
        # Fall back to reader levels, else font size ranks.
        if any(items[i].level for i in heads):
            for i in heads:
                items[i].level = items[i].level or 2
        else:
            sizes = sorted({round(items[i].size_ratio or 1.0, 2) for i in heads}, reverse=True)
            rank = {s: n + 1 for n, s in enumerate(sizes[:5])}
            for i in heads:
                items[i].level = rank.get(round(items[i].size_ratio or 1.0, 2), 6)
        for i in heads:
            if classes[i] == "part":
                items[i].level = 1
        chapter_level = min((items[i].level for i in heads), default=1)

    # Close up gaps so levels start at 1 and never jump.
    if heads:
        used = sorted({items[i].level for i in heads})
        remap = {lvl: n + 1 for n, lvl in enumerate(used)}
        for i in heads:
            items[i].level = remap[items[i].level]
        chapter_level = remap.get(chapter_level, 1)
    # Explicit rules from book.yaml win, exactly as written.
    for i in heads:
        for pattern, level in rules:
            if pattern.search(items[i].text):
                items[i].level = level
                break
    return chapter_level


# ---------------------------------------------------------------------------
# 5-7. Build the script
# ---------------------------------------------------------------------------
def _slug_number(text: str) -> Optional[str]:
    m = NUMBERED_HEADING_RE.match(text) or CHAPTER_HEADING_RE.match(text)
    if m and m.group(1).isdigit():
        return f"{int(m.group(1)):03d}"
    if m and len(m.groups()) > 1 and m.group(2) and m.group(2).isdigit():
        return f"{int(m.group(2)):03d}"
    return None


def build_script(
    items: list[RawItem],
    *,
    meta: dict[str, str],
    n_pages: int = 0,
    heading_rules: list[tuple[str, int]] = (),
    skip_sections: list[str] = (),
    chapter_level: Optional[int] = None,
) -> tuple[Script, dict]:
    """Clean up reader output and turn it into a Script. Returns (script, stats)."""
    stats: dict = {}
    for it in items:
        it.text = fix_glyphs(it.text)
        it.caption = fix_glyphs(it.caption)
        it.rows = [[fix_glyphs(c) for c in r] for r in it.rows]
    items, stats["furniture_removed"] = drop_furniture(items, n_pages)
    counts = word_counts(items)
    for it in items:
        if it.kind in ("para", "list_item", "footnote"):
            it.text = repair_hyphenation(it.text, counts)
    items = merge_across_pages(items, counts)
    compiled = [(re.compile(p, re.I), lvl) for p, lvl in heading_rules]
    auto_level = assign_heading_levels(items, compiled)
    stats["chapter_level"] = chapter_level or auto_level

    script = Script(meta=dict(meta))
    script.meta["chapter-level"] = str(stats["chapter_level"])
    skip_names = {s.lower().strip() for s in skip_sections}
    current_page: Optional[str] = None
    seen_pages: set[str] = set()
    chapter_no = 0
    chapter_slug = "000"
    fig_n = tab_n = 0
    skipping: Optional[Skip] = None
    skip_level = 0
    pending_list: Optional[ListBlock] = None

    def emit(block):
        nonlocal pending_list
        if not isinstance(block, ListBlock):
            pending_list = None
        if skipping is not None:
            skipping.lines.extend(write_block(block).split("\n"))
            skipping.lines.append("")
        else:
            script.blocks.append(block)

    for it in items:
        if it.kind == "heading" and skipping is not None and it.level <= skip_level:
            while skipping.lines and not skipping.lines[-1]:
                skipping.lines.pop()
            skipping = None
        if it.kind == "page":
            if it.text not in seen_pages:
                emit(PageMarker(it.text))
                seen_pages.add(it.text)
            current_page = it.text
            continue
        # Mark each print page once, where its first item starts. Items that
        # belong to an earlier page (a footnote after a paragraph that ran
        # onto the next page) do not re-mark it.
        if it.page is not None and it.page != current_page and it.page not in seen_pages:
            emit(PageMarker(it.page))
            seen_pages.add(it.page)
            current_page = it.page
        if it.kind == "heading":
            if skipping is None and it.text.strip().lower().rstrip(":. ") in skip_names:
                # The heading goes inside the skip block too, so the section
                # is neither read nor offered as a navigation point.
                skipping = Skip([write_block(Heading(it.level, it.text)), ""], reason=it.text.strip().lower())
                skip_level = it.level
                script.blocks.append(skipping)
                continue
            if it.level == stats["chapter_level"] and skipping is None:
                chapter_no += 1
                chapter_slug = _slug_number(it.text) or f"{chapter_no:03d}"
                fig_n = tab_n = 0
            emit(Heading(it.level, it.text))
        elif it.kind == "para":
            if it.text.strip():
                emit(Paragraph(it.text.strip()))
                inline = INLINE_PAGE_RE.findall(it.text)
                if inline:
                    current_page = inline[-1]
                    seen_pages.update(inline)
        elif it.kind == "list_item":
            item = ListItem(it.text.strip(), it.marker or ("1." if it.ordered else "-"))
            if pending_list is not None and pending_list.ordered == it.ordered and skipping is None:
                pending_list.items.append(item)
            else:
                lb = ListBlock([item], it.ordered)
                emit(lb)
                pending_list = lb if skipping is None else None
        elif it.kind == "figure":
            fig_n += 1
            # For figures, ``text`` carries a description supplied with the
            # source (for example EPUB alt text written by the publisher).
            emit(Figure(id=f"fig-{chapter_slug}-{fig_n}", page=it.page or current_page, caption=it.caption.strip(),
                        description=it.text.strip(), description_source="publisher" if it.text.strip() else ""))
        elif it.kind == "table":
            tab_n += 1
            emit(Table(id=f"tab-{chapter_slug}-{tab_n}", page=it.page or current_page, rows=it.rows,
                       header_rows=it.header_rows if it.rows else 0, caption=it.caption.strip()))
        elif it.kind == "footnote":
            emit(Footnote(it.note_id, it.text.strip(), endnote=it.endnote))
    if skipping is not None:
        while skipping.lines and not skipping.lines[-1]:
            skipping.lines.pop()
    stats["headings"] = sum(1 for b in script.blocks if isinstance(b, Heading))
    stats["chapters"] = sum(1 for b in script.blocks if isinstance(b, Heading) and b.level == stats["chapter_level"])
    stats["figures"] = sum(1 for b in script.blocks if isinstance(b, Figure))
    stats["tables"] = sum(1 for b in script.blocks if isinstance(b, Table))
    return script, stats
