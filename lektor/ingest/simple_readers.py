"""Readers for plain text, Markdown, and fallback readers for DOCX and HTML.

Docling is preferred for DOCX, PPTX and HTML; the fallbacks here keep
Lektor working (and testable) when Docling is not installed.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import IngestError
from ..script.model import escape_inline
from .common import NUMBERED_HEADING_RE, PART_HEADING_RE, RawItem, join_hyphenated
from .html_walk import items_from_html_strings, walk_html

BULLET_RE = re.compile(r"^\s*([-*+•])\s+(.*)$")
ORDERED_RE = re.compile(r"^\s*(\d{1,3}[.)])\s+(.*)$")


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    raise IngestError(f"{path.name} could not be read as text.")


def _join_lines(lines: list[str]) -> str:
    text = ""
    for ln in lines:
        ln = ln.strip()
        if text.endswith("-") and ln[:1].islower():
            m = re.search(r"(\w+)-$", text)
            first, _, rest = ln.partition(" ")
            if m:
                from collections import Counter

                text = text[: m.start()] + join_hyphenated(m.group(1), first, Counter()) + (" " + rest if rest else "")
                continue
        text = (text + " " + ln).strip()
    return text


def _looks_like_heading(text: str) -> bool:
    if len(text) > 90 or "\n" in text:
        return False
    if re.search(r"[.,;:!?]$", text):
        return False
    letters = re.sub(r"[^A-Za-z]", "", text)
    if not letters:
        return False
    return letters.isupper() or bool(NUMBERED_HEADING_RE.match(text)) or bool(PART_HEADING_RE.match(text)) or (
        len(text.split()) <= 8 and text[:1].isupper() and text.istitle()
    )


def read_txt(path: Path) -> list[RawItem]:
    text = _read_text_file(path)
    text = text.replace("\f", "\n\n")
    blocks = re.split(r"\n\s*\n", text)
    items: list[RawItem] = []
    for blk in blocks:
        lines = [l for l in blk.split("\n") if l.strip()]
        if not lines:
            continue
        if len(lines) == 1 and _looks_like_heading(lines[0].strip()):
            items.append(RawItem(kind="heading", text=escape_inline(lines[0].strip())))
            continue
        if all(BULLET_RE.match(l) or ORDERED_RE.match(l) for l in lines):
            for l in lines:
                m = BULLET_RE.match(l) or ORDERED_RE.match(l)
                ordered = bool(ORDERED_RE.match(l))
                items.append(RawItem(kind="list_item", text=escape_inline(m.group(2).strip()), ordered=ordered,
                                     marker=m.group(1) if ordered else "-"))
            continue
        joined = _join_lines(lines)
        if len(lines) == 1 and _looks_like_heading(joined):
            items.append(RawItem(kind="heading", text=escape_inline(joined)))
        else:
            items.append(RawItem(kind="para", text=escape_inline(joined)))
    return items


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
MD_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]*)\)\s*$")
MD_FOOTNOTE_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)$")
MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _md_inline(text: str) -> str:
    """Strip Markdown inline formatting, keeping the words."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]^][^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"\1", text)
    # Keep footnote references [^1] as markup; escape other brackets.
    refs: list[str] = []
    text = re.sub(r"\[\^([^\]]+)\]", lambda m: refs.append(m.group(1)) or f"\x00{len(refs) - 1}\x00", text)
    text = escape_inline(text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"[^{re.sub(r'[^A-Za-z0-9_.-]', '-', refs[int(m.group(1))])}]", text)
    return re.sub(r"\s+", " ", text).strip()


