"""Print page labels for PDFs.

Order of preference:

1. ``page_offset`` in book.yaml (printed = physical - offset), if set
2. page labels stored in the PDF (``/PageLabels``)
3. printed page numbers found in the page margins by the reader
4. no labels (pages are then not marked)
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional


def to_roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def pdf_page_labels(path: Path) -> Optional[list[str]]:
    """Labels from the PDF's /PageLabels, or None if it defines none."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        root = reader.trailer["/Root"]
        if "/PageLabels" not in root:
            return None
        return list(reader.page_labels)
    except Exception:
        return None


def labels_from_detected(n_pages: int, detected: dict[int, str]) -> Optional[list[Optional[str]]]:
    """Infer labels for every page from printed numbers seen on some pages.

    ``detected`` maps physical page (1-based) to the number printed on it.
    Uses the most common offset between physical and printed arabic numbers.
    Pages before the first arabic page get roman numerals if roman numbers
    were seen there, otherwise no label.
    """
    offsets = Counter(p - int(v) for p, v in detected.items() if v.isdigit())
    if not offsets:
        return None
    offset, hits = offsets.most_common(1)[0]
    arabic_pages = sum(1 for v in detected.values() if v.isdigit())
    if hits < max(2, 0.5 * arabic_pages):
        return None
    labels: list[Optional[str]] = []
    romans = {p: v for p, v in detected.items() if not v.isdigit()}
    roman_offsets = Counter()
    from .common import ROMAN_RE

    for p, v in romans.items():
        if ROMAN_RE.match(v):
            roman_offsets[p - _roman_value(v)] += 1
    r_off = roman_offsets.most_common(1)[0][0] if roman_offsets else None
    for p in range(1, n_pages + 1):
        n = p - offset
        if n >= 1:
            labels.append(str(n))
        elif r_off is not None and p - r_off >= 1:
            labels.append(to_roman(p - r_off))
        else:
            labels.append(None)
    return labels


def _roman_value(s: str) -> int:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = s.lower()
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        if i + 1 < len(s) and vals[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return total


def resolve_labels(
    n_pages: int,
    pdf_labels: Optional[list[str]],
    detected: dict[int, str],
    page_offset: Optional[int] = None,
) -> tuple[list[Optional[str]], str]:
    """Return (labels for physical pages 1..n, a note saying where they came from)."""
    if page_offset is not None:
        return ([str(p - page_offset) if p - page_offset >= 1 else None for p in range(1, n_pages + 1)],
                f"page_offset {page_offset} from book.yaml")
    if pdf_labels and len(pdf_labels) == n_pages:
        return list(pdf_labels), "page labels stored in the PDF"
    inferred = labels_from_detected(n_pages, detected)
    if inferred:
        return inferred, "printed page numbers found on the pages"
    return [None] * n_pages, "none found (print page navigation will not be available)"
