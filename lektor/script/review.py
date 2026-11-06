"""The review report (``script/review.html``).

A single self-contained HTML page for the librarian: summary statistics,
the heading outline, problems found by the validator, and a list of things
worth a human look, each with the line number in ``script.md``.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from .model import (
    INLINE_PAGE_RE,
    Figure,
    Footnote,
    Heading,
    ListBlock,
    Note,
    PageMarker,
    Paragraph,
    Script,
    Skip,
    Table,
    block_text,
    plain_text,
)
from .validator import validate_file

WORDS_PER_MINUTE = 155
ODD_CHARS_RE = re.compile(r"[�\u0000-\u0008\u000b\u000c\u000e-\u001f-ﬀ-ﬆ­]|\(cid:\d+\)")
HYPHEN_RE = re.compile(r"\b\w{2,}- [a-z]{2,}\b|\w-$")
NUMBERED_RE = re.compile(r"^\s*(?:chapter|strategy)?\s*(\d{1,3})\b", re.I)


def _e(s) -> str:
    return html.escape(str(s), quote=True)


class Finding:
    def __init__(self, line: int, category: str, message: str, excerpt: str = "") -> None:
        self.line, self.category, self.message, self.excerpt = line, category, message, excerpt


def find_things_to_check(script: Script, chapter_level: int, max_table_cells: int = 40) -> list[Finding]:
    out: list[Finding] = []
    paras = [b for b in script.blocks if isinstance(b, Paragraph)]

    # Leftover headers or footers: short lines repeated several times.
    short_counts = Counter(re.sub(r"\d+", "#", plain_text(p.text).lower()) for p in paras if len(plain_text(p.text)) < 60)
    for p in paras:
        t = plain_text(p.text)
        key = re.sub(r"\d+", "#", t.lower())
        if len(t) < 60 and short_counts[key] >= 3:
            out.append(Finding(p.line, "Possible header or footer",
                               f"this short line appears {short_counts[key]} times; it may be a running header "
                               "or footer. Delete it if so.", t))
        elif re.fullmatch(r"(page\s*)?[\divxlcdm]{1,6}", t, re.I):
            out.append(Finding(p.line, "Possible header or footer",
                               "this paragraph is just a number; it may be a printed page number.", t))

    for b in script.blocks:
        texts = []
        if isinstance(b, (Paragraph, Heading, Note, Footnote)):
            texts = [b.text]
        elif isinstance(b, ListBlock):
            texts = [i.text for i in b.items]
        elif isinstance(b, Figure):
            texts = [b.caption, b.description]
        elif isinstance(b, Table):
            texts = [" ".join(r) for r in b.rows]
        for t in texts:
            pt = plain_text(t)
            m = HYPHEN_RE.search(pt)
            if m:
                out.append(Finding(b.line, "Broken hyphenation",
                                   f"'{m.group(0)}' may be a word split at a line end. Join it if so.", pt[:160]))
            m = ODD_CHARS_RE.search(t)
            if m:
                code = f"U+{ord(m.group(0)[0]):04X}" if len(m.group(0)) == 1 else m.group(0)
                out.append(Finding(b.line, "Odd character",
                                   f"contains an unusual character ({code}) that may be misread by the voice.", pt[:160]))

    for p in paras:
        t = plain_text(p.text)
        if 0 < len(t) < 25 and not re.fullmatch(r"(page\s*)?[\divxlcdm]{1,6}", t, re.I):
            out.append(Finding(p.line, "Very short paragraph",
                               "a very short paragraph; it may be a heading, a caption or a fragment.", t))
        elif len(t) > 2500:
            out.append(Finding(p.line, "Very long paragraph",
                               f"{len(t.split())} words in one paragraph; two paragraphs may have been joined.",
                               t[:160] + "…"))
        letters = re.sub(r"[^A-Za-z]", "", t)
        if len(letters) > 12 and letters.isupper():
            out.append(Finding(p.line, "All capitals",
                               "this paragraph is all in capitals; it may be a heading or page furniture.", t[:160]))

    for b in script.blocks:
        if isinstance(b, Figure) and not b.caption:
            out.append(Finding(b.line, "Figure without caption",
                               f"figure {b.id} (page {b.page or '?'}) has no caption. Add a caption or a description "
                               "if the figure matters, or delete the block if it is decorative.", ""))
        if isinstance(b, Table):
            read = "will be read row by row" if b.n_cells <= max_table_cells else \
                "is large, so a spoken notice will replace it"
            out.append(Finding(b.line, "Table",
                               f"table {b.id} has {len(b.rows)} rows and {b.n_cells} cells and {read}. Check the "
                               "cells and header row.", " | ".join(b.rows[0]) if b.rows else ""))
        if isinstance(b, Heading) and len(b.text) > 120:
            out.append(Finding(b.line, "Long heading", "this heading is very long; it may be a paragraph.", b.text[:160]))

    # Pages with no text: a page marker followed directly by another.
    prev_page: Optional[PageMarker] = None
    for b in script.blocks:
        if isinstance(b, PageMarker):
            if prev_page is not None:
                out.append(Finding(prev_page.line, "Page with no text",
                                   f"page {prev_page.label} has no text. It may be a full page figure or blank.", ""))
            prev_page = b
        elif isinstance(b, (Paragraph, Heading, ListBlock, Figure, Table, Note, Skip, Footnote)):
            prev_page = None

    # Gaps in chapter numbering.
    last = None
    for b in script.blocks:
        if isinstance(b, Heading) and b.level == chapter_level:
            m = NUMBERED_RE.match(b.text)
            if not m:
                continue
            n = int(m.group(1))
            if last is not None and n != last + 1:
                out.append(Finding(b.line, "Numbering gap",
                                   f"heading number {n} follows {last}; a numbered heading may be missing, "
                                   "merged into the text, or at the wrong level.", b.text))
            last = n
    out.sort(key=lambda f: f.line)
    return out


def script_stats(script: Script, chapter_level: int) -> dict:
    words = 0
    for b in script.blocks:
        if not isinstance(b, Skip):
            words += len(block_text(b).split())
    pages = [b.label for b in script.blocks if isinstance(b, PageMarker)]
    for b in script.blocks:
        if isinstance(b, Paragraph):
            pages.extend(INLINE_PAGE_RE.findall(b.text))
    levels = Counter(b.level for b in script.blocks if isinstance(b, Heading))
    figs = [b for b in script.blocks if isinstance(b, Figure)]
    return {
        "words": words,
        "minutes": round(words / WORDS_PER_MINUTE),
        "paragraphs": sum(1 for b in script.blocks if isinstance(b, Paragraph)),
        "headings": dict(sorted(levels.items())),
        "chapters": levels.get(chapter_level, 0),
        "figures": len(figs),
        "figures_without_caption": sum(1 for f in figs if not f.caption),
        "tables": sum(1 for b in script.blocks if isinstance(b, Table)),
        "footnotes": sum(1 for b in script.blocks if isinstance(b, Footnote)),
        "library_notes": sum(1 for b in script.blocks if isinstance(b, Note)),
        "skip_blocks": sum(1 for b in script.blocks if isinstance(b, Skip)),
        "page_markers": len(pages),
        "first_page": pages[0] if pages else None,
        "last_page": pages[-1] if pages else None,
    }


CSS = """
:root { color-scheme: light dark; --fg:#1d1d1f; --bg:#fff; --muted:#5f6368; --line:#d9d9de; --warn:#8a4b00;
        --err:#b3261e; --accent:#0b57d0; --chip:#f1f3f4; }
@media (prefers-color-scheme: dark) { :root { --fg:#e8e8ea; --bg:#151517; --muted:#a0a3a8; --line:#36363b;
        --warn:#f0b35a; --err:#ff8a80; --accent:#8ab4f8; --chip:#232327; } }
body { font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg);
       max-width: 60rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
h1 { font-size: 1.6rem; margin-bottom: .2rem } h2 { margin-top: 2.2rem; border-bottom: 1px solid var(--line) }
.muted { color: var(--muted) } table { border-collapse: collapse; width: 100% }
th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top }
.stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: .6rem }
.stat { background: var(--chip); border-radius: .5rem; padding: .6rem .8rem }
.stat b { display: block; font-size: 1.4rem } .err { color: var(--err) } .warn { color: var(--warn) }
.line { font-variant-numeric: tabular-nums; white-space: nowrap } .excerpt { color: var(--muted); font-size: .92rem }
ul.outline { list-style: none; padding-left: 0 } ul.outline li { padding: .1rem 0 }
.cat { background: var(--chip); border-radius: 1rem; padding: .05rem .55rem; font-size: .85rem; white-space: nowrap }
.note { border-left: 4px solid var(--accent); padding: .4rem .8rem; background: var(--chip) }
"""


def render_review(script: Script, issues, cfg=None, notes=(), stats: Optional[dict] = None,
                  script_name: str = "script.md") -> str:
    chapter_level = int(script.meta.get("chapter-level", 0) or 0)
    if cfg is not None and isinstance(cfg.structure.chapter_level, int):
        chapter_level = cfg.structure.chapter_level
    chapter_level = chapter_level or 1
    max_cells = cfg.speech.max_table_cells if cfg is not None else 40
    st = script_stats(script, chapter_level)
    findings = find_things_to_check(script, chapter_level, max_cells)
    title = script.meta.get("title") or (cfg.title if cfg else "") or "Untitled"

    h: list[str] = []
    h.append(f"<!doctype html><html lang='en-GB'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
             f"<title>Review: {_e(title)}</title><style>{CSS}</style></head><body>")
    h.append(f"<h1>Script review: {_e(title)}</h1>")
    h.append(f"<p class='muted'>File: {_e(script_name)}. Line numbers below refer to this file. "
             "Open it in the Colab file editor, fix what needs fixing, then run the validator again "
             "(notebook section 3).</p>")
    for n in notes:
        h.append(f"<p class='note'>{_e(n)}</p>")

    h.append("<h2>Summary</h2><div class='stats'>")
    pages = f"{st['first_page']} to {st['last_page']}" if st["first_page"] else "none"
    hours, mins = divmod(st["minutes"], 60)
    cells = [
        ("Words", f"{st['words']:,}"),
        ("Estimated listening", f"{hours} h {mins} min"),
        ("Chapters (level %d)" % chapter_level, st["chapters"]),
        ("Paragraphs", st["paragraphs"]),
        ("Figures", f"{st['figures']} ({st['figures_without_caption']} no caption)"),
        ("Tables", st["tables"]),
        ("Footnotes", st["footnotes"]),
        ("Print pages marked", f"{st['page_markers']} ({pages})"),
        ("Skip blocks", st["skip_blocks"]),
    ]
    for label, value in cells:
        h.append(f"<div class='stat'><b>{_e(value)}</b>{_e(label)}</div>")
    h.append("</div>")
    if stats and stats.get("furniture_removed"):
        h.append(f"<p class='muted'>{stats['furniture_removed']} running headers, footers and page numbers were removed.</p>")

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    h.append("<h2>Problems in the script format</h2>")
    if not issues:
        h.append("<p>None. The script is valid.</p>")
    else:
        h.append(f"<p>{len(errors)} error(s) must be fixed before audio can be made; "
                 f"{len(warnings)} warning(s) are worth a look.</p><table><tr><th>Line</th><th>Problem</th></tr>")
        for i in issues:
            cls = "err" if i.severity == "error" else "warn"
            h.append(f"<tr><td class='line'>{i.line or ''}</td><td class='{cls}'>{_e(i.message)}</td></tr>")
        h.append("</table>")

    h.append("<h2>Heading outline</h2>")
    h.append(f"<p class='muted'>Level {chapter_level} headings become chapters (navigation points and "
             "M4B chapters). To change a heading's level, change the number of # signs in front of it.</p>")
    h.append("<ul class='outline'>")
    for b in script.blocks:
        if isinstance(b, Heading):
            pad = (b.level - 1) * 1.4
            mark = " <span class='cat'>chapter</span>" if b.level == chapter_level else ""
            h.append(f"<li style='padding-left:{pad}rem'><span class='line muted'>{b.line}</span> "
                     f"{'#' * b.level} {_e(b.text)}{mark}</li>")
    h.append("</ul>")

    h.append("<h2>Worth a human look</h2>")
    if not findings:
        h.append("<p>Nothing flagged.</p>")
    else:
        by_cat = Counter(f.category for f in findings)
        h.append("<p>" + ", ".join(f"{_e(c)}: {n}" for c, n in by_cat.most_common()) + "</p>")
        h.append("<table><tr><th>Line</th><th>What</th><th>Details</th></tr>")
        for f in findings:
            ex = f"<div class='excerpt'>{_e(f.excerpt)}</div>" if f.excerpt else ""
            h.append(f"<tr><td class='line'>{f.line}</td><td><span class='cat'>{_e(f.category)}</span></td>"
                     f"<td>{_e(f.message)}{ex}</td></tr>")
        h.append("</table>")
    h.append("</body></html>")
    return "\n".join(h)


def write_review(script_path: Path, out_path: Path, cfg=None, notes=(), stats: Optional[dict] = None) -> Path:
    chapter_level = cfg.structure.chapter_level if cfg is not None and isinstance(cfg.structure.chapter_level, int) else None
    script, issues = validate_file(script_path, chapter_level)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_review(script, issues, cfg, notes, stats, script_path.name), encoding="utf-8")
    return out_path
