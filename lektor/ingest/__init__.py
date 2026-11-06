"""Ingest: turn a source document into ``script/script.md`` and ``review.html``.

Choosing a reader (``ingest.reader`` in book.yaml):

========  ==========================================================
auto      PDF, DOCX, PPTX, HTML: Docling if it can run here, else the
          fallback reader. EPUB: Lektor's EPUB reader. TXT, MD: direct.
docling   always Docling (in its own environment on Colab)
fallback  never Docling
========  ==========================================================

Protecting the librarian's edits: after writing the script, Lektor records
a fingerprint of it. If the script has been edited since, running ingest
again does **not** overwrite it; the fresh conversion is written next to it
as ``script.new.md`` instead. Pass ``force=True`` to replace it (the old
version is kept as a dated backup).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import importlib.util
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..config import BookConfig, BookPaths, append_log
from ..errors import IngestError
from ..script.writer import save_script
from .common import RawItem, build_script

DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".htm", ".xhtml"}
SUPPORTED = DOCLING_SUFFIXES | {".epub", ".txt", ".md", ".markdown"}


@dataclass
class IngestResult:
    script_path: Path
    review_path: Path
    reader: str
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    wrote_new_copy: bool = False


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state_path(paths: BookPaths) -> Path:
    return paths.script_dir / ".ingest-state.json"


def script_was_edited(paths: BookPaths) -> bool:
    if not paths.script.exists():
        return False
    try:
        state = json.loads(_state_path(paths).read_text())
    except Exception:
        return True  # unknown origin: treat as edited, to be safe
    return state.get("fingerprint") != _fingerprint(paths.script)


def docling_in_process() -> bool:
    return importlib.util.find_spec("docling") is not None


def _read_with_docling(src: Path, cfg: BookConfig, log: Callable[[str], None]) -> dict:
    from .. import envs
    from .docling_reader import convert, docling_to_neutral

    if docling_in_process():
        return docling_to_neutral(convert(src, cfg.ingest.ocr))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "neutral.json"
        proc = envs.run_module("docling", "lektor.ingest.docling_reader", [str(src), str(out), "--ocr", cfg.ingest.ocr], log)
        if proc.returncode != 0 or not out.exists():
            tail = "\n".join(proc.stdout.strip().splitlines()[-15:])
            raise IngestError(f"Docling could not convert {src.name}.\n{tail}")
        return json.loads(out.read_text(encoding="utf-8"))


def read_source(src: Path, cfg: BookConfig, log: Callable[[str], None] = print
                ) -> tuple[list[RawItem], int, dict[str, str], str, list[str]]:
    """Run the right reader. Returns (items, n_pages, metadata found, reader name, notes)."""
    suffix = src.suffix.lower()
    if suffix not in SUPPORTED:
        raise IngestError(f"Lektor cannot read {suffix or 'this kind of'} files yet ({src.name}).",
                          hint="Supported: PDF, EPUB, DOCX, PPTX, HTML, TXT and Markdown.")
    notes: list[str] = []
    mode = cfg.ingest.reader
    from .. import envs

    want_docling = suffix in DOCLING_SUFFIXES and (
        mode == "docling" or (mode == "auto" and (docling_in_process() or envs.in_colab()))
    )
    if suffix in (".pptx",) and not want_docling:
        raise IngestError("PowerPoint files need the Docling reader.", hint="Set ingest.reader to 'docling'.")

    if want_docling:
        try:
            neutral = _read_with_docling(src, cfg, log)
            from .docling_reader import detected_page_numbers, neutral_to_items
            from .pages import pdf_page_labels, resolve_labels

            n_pages = neutral.get("num_pages", 0)
            labels: list = []
            if suffix == ".pdf":
                labels, note = resolve_labels(n_pages, pdf_page_labels(src), detected_page_numbers(neutral),
                                              cfg.structure.page_offset)
                notes.append(f"Print page numbers: {note}.")
            return neutral_to_items(neutral, labels), n_pages, {}, "docling", notes
        except Exception as e:
            if mode == "docling" or suffix == ".pptx":
                raise
            notes.append(f"Docling failed ({e}); the fallback reader was used instead. Check the review carefully.")
            log(notes[-1])

    if suffix == ".pdf":
        from .pdf_fallback import read_pdf_fallback

        items, n_pages, note = read_pdf_fallback(src, cfg.structure.page_offset)
        notes.append(f"Print page numbers: {note}.")
        meta = {}
        try:
            from pypdf import PdfReader

            info = PdfReader(str(src)).metadata or {}
            if info.get("/Title"):
                meta["title"] = str(info["/Title"])
            if info.get("/Author"):
                meta["author"] = str(info["/Author"])
        except Exception:
            pass
        return items, n_pages, meta, "pdf-fallback", notes
    if suffix == ".epub":
        from .epub_reader import read_epub

        items, meta = read_epub(src)
        if not any(i.kind == "page" for i in items) and not any("[[page" in i.text for i in items):
            notes.append("This EPUB has no page break markers, so print page navigation will not be available.")
        return items, 0, meta, "epub", notes
    if suffix == ".docx":
        from .simple_readers import read_docx_fallback

        items, meta = read_docx_fallback(src)
        return items, 0, meta, "docx-fallback", notes
    if suffix in (".html", ".htm", ".xhtml"):
        from .simple_readers import read_html_fallback

        items, meta = read_html_fallback(src)
        return items, 0, meta, "html-fallback", notes
    if suffix == ".txt":
        from .simple_readers import read_txt

        return read_txt(src), 0, {}, "text", notes
    from .simple_readers import read_markdown

    return read_markdown(src), 0, {}, "markdown", notes


def run_ingest(paths: BookPaths, cfg: BookConfig, force: bool = False, log: Callable[[str], None] = print
               ) -> IngestResult:
    paths.ensure()
    src = paths.resolve_source(cfg)
    log(f"Reading {src.name} ...")
    items, n_pages, found_meta, reader, notes = read_source(src, cfg, log)
    if not items:
        raise IngestError(f"No text could be found in {src.name}.",
                          hint="If it is a scanned book, set ingest.reader to 'docling' and ingest.ocr to 'on'.")
    meta = {
        "book_id": cfg.book_id,
        "title": cfg.title or found_meta.get("title", ""),
        "author": cfg.author or found_meta.get("author", ""),
        "source": src.name,
        "reader": reader,
        "created": _dt.date.today().isoformat(),
    }
    chapter_level = cfg.structure.chapter_level if isinstance(cfg.structure.chapter_level, int) else None
    script, stats = build_script(
        items,
        meta=meta,
        n_pages=n_pages,
        heading_rules=[(r.pattern, r.level) for r in cfg.structure.heading_rules],
        skip_sections=cfg.structure.skip_sections,
        chapter_level=chapter_level,
    )
    stats["pages"] = n_pages

    target = paths.script
    wrote_new = False
    if paths.script.exists() and script_was_edited(paths):
        if force:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = paths.script_dir / f"script.backup-{stamp}.md"
            paths.script.replace(backup)
            notes.append(f"Your edited script was kept as {backup.name} and replaced by a fresh conversion.")
        else:
            target = paths.script_dir / "script.new.md"
            wrote_new = True
            notes.append("script.md has been edited, so it was NOT replaced. The fresh conversion is in "
                         "script.new.md. Copy across anything you need, or run again with 'replace' ticked.")
    save_script(script, target)
    if target == paths.script:
        _state_path(paths).write_text(json.dumps({"fingerprint": _fingerprint(target), "reader": reader}))

    from ..script.review import write_review

    review_path = write_review(target, paths.review, cfg, notes=notes, stats=stats)
    append_log(paths, "ingest", f"{src.name} read with {reader}; {stats.get('headings', 0)} headings, "
                                f"{stats.get('chapters', 0)} chapters, {stats.get('figures', 0)} figures; "
                                f"wrote {target.name}")
    return IngestResult(target, review_path, reader, stats, notes, wrote_new)
