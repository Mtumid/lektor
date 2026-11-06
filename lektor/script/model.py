"""In-memory model of a Lektor script.

A :class:`Script` is an ordered list of blocks plus some front matter
metadata. Every block remembers the line it came from (``line``) so that the
validator and the review report can point the librarian at it.

Inline content: paragraph, list item, caption and note text may contain two
inline markers, both kept verbatim in ``text``:

- ``[[page 48]]``  a print page break in the middle of a paragraph
- ``[^12]``        a reference to footnote 12

Use :func:`split_inline` to separate them from the readable text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional, Union

INLINE_PAGE_RE = re.compile(r"(?<!\\)\[\[page\s+([^\]\s]+)\s*\]\]")
FOOTNOTE_REF_RE = re.compile(r"(?<!\\)\[\^([A-Za-z0-9_.-]+)\]")


@dataclass
class Heading:
    level: int
    text: str
    line: int = 0
    kind: str = "heading"


@dataclass
class PageMarker:
    label: str
    line: int = 0
    kind: str = "page"


@dataclass
class Paragraph:
    text: str
    line: int = 0
    kind: str = "paragraph"


@dataclass
class ListItem:
    text: str
    marker: str = "-"  # "-" for bullets, "1." style for numbered items
    line: int = 0


@dataclass
class ListBlock:
    items: list[ListItem]
    ordered: bool = False
    line: int = 0
    kind: str = "list"


@dataclass
class Figure:
    id: str
    page: Optional[str] = None
    caption: str = ""
    description: str = ""
    # Who wrote the description: "" (none), "librarian", or later "ai-draft"
    # / "ai-approved". Kept so AI drafted descriptions can be added later.
    description_source: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    line: int = 0
    kind: str = "figure"


@dataclass
class Table:
    id: str
    page: Optional[str] = None
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 1
    caption: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    line: int = 0
    kind: str = "table"

    @property
    def n_cells(self) -> int:
        return sum(len(r) for r in self.rows)


@dataclass
class Footnote:
    id: str
    text: str
    endnote: bool = False
    line: int = 0
    kind: str = "footnote"


@dataclass
class Note:
    """A note added by the library. Read in the secondary voice."""

    text: str
    line: int = 0
    kind: str = "note"


@dataclass
class Comment:
    """A librarian's comment (``<!-- ... -->``). Kept, never read aloud."""

    text: str
    line: int = 0
    kind: str = "comment"


@dataclass
class Skip:
    """Lines kept in the script but never read aloud."""

    lines: list[str]
    reason: str = ""
    line: int = 0
    kind: str = "skip"


Block = Union[Heading, PageMarker, Paragraph, ListBlock, Figure, Table, Footnote, Note, Skip, Comment]


@dataclass
class Script:
    meta: dict[str, str] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)

    def headings(self) -> Iterator[Heading]:
        return (b for b in self.blocks if isinstance(b, Heading))

    def footnotes(self) -> dict[str, Footnote]:
        return {b.id: b for b in self.blocks if isinstance(b, Footnote)}

    def figures(self) -> list[Figure]:
        return [b for b in self.blocks if isinstance(b, Figure)]


def split_inline(text: str) -> list[tuple[str, str]]:
    """Split text into ``("text", s)``, ``("page", label)`` and ``("fnref", id)`` parts."""
    parts: list[tuple[str, str]] = []
    pattern = re.compile(INLINE_PAGE_RE.pattern + "|" + FOOTNOTE_REF_RE.pattern)
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            parts.append(("text", text[pos : m.start()]))
        if m.group(1) is not None:
            parts.append(("page", m.group(1)))
        else:
            parts.append(("fnref", m.group(2)))
        pos = m.end()
    if pos < len(text):
        parts.append(("text", text[pos:]))
    return parts


def plain_text(text: str) -> str:
    """Text with inline page markers and footnote references removed."""
    out = "".join(s for k, s in split_inline(text) if k == "text")
    out = out.replace("\\[[", "[[").replace("\\[^", "[^")
    return re.sub(r"\s{2,}", " ", out).strip()


def block_text(block: Block) -> str:
    """The readable text of a block, for statistics and searching."""
    if isinstance(block, (Heading, Paragraph, Note, Footnote)):
        return plain_text(block.text)
    if isinstance(block, ListBlock):
        return " ".join(plain_text(i.text) for i in block.items)
    if isinstance(block, Figure):
        return " ".join(x for x in (block.caption, block.description) if x)
    if isinstance(block, Table):
        return " ".join(" ".join(r) for r in block.rows)
    return ""


def escape_inline(text: str) -> str:
    """Escape source text so literal ``[[`` and ``[^`` are not read as markup.

    Readers call this on every piece of author text before putting it in a
    block. Inline page markers and footnote references are added afterwards.
    """
    return text.replace("[[", "\\[[").replace("[^", "\\[^")
