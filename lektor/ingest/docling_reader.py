"""Docling reader for PDF, DOCX, PPTX and HTML.

Docling is heavy (it brings layout and table models), so Lektor normally
runs it in its own virtual environment (see :mod:`lektor.envs`). This
module therefore has two halves:

- **worker side** (``python -m lektor.ingest.docling_reader SRC OUT.json``):
  runs inside the Docling environment, converts the document and writes a
  small neutral JSON file (no Docling types in it);
- **main side** (:func:`neutral_to_items`): turns that JSON into
  :class:`~lektor.ingest.common.RawItem` objects with pure Python.

The neutral format keeps the main process independent of Docling's
internal data model, which changes often.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

NEUTRAL_VERSION = 1


# ---------------------------------------------------------------------------
# Worker side (imports Docling)
# ---------------------------------------------------------------------------
def convert(src: Path, ocr: str = "auto"):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.do_ocr = ocr != "off"
    if ocr == "on":
        try:
            opts.ocr_options.force_full_page_ocr = True
        except Exception:  # pragma: no cover - depends on Docling version
            pass
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return converter.convert(str(src)).document


def docling_to_neutral(doc) -> dict:
    """Flatten a DoclingDocument into Lektor's neutral JSON structure."""
    from docling_core.types.doc import ContentLayer, DocItemLabel

    try:
        layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
        iterator = doc.iterate_items(included_content_layers=layers)
    except TypeError:  # older docling-core
        iterator = doc.iterate_items()

    page_heights = {}
    for no, page in (getattr(doc, "pages", None) or {}).items():
        try:
            page_heights[int(no)] = float(page.size.height)
        except Exception:
            pass

    caption_refs = set()
    for item, _ in doc.iterate_items():
        for ref in list(getattr(item, "captions", []) or []) + list(getattr(item, "footnotes", []) or []):
            caption_refs.add(getattr(ref, "cref", None))

    out = []
    for item, _level in iterator:
        label = getattr(item, "label", None)
        label = getattr(label, "value", label)
        if label is None:
            continue
        if getattr(item, "self_ref", None) in caption_refs:
            continue
        prov = (getattr(item, "prov", None) or [None])[0]
        rec: dict = {"label": label}
        if prov is not None:
            rec["page"] = int(prov.page_no)
            h = page_heights.get(int(prov.page_no))
            try:
                bb = prov.bbox.to_top_left_origin(page_height=h) if h else prov.bbox
                rec["top"] = float(bb.t)
                rec["bottom"] = float(bb.b)
                if h:
                    rec["page_height"] = h
            except Exception:
                pass
        layer = getattr(item, "content_layer", None)
        rec["furniture"] = getattr(layer, "value", str(layer)) == "furniture"
        text = getattr(item, "text", "") or ""
        rec["text"] = text
        if label == DocItemLabel.SECTION_HEADER.value:
            rec["level"] = int(getattr(item, "level", 1) or 1)
        if label == DocItemLabel.LIST_ITEM.value:
            rec["ordered"] = bool(getattr(item, "enumerated", False))
            rec["marker"] = getattr(item, "marker", "") or ""
        if label in (DocItemLabel.PICTURE.value, DocItemLabel.CHART.value):
            try:
                rec["caption"] = item.caption_text(doc) or ""
            except Exception:
                rec["caption"] = ""
            desc = ""
            for ann in getattr(item, "annotations", []) or []:
                if getattr(ann, "kind", "") == "description" and getattr(ann, "text", ""):
                    desc = ann.text
            rec["description"] = desc
        if label in (DocItemLabel.TABLE.value, DocItemLabel.DOCUMENT_INDEX.value):
            try:
                rec["caption"] = item.caption_text(doc) or ""
            except Exception:
                rec["caption"] = ""
            rows, header_rows = [], 0
            try:
                for r, row in enumerate(item.data.grid):
                    cells = [(c.text or "").strip() for c in row]
                    if r == header_rows and row and all(getattr(c, "column_header", False) for c in row):
                        header_rows += 1
                    rows.append(cells)
            except Exception:
                pass
            # Merged cells repeat in Docling's grid; collapse exact repeats.
            rows = [[c for k, c in enumerate(r) if k == 0 or c != r[k - 1] or not c] for r in rows]
            rec["rows"] = rows
            rec["header_rows"] = header_rows
        out.append(rec)
    return {"lektor_neutral": NEUTRAL_VERSION, "num_pages": len(page_heights), "items": out}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Convert a document with Docling into Lektor's neutral JSON.")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--ocr", default="auto", choices=["auto", "on", "off"])
    args = ap.parse_args(argv)
    doc = convert(Path(args.src), args.ocr)
    data = docling_to_neutral(doc)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"docling: {len(data['items'])} items from {data['num_pages']} pages")
    return 0