def read_markdown(path: Path) -> list[RawItem]:
    text = _read_text_file(path)
    if text.lstrip().startswith("---"):
        # Drop YAML front matter.
        parts = text.lstrip().split("\n")
        for j in range(1, len(parts)):
            if parts[j].strip() == "---":
                text = "\n".join(parts[j + 1 :])
                break
    lines = text.split("\n")
    items: list[RawItem] = []
    i = 0
    para: list[str] = []

    def flush():
        if para:
            items.append(RawItem(kind="para", text=_md_inline(_join_lines(para))))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            flush()
            i += 1
            continue
        if s.startswith("```"):
            flush()
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            items.append(RawItem(kind="para", text=escape_inline(" ".join(c.strip() for c in code if c.strip()))))
            i = j + 1
            continue
        m = MD_HEADING_RE.match(s)
        if m:
            flush()
            items.append(RawItem(kind="heading", text=_md_inline(m.group(2)), level=len(m.group(1))))
            i += 1
            continue
        if i + 1 < len(lines) and re.fullmatch(r"=+|-+", lines[i + 1].strip() or "x") and not para and len(lines[i + 1].strip()) >= 3:
            items.append(RawItem(kind="heading", text=_md_inline(s), level=1 if lines[i + 1].strip()[0] == "=" else 2))
            i += 2
            continue
        m = MD_IMAGE_RE.match(s)
        if m:
            flush()
            items.append(RawItem(kind="figure", caption=_md_inline(m.group(1))))
            i += 1
            continue
        m = MD_FOOTNOTE_RE.match(s)
        if m:
            flush()
            items.append(RawItem(kind="footnote", note_id=re.sub(r"[^A-Za-z0-9_.-]", "-", m.group(1)),
                                 text=_md_inline(m.group(2))))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and MD_TABLE_SEP_RE.match(lines[i + 1].strip()):
            flush()
            rows = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                if not MD_TABLE_SEP_RE.match(lines[j].strip()):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    rows.append([_md_inline(c) for c in cells])
                j += 1
            items.append(RawItem(kind="table", rows=rows, header_rows=1))
            i = j
            continue
        mb, mo = BULLET_RE.match(ln), ORDERED_RE.match(ln)
        if (mb or mo) and not para:
            m = mb or mo
            items.append(RawItem(kind="list_item", text=_md_inline(m.group(2)), ordered=bool(mo),
                                 marker=m.group(1) if mo else "-"))
            i += 1
            continue
        if s.startswith(">"):
            s = s.lstrip("> ")
        para.append(s)
        i += 1
    flush()
    return items


# ---------------------------------------------------------------------------
# DOCX and HTML fallbacks
# ---------------------------------------------------------------------------
def read_docx_fallback(path: Path) -> tuple[list[RawItem], dict[str, str]]:
    try:
        import docx
    except ImportError as e:  # pragma: no cover
        raise IngestError("The DOCX reader (python-docx) is not installed.",
                          hint="Run the setup cell (section 0) again.") from e
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise IngestError(f"{path.name} could not be opened as a Word document ({e}).") from e
    meta = {}
    cp = d.core_properties
    if cp.title:
        meta["title"] = cp.title
    if cp.author:
        meta["author"] = cp.author
    items: list[RawItem] = []
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxPara

    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            p = DocxPara(child, d)
            text = escape_inline(re.sub(r"\s+", " ", p.text).strip())
            style = (p.style.name if p.style is not None else "") or ""
            has_image = bool(child.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"))
            if has_image:
                items.append(RawItem(kind="figure"))
            if not text:
                continue
            sl = style.lower()
            m = re.match(r"heading\s*(\d)", sl)
            if m:
                items.append(RawItem(kind="heading", text=text, level=int(m.group(1))))
            elif sl == "title":
                items.append(RawItem(kind="heading", text=text, level=1))
            elif sl == "caption":
                if items and items[-1].kind in ("figure", "table") and not items[-1].caption:
                    items[-1].caption = text
                else:
                    items.append(RawItem(kind="figure", caption=text))
            elif "list" in sl or child.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr") is not None:
                ordered = "number" in sl
                items.append(RawItem(kind="list_item", text=text, ordered=ordered, marker="1." if ordered else "-"))
            else:
                items.append(RawItem(kind="para", text=text))
        elif tag == "tbl":
            t = DocxTable(child, d)
            rows = []
            for r in t.rows:
                row = []
                for c in r.cells:
                    row.append(escape_inline(re.sub(r"\s+", " ", c.text).strip()))
                rows.append(row)
            if rows:
                items.append(RawItem(kind="table", rows=rows, header_rows=1))
    return items, meta


def read_html_fallback(path: Path) -> tuple[list[RawItem], dict[str, str]]:
    w = walk_html(_read_text_file(path))
    return w.items, ({"title": w.title} if w.title else {})


__all__ = ["read_txt", "read_markdown", "read_docx_fallback", "read_html_fallback", "items_from_html_strings"]
