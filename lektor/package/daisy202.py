"""DAISY 2.02 full text and full audio, as one ZIP.

Layout of the ZIP (everything at the top level; names are short, lower
case ASCII and never need renaming)::

    ncc.html          navigation control centre: title, every heading, every print page
    c0001.html ...    content (the text), one XHTML file per chapter
    s0001.smil ...    synchronisation, one SMIL 1.0 file per chapter
    a0001.mp3 ...     audio, one MP3 per chapter (mono)
    lektor.css        a small stylesheet for the text

Every sentence (or sentence part) is one ``par`` in the SMIL file, linking
its text (``<span id>`` in the content file) to its audio clip
(``clip-begin``/``clip-end`` in ``npt=`` form). Clips are contiguous: each
one runs to the start of the next, so the pauses between sentences are
played. The NCC lists the title, every heading (h1 to h6, the book's own
levels) and every print page (``page-normal``, ``page-front`` for roman
numerals, ``page-special`` for anything else), each linking to the par
where it starts.

Everything is generated from the timing map (``audio/timing.json``), the
same source as the M4B chapters. The format follows the DAISY 2.02
specification as encoded in the DAISY Pipeline 2 validation schemas, which
:mod:`lektor.package.daisy_check` uses to check the result.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import __version__
from ..config import BookConfig, BookPaths
from .common import BookMeta
from .ffmpeg import run_ffmpeg

XHTML_NS = "http://www.w3.org/1999/xhtml"
ROMAN_RE = re.compile(r"^(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
DAISY_MP3_RATE = 44100  # MPEG-1 Layer III: the most widely supported by DAISY players

CSS = """body { font-family: Verdana, Arial, sans-serif; line-height: 1.5; margin: 1em; }
.lektor-added { font-style: italic; }
.page-normal, .page-front, .page-special { display: block; text-align: right; font-size: 0.8em; color: #555; }
.frontmatter { border-bottom: 1px solid #999; margin-bottom: 1em; }
.footnote, .note { margin-left: 1.5em; font-size: 0.95em; }
"""


def e(text: str) -> str:
    return html.escape(str(text), quote=True)


def clock(seconds: float, fraction: bool = False) -> str:
    """h:mm:ss (or h:mm:ss.sss) as used by ncc:totalTime and the SMIL meta."""
    seconds = max(0.0, seconds)
    if fraction:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h)}:{int(m):02d}:{s:06.3f}"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def npt(seconds: float) -> str:
    return f"npt={max(0.0, seconds):.3f}s"


def page_class(label: str) -> str:
    if label.isdigit() and int(label) > 0:
        return "page-normal"
    if ROMAN_RE.match(label):
        return "page-front"
    return "page-special"


@dataclass
class Fragment:
    """One sentence (or sentence part) of one chapter, with its clip."""

    fid: str  # base for span / par / text / audio ids
    display: str
    begin: float
    end: float
    unit: dict
    pages: list[str] = field(default_factory=list)


@dataclass
class NccItem:
    kind: str  # "title", "heading", "page"
    level: int
    text: str
    href: str
    cls: str = ""


@dataclass
class DaisyBook:
    folder: Path
    files: list[str]
    ncc_items: int
    pages: dict[str, int]
    total_seconds: float


def _fragments(ch: dict, duration: float) -> list[Fragment]:
    frags: list[Fragment] = []
    for u in ch["units"]:
        for s in u["sentences"]:
            fid = s["sid"] + (f"_{s['part']}" if s.get("part") else "")
            frags.append(Fragment(fid, s["display"], s["start"], s["end"], u, list(s.get("pages", []))))
    # Contiguous clips: the first starts at 0, each runs to the next one's
    # start, the last to the end of the file, so the pauses are played too.
    starts = [0.0] + [f.begin for f in frags[1:]]
    ends = starts[1:] + [duration]
    for f, b, en in zip(frags, starts, ends):
        f.begin, f.end = b, en
    return frags


def _content_doc(n: int, ch: dict, frags: list[Fragment], meta: BookMeta, smil: str) -> str:
    lines = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">',
        f'<html xmlns="{XHTML_NS}" xml:lang="{e(meta.language)}" lang="{e(meta.language)}">',
        "<head>",
        '<meta http-equiv="Content-Type" content="application/xhtml+xml; charset=utf-8" />',
        f"<title>{e(ch['title'])}</title>",
        f'<meta name="dc:title" content="{e(meta.title)}" />',
        f'<meta name="dc:identifier" content="{e(meta.identifier)}" />',
        '<link rel="stylesheet" type="text/css" href="lektor.css" />',
        "</head>",
        "<body>",
    ]
    by_unit: dict[str, list[Fragment]] = {}
    order: list[dict] = []
    for f in frags:
        if f.unit["id"] not in by_unit:
            by_unit[f.unit["id"]] = []
            order.append(f.unit)
        by_unit[f.unit["id"]].append(f)

    def spans(fs: list[Fragment]) -> str:
        out = []
        for f in fs:
            for p in f.pages:
                out.append(f'<span class="{page_class(p)}" id="pg_{e(f.fid)}_{e(re.sub(r"[^A-Za-z0-9]", "", p))}">'
                           f"{e(p)}</span>")
            out.append(f'<span class="sentence" id="t{e(f.fid)}"><a href="{smil}#p{e(f.fid)}">{e(f.display)}</a></span>')
        return " ".join(out)

    in_list = False
    for u in order:
        fs = by_unit[u["id"]]
        kind = u["kind"]
        if kind != "list_item" and in_list:
            lines.append("</ul>")
            in_list = False
        added = " lektor-added" if u["role"] == "secondary" else ""
        if kind == "heading":
            level = min(6, max(1, u.get("heading_level") or 1))
            # Page markers cannot sit inside a heading: put them just before it.
            pages = "".join(f'<span class="{page_class(p)}" id="pg_{e(f.fid)}_{e(re.sub(r"[^A-Za-z0-9]", "", p))}">'
                            f"{e(p)}</span>" for f in fs for p in f.pages)
            if pages:
                lines.append(f"<div>{pages}</div>")
            inner = " ".join(f'<span class="sentence" id="t{e(f.fid)}"><a href="{smil}#p{e(f.fid)}">{e(f.display)}'
                             f"</a></span>" for f in fs)
            lines.append(f'<h{level} id="h{e(u["id"])}">{inner}</h{level}>')
        elif kind == "list_item":
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{spans(fs)}</li>")
        elif kind == "frontmatter":
            lines.append(f'<div class="frontmatter{added}"><p>{spans(fs)}</p></div>')
        elif kind in ("figure", "table", "note", "footnote"):
            lines.append(f'<div class="{kind}{added}"><p>{spans(fs)}</p></div>')
        else:
            lines.append(f'<p class="{e(kind)}{added}">{spans(fs)}</p>')
    if in_list:
        lines.append("</ul>")
    lines += ["</body>", "</html>"]
    return "\n".join(lines) + "\n"


def _smil_doc(n: int, ch: dict, frags: list[Fragment], meta: BookMeta, content: str, audio: str,
              elapsed: float, duration: float) -> str:
    lines = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<!DOCTYPE smil PUBLIC "-//W3C//DTD SMIL 1.0//EN" "http://www.w3.org/TR/REC-SMIL/SMIL10.dtd">',
        "<smil>",
        "<head>",
        f'<meta name="ncc:generator" content="Lektor {e(__version__)}" />',
        '<meta name="dc:format" content="Daisy 2.02" />',
        f'<meta name="dc:identifier" content="{e(meta.identifier)}" />',
        f'<meta name="dc:title" content="{e(meta.title)}" />',
        f'<meta name="title" content="{e(ch["title"])}" />',
        f'<meta name="ncc:totalElapsedTime" content="{clock(elapsed, fraction=True)}" />',
        f'<meta name="ncc:timeInThisSmil" content="{clock(duration, fraction=True)}" />',
        "<layout>",
        '<region id="txtView" />',
        "</layout>",
        "</head>",
        "<body>",
        f'<seq dur="{duration:.3f}s">',
    ]
    for f in frags:
        lines.append(f'<par endsync="last" id="p{e(f.fid)}">')
        lines.append(f'<text id="x{e(f.fid)}" src="{content}#t{e(f.fid)}" />')
        lines.append(f'<audio id="a{e(f.fid)}" src="{audio}" clip-begin="{npt(f.begin)}" clip-end="{npt(f.end)}" />')
        lines.append("</par>")
    lines += ["</seq>", "</body>", "</smil>"]
    return "\n".join(lines) + "\n"


def _ncc_doc(items: list[NccItem], meta: BookMeta, cfg: BookConfig, total: float, n_files: int,
             kbytes: int, depth: int, pages: dict[str, int], max_page: int) -> str:
    today = _dt.date.today().isoformat()
    metas = [
        ("dc:title", meta.title),
        ("dc:creator", meta.author or "Unknown"),
        ("dc:date", today),
        ("dc:format", "Daisy 2.02"),
        ("dc:identifier", meta.identifier),
        ("dc:language", meta.language),
        ("dc:publisher", "University of West London Library"),
        ("dc:description", f"{meta.accessibility} {meta.synthetic_voice}"),
        ("dc:rights", meta.acknowledgement),
        ("ncc:charset", "utf-8"),
        ("ncc:depth", str(depth)),
        ("ncc:files", str(n_files)),
        ("ncc:footnotes", "0"),
        ("ncc:generator", meta.generator),
        ("ncc:kByteSize", str(kbytes)),
        ("ncc:maxPageNormal", str(max_page)),
        ("ncc:multimediaType", "audioFullText"),
        ("ncc:narrator", meta.narrator),
        ("ncc:pageFront", str(pages["page-front"])),
        ("ncc:pageNormal", str(pages["page-normal"])),
        ("ncc:pageSpecial", str(pages["page-special"])),
        ("ncc:prodNotes", "0"),
        ("ncc:producedDate", today),
        ("ncc:producer", "University of West London Library"),
        ("ncc:setInfo", "1 of 1"),
        ("ncc:sidebars", "0"),
        ("ncc:tocItems", str(len(items))),
        ("ncc:totalTime", clock(total)),
    ]
    if meta.publisher:
        metas.append(("ncc:sourcePublisher", meta.publisher))
    if meta.year:
        metas.append(("ncc:sourceDate", meta.year))
    if cfg.isbn:
        metas.append(("dc:source", cfg.isbn))
    lines = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">',
        f'<html xmlns="{XHTML_NS}" xml:lang="{e(meta.language)}" lang="{e(meta.language)}">',
        "<head>",
        '<meta http-equiv="Content-type" content="application/xhtml+xml; charset=utf-8" />',
        f"<title>{e(meta.title)}</title>",
    ]
    lines += [f'<meta name="{k}" content="{e(v)}" />' for k, v in metas]
    lines += ["</head>", "<body>"]
    for i, it in enumerate(items, 1):
        if it.kind == "title":
            lines.append(f'<h1 class="title" id="ncc{i:05d}"><a href="{it.href}">{e(it.text)}</a></h1>')
        elif it.kind == "heading":
            lines.append(f'<h{it.level} id="ncc{i:05d}"><a href="{it.href}">{e(it.text)}</a></h{it.level}>')
        else:
            lines.append(f'<span class="{it.cls}" id="ncc{i:05d}"><a href="{it.href}">{e(it.text)}</a></span>')
    lines += ["</body>", "</html>"]
    return "\n".join(lines) + "\n"


def build_daisy(paths: BookPaths, cfg: BookConfig, timing: dict, meta: BookMeta, out_dir: Path,
                bitrate_kbps: int = 64, log: Callable[[str], None] = print) -> DaisyBook:
    """Write the DAISY 2.02 files into ``out_dir`` (which must be empty)."""
    from ..assemble import audio_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    adir = audio_dir(paths)
    items: list[NccItem] = []
    pages = {"page-normal": 0, "page-front": 0, "page-special": 0}
    max_page = 0
    elapsed = 0.0
    files = ["ncc.html", "lektor.css"]
    (out_dir / "lektor.css").write_text(CSS, encoding="utf-8")
    prev_level = 0
    for n, ch in enumerate(timing["chapters"], 1):
        content, smil, audio = f"c{n:04d}.html", f"s{n:04d}.smil", f"a{n:04d}.mp3"
        duration = float(ch["duration"])
        frags = _fragments(ch, duration)
        if not frags:
            continue
        # NCC entries for this chapter
        chapter_items: list[NccItem] = []
        for f in frags:
            u = f.unit
            first_of_unit = f is next(x for x in frags if x.unit["id"] == u["id"])
            if n == 1 and not items and not chapter_items:
                chapter_items.append(NccItem("title", 1, meta.title, f"{smil}#p{f.fid}"))
            elif u["kind"] == "heading" and first_of_unit:
                level = min(6, max(1, u.get("heading_level") or 1))
                level = min(level, prev_level + 1) if prev_level else 1
                text = " ".join(x.display for x in frags if x.unit["id"] == u["id"])
                chapter_items.append(NccItem("heading", level, text, f"{smil}#p{f.fid}"))
                prev_level = level
            for p in f.pages:
                cls = page_class(p)
                pages[cls] += 1
                if cls == "page-normal":
                    max_page = max(max_page, int(p))
                chapter_items.append(NccItem("page", 0, p, f"{smil}#p{f.fid}", cls))
        # Every SMIL must be reachable from the NCC (DAISY 2.02 has no spine):
        # a chapter without a heading of its own gets one from its title.
        if not any(it.kind in ("heading", "title") for it in chapter_items):
            chapter_items.insert(0, NccItem("heading", 1, ch["title"], f"{smil}#p{frags[0].fid}"))
            prev_level = 1
        items.extend(chapter_items)
        (out_dir / content).write_text(_content_doc(n, ch, frags, meta, smil), encoding="utf-8")
        (out_dir / smil).write_text(_smil_doc(n, ch, frags, meta, content, audio, elapsed, duration), encoding="utf-8")
        run_ffmpeg(["-i", str(adir / ch["file"]), "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k", "-ac", "1",
                    "-ar", str(DAISY_MP3_RATE), "-id3v2_version", "3", "-metadata", f"title={ch['title']}",
                    "-metadata", f"album={meta.title}", "-metadata", f"artist={meta.author}",
                    "-metadata", f"track={n}", str(out_dir / audio)])
        files += [content, smil, audio]
        elapsed += duration
        log(f"  DAISY: chapter {n} of {len(timing['chapters'])}")
    depth = max([it.level for it in items if it.kind in ("heading", "title")] or [1])
    kbytes = sum((out_dir / f).stat().st_size for f in files if (out_dir / f).exists()) // 1024
    (out_dir / "ncc.html").write_text(_ncc_doc(items, meta, cfg, elapsed, len(files), kbytes, depth, pages, max_page),
                                      encoding="utf-8")
    return DaisyBook(out_dir, files, len(items), pages, elapsed)


def zip_daisy(book: DaisyBook, out: Path) -> Path:
    """One book per ZIP; files at the top level, ncc.html first."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "daisy.zip"
        with zipfile.ZipFile(tmp, "w") as z:
            for name in book.files:
                compress = zipfile.ZIP_STORED if name.endswith(".mp3") else zipfile.ZIP_DEFLATED
                z.write(book.folder / name, name, compress_type=compress)
        part = out.with_suffix(".zip.part")
        shutil.copyfile(tmp, part)
        part.replace(out)
    return out


def write_daisy(paths: BookPaths, cfg: BookConfig, timing: dict, meta: BookMeta, out: Path,
                log: Callable[[str], None] = print) -> Path:
    """Build, check and zip the DAISY book. Raises if the checks find errors."""
    from ..errors import LektorError
    from .daisy_check import check_daisy

    with tempfile.TemporaryDirectory() as td:
        book = build_daisy(paths, cfg, timing, meta, Path(td) / "daisy", cfg.audio.mp3_bitrate_kbps, log)
        report = check_daisy(book.folder, log=log)
        report_path = paths.output_dir / "daisy_check.txt"
        paths.output_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.text(), encoding="utf-8")
        if report.errors:
            raise LektorError(f"The DAISY book failed {len(report.errors)} check(s), for example: {report.errors[0]}",
                              hint=f"Details are in {report_path.name} in the output folder. Please send it to the "
                                   "developer.")
        for w in report.warnings[:5]:
            log(f"  DAISY check warning: {w}")
        return zip_daisy(book, out)