# ---------------------------------------------------------------------------
# Main side (pure Python)
# ---------------------------------------------------------------------------
def detected_page_numbers(neutral: dict) -> dict[int, str]:
    from .common import is_page_number_text

    found: dict[int, str] = {}
    for rec in neutral.get("items", []):
        if rec.get("label") in ("page_header", "page_footer") or rec.get("furniture"):
            t = (rec.get("text") or "").strip()
            if rec.get("page") and is_page_number_text(t):
                found[int(rec["page"])] = t if t.isdigit() else t.lower()
    return found


def neutral_to_items(neutral: dict, labels: list[Optional[str]]):
    from ..script.model import escape_inline
    from .common import RawItem

    def lab(rec):
        p = rec.get("page")
        if p and 1 <= p <= len(labels):
            return labels[p - 1]
        return None

    items: list[RawItem] = []
    for rec in neutral.get("items", []):
        label = rec.get("label")
        text = escape_inline(re.sub(r"\s+", " ", rec.get("text") or "").strip())
        h = rec.get("page_height")
        at_top = bool(h and rec.get("top") is not None and rec["top"] < 0.1 * h)
        at_bottom = bool(h and rec.get("bottom") is not None and rec["bottom"] > 0.9 * h)
        common = dict(page=lab(rec), physical_page=rec.get("page"), at_top=at_top, at_bottom=at_bottom)
        if label in ("page_header", "page_footer") or (rec.get("furniture") and label not in ("picture",)):
            items.append(RawItem(kind="furniture", text=text, **common))
        elif label == "title":
            items.append(RawItem(kind="heading", text=text, level=1, size_ratio=2.0, **common))
        elif label == "section_header":
            items.append(RawItem(kind="heading", text=text, level=rec.get("level", 1), **common))
        elif label == "list_item":
            ordered = bool(rec.get("ordered"))
            marker = rec.get("marker") or ""
            text = re.sub(r"^[•▪◦●○■□–‣⁃·]\s*", "", text)
            items.append(RawItem(kind="list_item", text=text, ordered=ordered,
                                 marker=marker if ordered and re.fullmatch(r"\d{1,4}[.)]", marker) else
                                 ("1." if ordered else "-"), **common))
        elif label == "footnote":
            m = re.match(r"^\s*(\d{1,3}|[*†‡§])\s*\.?\s+(.*)$", text)
            nid, body = (m.group(1), m.group(2)) if m else (f"n{len(items)}", text)
            if nid in "*†‡§":
                nid = f"s{len(items)}"
            items.append(RawItem(kind="footnote", note_id=nid, text=body, **common))
        elif label in ("picture", "chart"):
            items.append(RawItem(kind="figure", caption=escape_inline(rec.get("caption", "")),
                                 text=escape_inline(rec.get("description", "")), **common))
        elif label in ("table", "document_index"):
            rows = [[escape_inline(c) for c in r] for r in rec.get("rows", [])]
            if label == "document_index":
                # A table of contents laid out as a table: keep as text.
                items.append(RawItem(kind="para", text=" ".join(" ".join(r) for r in rows), **common))
            elif rows:
                items.append(RawItem(kind="table", rows=rows, header_rows=rec.get("header_rows", 1),
                                     caption=escape_inline(rec.get("caption", "")), **common))
        elif label == "caption":
            items.append(RawItem(kind="figure", caption=text, **common))
        elif label in ("formula",):
            if text:
                items.append(RawItem(kind="para", text=text, **common))
        elif text:
            items.append(RawItem(kind="para", text=text, **common))
    return items


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
