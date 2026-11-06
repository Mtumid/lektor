"""A lightweight PDF reader built on pdfplumber.

Docling is the main PDF reader (it understands layout much better). This
reader is used when Docling is not available or fails, and in the automated
tests, which cannot download Docling's layout models. It handles:

- running headers, footers and printed page numbers (via margin position)
- headings, by font size and weight relative to the body text
- paragraphs, by line spacing and indentation
- line end hyphenation
- bullet and numbered lists
- simple ruled tables (pdfplumber's table finder)
- images with "Figure ..." captions
- footnotes (small text at the foot of the page) and superscript references

It does not do OCR. Scanned PDFs need Docling.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Optional

from ..errors import IngestError
from ..script.model import escape_inline
from .common import RawItem, is_page_number_text, join_hyphenated
from .pages import pdf_page_labels, resolve_labels

BULLET_CHARS = "•▪◦●○■□–‣⁃·*-"
BULLET_RE = re.compile(r"^([" + re.escape(BULLET_CHARS) + r"])\s+(.*)$")
ORDERED_RE = re.compile(r"^(\d{1,3}[.)]|[a-z][.)])\s+(.*)$")
CAPTION_RE = re.compile(r"^(fig(ure)?\.?|plate|illustration|example|screenshot)\s*[\dA-Z]", re.I)
TABLE_CAPTION_RE = re.compile(r"^table\s*\d", re.I)
FOOTNOTE_START_RE = re.compile(r"^(\d{1,3}|[*†‡§])\s*\.?\s+(.*)$")
TERMINAL_RE = re.compile(r"[.!?:”\"')]$")


def _is_bold(fontname: str) -> bool:
    f = fontname.lower()
    return any(k in f for k in ("bold", "black", "heavy", "semibold", "demi"))


def _line_from_chars(chars: list[dict], line_size: float) -> tuple[str, list[str]]:
    """Rebuild a line's text from its characters, turning superscript digits
    into footnote references. Returns (text, footnote ids referenced)."""
    out = ""
    refs: list[str] = []
    prev = None
    sup_buf = ""
    main_bottoms = [c["bottom"] for c in chars if c.get("size", line_size) >= 0.85 * line_size and c["text"].strip()]
    base_bottom = statistics.median(main_bottoms) if main_bottoms else None

    def flush_sup():
        nonlocal out, sup_buf
        if sup_buf:
            refs.append(sup_buf)
            out += f"\x00{sup_buf}\x00"
            sup_buf = ""

    for c in chars:
        t = c["text"]
        size = c.get("size", line_size)
        is_sup = (
            t.isdigit()
            and size < 0.8 * line_size
            and base_bottom is not None
            and c["bottom"] < base_bottom - 0.2 * line_size
            and out.strip() != ""
        )
        if is_sup:
            sup_buf += t
            prev = c
            continue
        flush_sup()
        if prev is not None and t != " " and not out.endswith(" "):
            gap = c["x0"] - prev["x1"]
            if gap > 0.18 * max(size, 1):
                out += " "
        out += t
        prev = c
    flush_sup()
    out = re.sub(r"\s+", " ", out).strip()
    # Glyphs the PDF does not map to text come out as "(cid:123)". At the
    # start of a line they are almost always bullets; elsewhere drop them.
    out = re.sub(r"^(?:\(cid:\d+\))+\s+", "• ", out)
    out = re.sub(r"\(cid:\d+\)", "", out)
    out = escape_inline(out)
    out = re.sub(r"\x00([^\x00]+)\x00", r"[^\1]", out)
    return out, refs


class _Line:
    __slots__ = ("text", "x0", "x1", "top", "bottom", "size", "bold", "page", "refs")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def read_pdf_fallback(path: Path, page_offset: Optional[int] = None) -> tuple[list[RawItem], int, str]:
    """Read a PDF. Returns (items, number of pages, note on page labels)."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise IngestError("The PDF reader (pdfplumber) is not installed.",
                          hint="Run the setup cell (section 0) again.") from e
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        raise IngestError(f"The PDF {path.name} could not be opened ({e}).",
                          hint="Check the file opens in a PDF viewer. If it is password protected, ask the "
                               "publisher for an unprotected copy.") from e

    with pdf:
        n_pages = len(pdf.pages)
        pages_lines: list[list[_Line]] = []
        page_dims: list[tuple[float, float]] = []
        page_tables: list[list] = []
        page_images: list[list[dict]] = []
        size_counter: Counter = Counter()
        text_chars = 0
        for pno, page in enumerate(pdf.pages, 1):
            w, h = float(page.width), float(page.height)
            page_dims.append((w, h))
            try:
                tables = page.find_tables()
            except Exception:
                tables = []
            tboxes = [t.bbox for t in tables]
            page_tables.append(tables)
            imgs = [im for im in page.images
                    if (im["x1"] - im["x0"]) > 40 and (im["bottom"] - im["top"]) > 40]
            page_images.append(imgs)
            lines: list[_Line] = []
            for ln in page.extract_text_lines(layout=False, strip=True, return_chars=True):
                cx = (ln["x0"] + ln["x1"]) / 2
                cy = (ln["top"] + ln["bottom"]) / 2
                if any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in tboxes):
                    continue
                chars = [c for c in ln.get("chars", []) if c.get("text")]
                if not chars:
                    continue
                sizes = [round(c.get("size", 0), 1) for c in chars if c["text"].strip()]
                size = statistics.median(sizes) if sizes else 0
                text, refs = _line_from_chars(chars, size)
                if not text:
                    continue
                bold_chars = sum(1 for c in chars if c["text"].strip() and _is_bold(c.get("fontname", "")))
                bold = bold_chars >= 0.7 * max(1, len(sizes))
                for s in sizes:
                    size_counter[s] += 1
                text_chars += len(text)
                lines.append(_Line(text=text, x0=ln["x0"], x1=ln["x1"], top=ln["top"], bottom=ln["bottom"],
                                   size=size, bold=bold, page=pno, refs=refs))
            lines.sort(key=lambda l: (round(l.top, 0), l.x0))
            pages_lines.append(lines)

        if text_chars < 20 * max(1, n_pages) * 0.1:
            raise IngestError(
                f"{path.name} has almost no text layer; it looks like a scanned book.",
                hint="Use the Docling reader with OCR (set ingest.reader to 'docling' in book.yaml).",
            )
        body = size_counter.most_common(1)[0][0] if size_counter else 10.0

        # Printed page numbers in the margins
        detected: dict[int, str] = {}
        for pno, lines in enumerate(pages_lines, 1):
            h = page_dims[pno - 1][1]
            for ln in lines:
                if (ln.top < 0.1 * h or ln.bottom > 0.9 * h) and is_page_number_text(ln.text):
                    detected[pno] = ln.text.strip().lower() if not ln.text.strip().isdigit() else ln.text.strip()
        labels, label_note = resolve_labels(n_pages, pdf_page_labels(path), detected, page_offset)

        items: list[RawItem] = []
        for pno, lines in enumerate(pages_lines, 1):
            w, h = page_dims[pno - 1]
            label = labels[pno - 1]
            left = min((l.x0 for l in lines), default=0)
            right = max((l.x1 for l in lines), default=w)
            positioned: list[tuple[float, RawItem]] = []

            # Group lines into blocks
            blocks: list[list[_Line]] = []
            for ln in lines:
                if not blocks:
                    blocks.append([ln])
                    continue
                prev = blocks[-1][-1]
                gap = ln.top - prev.bottom
                same_style = abs(ln.size - prev.size) < 0.6 and ln.bold == prev.bold
                indent = ln.x0 > left + 1.2 * body and prev.x0 <= left + 0.5 * body
                prev_short = prev.x1 < right - max(6 * body, 0.25 * (right - left)) and TERMINAL_RE.search(prev.text)
                new_item = BULLET_RE.match(ln.text) or ORDERED_RE.match(ln.text)
                margin_change = (prev.top < 0.1 * h) != (ln.top < 0.1 * h) or (prev.bottom > 0.9 * h) != (ln.bottom > 0.9 * h)
                if gap > 0.55 * max(ln.size, body) or not same_style or indent or prev_short or new_item or margin_change:
                    blocks.append([ln])
                else:
                    blocks[-1].append(ln)

            for blk in blocks:
                text = ""
                for ln in blk:
                    if text.endswith("-") and ln.text[:1].islower():
                        m = re.search(r"(\w+)-$", text)
                        first, _, rest = ln.text.partition(" ")
                        if m:
                            text = text[: m.start()] + join_hyphenated(m.group(1), first, Counter()) + (" " + rest if rest else "")
                        else:
                            text += ln.text
                    else:
                        text = (text + " " + ln.text).strip()
                first = blk[0]
                ratio = first.size / body if body else 1.0
                at_top = first.top < 0.1 * h
                at_bottom = blk[-1].bottom > 0.9 * h
                it = RawItem(kind="para", text=text, page=label, size_ratio=ratio, bold=first.bold,
                             at_top=at_top, at_bottom=at_bottom, physical_page=pno)
                short = len(text) < 150 and len(blk) <= 3
                if (ratio >= 1.12 and short) or (first.bold and short and not TERMINAL_RE.search(text) and ratio >= 0.98):
                    it.kind = "heading"
                elif ratio < 0.92 and first.top > 0.6 * h and FOOTNOTE_START_RE.match(text):
                    m = FOOTNOTE_START_RE.match(text)
                    it.kind, it.note_id, it.text = "footnote", m.group(1), m.group(2)
                elif BULLET_RE.match(text):
                    m = BULLET_RE.match(text)
                    it.kind, it.text, it.marker = "list_item", m.group(2), "-"
                elif ORDERED_RE.match(text) and len(blk) <= 6:
                    m = ORDERED_RE.match(text)
                    it.kind, it.text, it.ordered, it.marker = "list_item", m.group(2), True, m.group(1)
                elif CAPTION_RE.match(text) or TABLE_CAPTION_RE.match(text):
                    it.kind = "caption"
                positioned.append((first.top, it))

            for t in page_tables[pno - 1]:
                try:
                    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in t.extract()]
                except Exception:
                    rows = []
                rows = [r for r in rows if any(r)]
                if rows:
                    positioned.append((t.bbox[1], RawItem(kind="table", rows=[[escape_inline(c) for c in r] for r in rows],
                                                          header_rows=1, page=label, physical_page=pno)))
            for im in page_images[pno - 1]:
                positioned.append((im["top"], RawItem(kind="figure", page=label, physical_page=pno)))

            positioned.sort(key=lambda p: p[0])
            # Attach captions to the nearest figure or table on the page
            seq = [it for _, it in positioned]
            tops = [t for t, _ in positioned]
            for i, it in enumerate(seq):
                if it.kind != "caption":
                    continue
                want = "table" if TABLE_CAPTION_RE.match(it.text) else "figure"
                cands = [j for j, o in enumerate(seq) if o.kind == want and not o.caption]
                if cands:
                    j = min(cands, key=lambda j: abs(tops[j] - tops[i]))
                    seq[j].caption = it.text
                    it.kind = "drop"
                else:
                    # A caption with no picture we could see (vector art):
                    # still make a figure so the caption is read.
                    it.kind, it.caption, it.text = ("figure" if want == "figure" else "para"), it.text, it.text if want != "figure" else ""
            items.extend(it for it in seq if it.kind != "drop")

    return items, n_pages, label_note
