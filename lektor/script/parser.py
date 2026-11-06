"""Parse ``script.md`` into a :class:`~lektor.script.model.Script`.

The parser never raises on bad input. It does its best and records every
problem as an :class:`Issue` with a line number and a plain English
message. :func:`parse_script` returns both the script and the issues; the
validator adds further checks on top.

See ``docs/script-format.md`` for the format itself.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .model import (
    Comment,
    Figure,
    Footnote,
    Heading,
    ListBlock,
    ListItem,
    Note,
    PageMarker,
    Paragraph,
    Script,
    Skip,
    Table,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
HASHES_NO_SPACE_RE = re.compile(r"^#{1,6}[^#\s]")
PAGE_RE = re.compile(r"^\[\[page\s+([^\]\s]+)\s*\]\]$", re.I)
OPEN_RE = re.compile(r"^\[\[([a-z]+)\b(.*?)\]\]$", re.I)
CLOSE_RE = re.compile(r"^\[\[/([a-z]+)\s*\]\]$", re.I)
ONELINE_NOTE_RE = re.compile(r"^\[\[note\b(.*?)\]\](.*)\[\[/note\]\]$", re.I | re.S)
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([A-Za-z0-9_.-]+)\]:\s*(.*)$")
BULLET_RE = re.compile(r"^([-*+])\s+(.*)$")
ORDERED_RE = re.compile(r"^(\d{1,4}[.)])\s+(.*)$")
FIELD_RE = re.compile(r"^(Caption|Description)\s*:\s?(.*)$", re.I)
COMMENT_START = "<!--"
COMMENT_END = "-->"

BLOCK_TAGS = {"figure", "table", "note", "skip", "endnotes"}
KNOWN_TAGS = BLOCK_TAGS | {"page"}


@dataclass
class Issue:
    line: int
    severity: str  # "error" or "warning"
    message: str

    def __str__(self) -> str:
        where = f"Line {self.line}" if self.line else "Script"
        label = "" if self.severity == "error" else " (warning)"
        return f"{where}{label}: {self.message}"


def parse_attrs(text: str, line_no: int, issues: list[Issue]) -> dict[str, str]:
    """Parse ``id=fig-1 page="48" caption-style=x`` into a dict."""
    text = text.strip()
    if not text:
        return {}
    try:
        tokens = shlex.split(text)
    except ValueError:
        issues.append(Issue(line_no, "error", "a quotation mark in this tag is not closed."))
        tokens = text.replace('"', "").split()
    attrs: dict[str, str] = {}
    for tok in tokens:
        if "=" not in tok:
            issues.append(Issue(line_no, "warning", f"'{tok}' in this tag should look like name=value; it was ignored."))
            continue
        k, v = tok.split("=", 1)
        attrs[k.strip().lower()] = v.strip()
    return attrs


def unescape_line(line: str) -> str:
    """Remove the single backslash that protects a line from being read as markup."""
    return line[1:] if line.startswith("\\") else line


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.i = 0
        self.issues: list[Issue] = []
        self.script = Script()
        self.in_endnotes: Optional[int] = None
        self.fig_count = 0
        self.tab_count = 0

    # -- helpers ------------------------------------------------------------
    def err(self, line: int, msg: str) -> None:
        self.issues.append(Issue(line, "error", msg))

    def warn(self, line: int, msg: str) -> None:
        self.issues.append(Issue(line, "warning", msg))

    @property
    def line_no(self) -> int:
        return self.i + 1

    def add(self, block) -> None:
        self.script.blocks.append(block)

    # -- front matter -------------------------------------------------------
    def front_matter(self) -> None:
        if not self.lines or self.lines[0].strip() != "---":
            return
        for j in range(1, len(self.lines)):
            s = self.lines[j].strip()
            if s == "---":
                for k in range(1, j):
                    raw = self.lines[k]
                    if not raw.strip() or raw.lstrip().startswith("#"):
                        continue
                    if ":" not in raw:
                        self.warn(k + 1, "front matter lines should look like 'name: value'.")
                        continue
                    key, val = raw.split(":", 1)
                    val = val.strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    self.script.meta[key.strip()] = val
                self.i = j + 1
                return
        self.err(1, "the front matter starting with '---' on line 1 is never closed with another '---' line.")

    # -- main loop ----------------------------------------------------------
    def parse(self) -> Script:
        self.front_matter()
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            line = raw.strip()
            if not line:
                self.i += 1
                continue
            if line.startswith(COMMENT_START):
                self.comment()
                continue
            if line.startswith("\\"):
                self.paragraph()
                continue
            m = HEADING_RE.match(line)
            if m:
                text = m.group(2).strip()
                if not text:
                    self.err(self.line_no, "this heading has no text.")
                self.add(Heading(len(m.group(1)), unescape_line(text), self.line_no))
                self.i += 1
                continue
            if HASHES_NO_SPACE_RE.match(line):
                self.warn(self.line_no, "this line starts with '#' but has no space after it, so it is read as "
                          "ordinary text. Add a space (for example '## Title') if it is meant to be a heading.")
            m = PAGE_RE.match(line)
            if m:
                self.add(PageMarker(m.group(1), self.line_no))
                self.i += 1
                continue
            m = ONELINE_NOTE_RE.match(line)
            if m:
                self.add(Note(unescape_line(m.group(2).strip()), self.line_no))
                self.i += 1
                continue
            m = CLOSE_RE.match(line)
            if m:
                tag = m.group(1).lower()
                if tag == "endnotes" and self.in_endnotes is not None:
                    self.in_endnotes = None
                else:
                    self.err(self.line_no, f"[[/{tag}]] closes a {tag} block, but no {tag} block is open here.")
                self.i += 1
                continue
            m = OPEN_RE.match(line)
            if m and m.group(1).lower() in BLOCK_TAGS:
                self.tagged_block(m.group(1).lower(), m.group(2))
                continue
            if m and m.group(1).lower() not in KNOWN_TAGS:
                self.err(self.line_no, f"[[{m.group(1)}]] is not a tag Lektor knows. Known tags are: "
                         "page, figure, table, note, skip, endnotes. If this is ordinary text, put a "
                         "backslash in front of it.")
                self.paragraph()
                continue
            if line.lower().startswith("[[page"):
                self.err(self.line_no, "this page marker is not written correctly. It should look like "
                         "[[page 47]] on a line of its own (page labels cannot contain spaces).")
                self.i += 1
                continue
            m = FOOTNOTE_DEF_RE.match(line)
            if m:
                self.footnote(m.group(1), m.group(2))
                continue
            if BULLET_RE.match(line) or ORDERED_RE.match(line):
                self.list_block()
                continue
            if line.startswith("|"):
                self.warn(self.line_no, "this looks like a table row, but it is not inside a [[table]] block, "
                          "so it will be read as ordinary text.")
            if FIELD_RE.match(line):
                self.warn(self.line_no, f"'{line.split(':')[0]}:' only has a meaning inside a [[figure]] or "
                          "[[table]] block; here it is read as ordinary text.")
            self.paragraph()
        if self.in_endnotes is not None:
            self.err(self.in_endnotes, "an [[endnotes]] block was opened but never closed with [[/endnotes]].")
        return self.script

    # -- block parsers ------------------------------------------------------
    def comment(self) -> None:
        start = self.line_no
        buf = []
        while self.i < len(self.lines):
            buf.append(self.lines[self.i])
            self.i += 1
            if COMMENT_END in buf[-1]:
                break
        else:
            self.err(start, "a comment was opened with '<!--' but never closed with '-->'.")
        text = "\n".join(buf).strip()
        text = text[len(COMMENT_START):]
        if text.endswith(COMMENT_END):
            text = text[: -len(COMMENT_END)]
        self.add(Comment(text.strip(), start))

    def _starts_new_block(self, line: str) -> bool:
        if not line:
            return True
        if line.startswith("\\"):
            return False
        return bool(
            HEADING_RE.match(line)
            or line.startswith("[[")
            or line.startswith(COMMENT_START)
            or FOOTNOTE_DEF_RE.match(line)
            or BULLET_RE.match(line)
            or ORDERED_RE.match(line)
        )

    def paragraph(self) -> None:
        start = self.line_no
        parts = [unescape_line(self.lines[self.i].strip())]
        self.i += 1
        while self.i < len(self.lines):
            nxt = self.lines[self.i].strip()
            if self._starts_new_block(nxt):
                break
            parts.append(unescape_line(nxt))
            self.i += 1
        self.add(Paragraph(" ".join(parts), start))

    def footnote(self, fid: str, first: str) -> None:
        start = self.line_no
        parts = [unescape_line(first.strip())]
        self.i += 1
        while self.i < len(self.lines):
            nxt = self.lines[self.i].strip()
            if self._starts_new_block(nxt):
                break
            parts.append(unescape_line(nxt))
            self.i += 1
        text = " ".join(p for p in parts if p)
        if not text:
            self.warn(start, f"footnote [^{fid}] has no text.")
        self.add(Footnote(fid, text, endnote=self.in_endnotes is not None, line=start))

    def list_block(self) -> None:
        start = self.line_no
        first = self.lines[self.i].strip()
        ordered = bool(ORDERED_RE.match(first)) and not BULLET_RE.match(first)
        items: list[ListItem] = []
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            line = raw.strip()
            mb, mo = BULLET_RE.match(line), ORDERED_RE.match(line)
            if mb or mo:
                m = mb or mo
                items.append(ListItem(unescape_line(m.group(2).strip()), m.group(1), self.line_no))
                self.i += 1
                continue
            if line and raw[:2] in ("  ", "\t") and items and not self._starts_new_block(line):
                items[-1].text += " " + unescape_line(line)
                self.i += 1
                continue
            break
        self.add(ListBlock(items, ordered, start))

    def _collect_until_close(self, tag: str, start: int) -> list[tuple[int, str]]:
        """Return (line number, raw line) pairs up to the matching close tag."""
        body: list[tuple[int, str]] = []
        self.i += 1
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            s = raw.strip()
            cm = CLOSE_RE.match(s)
            if cm and cm.group(1).lower() == tag:
                self.i += 1
                return body
            if cm and cm.group(1).lower() != tag and cm.group(1).lower() in BLOCK_TAGS:
                self.err(self.line_no, f"[[/{cm.group(1).lower()}]] was found while a {tag} block (opened on "
                         f"line {start}) was still open. Close the {tag} block first with [[/{tag}]].")
                self.i += 1
                return body
            om = OPEN_RE.match(s)
            if tag != "skip" and om and om.group(1).lower() in BLOCK_TAGS:
                self.err(start, f"a {tag} block was opened but never closed: another [[{om.group(1)}]] block "
                         f"starts on line {self.line_no} before [[/{tag}]] was found.")
                return body
            if tag != "skip" and HEADING_RE.match(s):
                self.err(start, f"a {tag} block was opened but never closed: a heading appears on line "
                         f"{self.line_no} before [[/{tag}]] was found.")
                return body
            body.append((self.line_no, raw))
            self.i += 1
        self.err(start, f"a {tag} block was opened but never closed. Add [[/{tag}]] where it should end.")
        return body

    def tagged_block(self, tag: str, attr_text: str) -> None:
        start = self.line_no
        attrs = parse_attrs(attr_text, start, self.issues)
        if tag == "endnotes":
            if self.in_endnotes is not None:
                self.err(start, f"an [[endnotes]] block is already open (from line {self.in_endnotes}).")
            self.in_endnotes = start
            self.i += 1
            return
        body = self._collect_until_close(tag, start)
        if tag == "skip":
            self.add(Skip([l for _, l in body], attrs.pop("reason", ""), start))
        elif tag == "note":
            text = " ".join(unescape_line(l.strip()) for _, l in body if l.strip())
            if not text:
                self.warn(start, "this library note is empty.")
            self.add(Note(text, start))
        elif tag == "figure":
            self.figure(attrs, body, start)
        elif tag == "table":
            self.table(attrs, body, start)

    def _fields(self, body: list[tuple[int, str]], allowed: set[str], start: int, tag: str
                ) -> tuple[dict[str, str], list[tuple[int, str]]]:
        fields: dict[str, list[str]] = {}
        other: list[tuple[int, str]] = []
        current: Optional[str] = None
        for n, raw in body:
            s = raw.strip()
            m = FIELD_RE.match(s)
            if m:
                name = m.group(1).lower()
                if name not in allowed:
                    self.warn(n, f"'{m.group(1)}:' is not used in a {tag} block; it was ignored.")
                    current = None
                    continue
                if name in fields:
                    self.warn(n, f"this {tag} block has more than one '{m.group(1)}:' line; the text was joined.")
                fields.setdefault(name, [])
                if m.group(2).strip():
                    fields[name].append(unescape_line(m.group(2).strip()))
                current = name
            elif not s:
                current = None if tag == "table" else current
            elif current is not None and not (tag == "table" and s.startswith("|")):
                fields[current].append(unescape_line(s))
            else:
                other.append((n, raw))
        return {k: " ".join(v) for k, v in fields.items()}, other

    def figure(self, attrs: dict[str, str], body, start: int) -> None:
        fields, other = self._fields(body, {"caption", "description"}, start, "figure")
        for n, raw in other:
            self.warn(n, "text inside a figure block should follow 'Caption:' or 'Description:'; "
                      "this line was added to the caption.")
            fields["caption"] = (fields.get("caption", "") + " " + raw.strip()).strip()
        self.fig_count += 1
        fid = attrs.pop("id", "")
        if not fid:
            self.warn(start, "this figure has no id; Lektor gave it one automatically.")
            fid = f"fig-auto-{self.fig_count}"
        self.add(Figure(
            id=fid,
            page=attrs.pop("page", None),
            caption=fields.get("caption", ""),
            description=fields.get("description", ""),
            description_source=attrs.pop("described-by", ""),
            attrs=attrs,
            line=start,
        ))

    def table(self, attrs: dict[str, str], body, start: int) -> None:
        fields, other = self._fields(body, {"caption"}, start, "table")
        rows: list[list[str]] = []
        sep_at: Optional[int] = None
        for n, raw in other:
            s = raw.strip()
            if not s.startswith("|"):
                self.warn(n, "lines in a table block should start with '|'; this line was ignored.")
                continue
            cells = _split_row(s)
            if all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip()) and any(c.strip() for c in cells):
                sep_at = len(rows)
                continue
            rows.append(cells)
        if not rows:
            self.warn(start, "this table has no rows.")
        widths = {len(r) for r in rows}
        if len(widths) > 1:
            self.warn(start, f"the rows of this table have different numbers of cells ({', '.join(map(str, sorted(widths)))}).")
        self.tab_count += 1
        tid = attrs.pop("id", "") or f"tab-auto-{self.tab_count}"
        header = attrs.pop("header", None)
        try:
            header_rows = int(header) if header is not None else (sep_at if sep_at is not None else 0)
        except ValueError:
            self.warn(start, "header= should be a number of rows; 1 was assumed.")
            header_rows = 1
        self.add(Table(id=tid, page=attrs.pop("page", None), rows=rows, header_rows=header_rows,
                       caption=fields.get("caption", ""), attrs=attrs, line=start))


def _split_row(s: str) -> list[str]:
    s = s.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, buf, k = [], "", 0
    while k < len(s):
        if s[k] == "\\" and k + 1 < len(s) and s[k + 1] == "|":
            buf += "|"
            k += 2
            continue
        if s[k] == "|":
            cells.append(buf.strip())
            buf = ""
        else:
            buf += s[k]
        k += 1
    cells.append(buf.strip())
    return cells


def parse_script(text: str) -> tuple[Script, list[Issue]]:
    p = _Parser(text)
    script = p.parse()
    return script, p.issues


def read_script(path: Path | str) -> tuple[Script, list[Issue]]:
    return parse_script(Path(path).read_text(encoding="utf-8"))
