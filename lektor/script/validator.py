"""Check a script for problems and explain them in plain English.

Errors stop later stages (audio generation will not start while there are
errors). Warnings are things worth a look that do not stop anything.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .model import (
    FOOTNOTE_REF_RE,
    INLINE_PAGE_RE,
    Figure,
    Footnote,
    Heading,
    ListBlock,
    Note,
    PageMarker,
    Paragraph,
    Script,
    Table,
)
from .parser import Issue, parse_script
from .writer import SCRIPT_FORMAT_VERSION

_STRAY_TAG_RE = re.compile(r"(?<!\\)\[\[(?!page\s+[^\]\s]+\s*\]\])")


def _inline_texts(b):
    if isinstance(b, (Paragraph, Note, Footnote)):
        yield b.line, b.text
    elif isinstance(b, ListBlock):
        for item in b.items:
            yield item.line or b.line, item.text
    elif isinstance(b, Figure):
        yield b.line, b.caption
        yield b.line, b.description
    elif isinstance(b, Table):
        yield b.line, b.caption


def check_script(script: Script, chapter_level: int | None = None) -> list[Issue]:
    issues: list[Issue] = []
    if not chapter_level:
        try:
            chapter_level = int(script.meta.get("chapter-level") or 0) or None
        except ValueError:
            chapter_level = None
    err = lambda line, msg: issues.append(Issue(line, "error", msg))  # noqa: E731
    warn = lambda line, msg: issues.append(Issue(line, "warning", msg))  # noqa: E731

    version = script.meta.get("lektor-script")
    if version and version != SCRIPT_FORMAT_VERSION:
        warn(1, f"this script says it uses format version {version}, but this version of Lektor "
                f"reads version {SCRIPT_FORMAT_VERSION}.")

    readable = [b for b in script.blocks if isinstance(b, (Paragraph, ListBlock, Heading, Figure, Table, Note))]
    if not readable:
        err(0, "the script has nothing to read aloud: no headings, paragraphs, lists, figures or tables were found.")
        return issues

    # Unique ids
    ids = Counter(b.id for b in script.blocks if isinstance(b, (Figure, Table)))
    for b in script.blocks:
        if isinstance(b, (Figure, Table)) and ids[b.id] > 1:
            err(b.line, f"the id '{b.id}' is used by more than one figure or table. Each id must be different.")
            ids[b.id] = 1  # report once

    # Footnotes
    defs = {}
    for b in script.blocks:
        if isinstance(b, Footnote):
            if b.id in defs:
                err(b.line, f"footnote [^{b.id}] is defined twice (also on line {defs[b.id].line}).")
            defs[b.id] = b
    refs: dict[str, int] = {}
    for b in script.blocks:
        for line, text in _inline_texts(b):
            for m in FOOTNOTE_REF_RE.finditer(text):
                refs.setdefault(m.group(1), line)
                if m.group(1) not in defs:
                    err(line, f"the text refers to footnote [^{m.group(1)}], but there is no line starting "
                              f"'[^{m.group(1)}]:' with the footnote text.")
            if _STRAY_TAG_RE.search(text):
                warn(line, "this text contains '[[' that is not a page marker. If it is part of the book's "
                           "text, put a backslash in front of it (\\[[); if it is meant to be a tag, put it "
                           "on a line of its own.")
    for fid, b in defs.items():
        if fid not in refs:
            warn(b.line, f"footnote [^{fid}] is never referred to in the text. It will be read where it stands.")

    # Headings
    prev_level = 0
    levels = Counter()
    for b in script.blocks:
        if isinstance(b, Heading):
            levels[b.level] += 1
            if prev_level and b.level > prev_level + 1:
                warn(b.line, f"this heading jumps from level {prev_level} to level {b.level}. Navigation works "
                             f"best without gaps; consider using {'#' * (prev_level + 1)}.")
            prev_level = b.level
    if not levels:
        warn(0, "the script has no headings, so the audiobook will have no chapters to move between.")
    elif chapter_level and not levels.get(chapter_level):
        warn(0, f"book.yaml says chapters are level {chapter_level} headings, but there are none at that level.")

    # Page markers
    last_num = None
    last_line = 0
    seen_pages: dict[str, int] = {}
    for b in script.blocks:
        labels = []
        if isinstance(b, PageMarker):
            labels.append((b.line, b.label))
        for line, text in _inline_texts(b):
            labels.extend((line, m.group(1)) for m in INLINE_PAGE_RE.finditer(text))
        for line, label in labels:
            if label in seen_pages:
                warn(line, f"page {label} is marked more than once (also on line {seen_pages[label]}).")
            seen_pages[label] = line
            if label.isdigit():
                n = int(label)
                if last_num is not None and n < last_num:
                    warn(line, f"page {label} comes after page {last_num} (line {last_line}); page numbers "
                               "normally go up.")
                last_num, last_line = n, line

    # Tables
    for b in script.blocks:
        if isinstance(b, Table):
            if b.header_rows > len(b.rows):
                warn(b.line, f"this table says it has {b.header_rows} header row(s) but only has {len(b.rows)} row(s).")

    return issues


def validate_text(text: str, chapter_level: int | None = None) -> tuple[Script, list[Issue]]:
    script, issues = parse_script(text)
    issues = issues + check_script(script, chapter_level)
    issues.sort(key=lambda i: (i.line, i.severity != "error"))
    return script, issues


def validate_file(path: Path | str, chapter_level: int | None = None) -> tuple[Script, list[Issue]]:
    return validate_text(Path(path).read_text(encoding="utf-8"), chapter_level)


def summarise(issues: list[Issue]) -> str:
    n_err = sum(1 for i in issues if i.severity == "error")
    n_warn = len(issues) - n_err
    if not issues:
        return "The script looks good: no problems found."
    head = f"Found {n_err} error(s) and {n_warn} warning(s)."
    if n_err:
        head += " Errors must be fixed before audio can be made."
    return head + "\n" + "\n".join(str(i) for i in issues)


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)
