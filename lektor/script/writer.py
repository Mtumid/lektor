"""Write a :class:`~lektor.script.model.Script` back to ``script.md``.

The writer and the parser are designed as a pair: for any script,
``parse(write(script))`` gives the same blocks back (line numbers aside).
Text that would otherwise be mistaken for markup is protected with a single
leading backslash, which the parser removes again.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import (
    Comment,
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
)

SCRIPT_FORMAT_VERSION = "1"

_NEEDS_ESCAPE = re.compile(
    r"^(#|\[\[|\[\^|[-*+]\s|\d{1,4}[.)]\s|\||---|<!--|\\|(caption|description)\s*:)", re.I
)


def _clean(text: str) -> str:
    """Single line, no stray whitespace."""
    return re.sub(r"\s+", " ", text).strip()


def escape_text(text: str) -> str:
    """Protect a line of author text so the parser reads it back unchanged."""
    text = _clean(text)
    if _NEEDS_ESCAPE.match(text):
        text = "\\" + text
    return text


def escape_field(text: str) -> str:
    """Escape text that follows a prefix such as '# ', '- ' or 'Caption: '.

    Only a leading backslash needs protecting there.
    """
    text = _clean(text)
    return "\\" + text if text.startswith("\\") else text


def _attr(value: str) -> str:
    if value == "" or re.search(r'[\s"\'=\]]', value):
        return '"' + value.replace('"', "'") + '"'
    return value


def _tag(name: str, attrs: dict[str, str | None]) -> str:
    parts = [name] + [f"{k}={_attr(str(v))}" for k, v in attrs.items() if v not in (None, "")]
    return "[[" + " ".join(parts) + "]]"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(_clean(c).replace("|", "\\|") for c in cells) + " |"


def write_block(b) -> str:
    if isinstance(b, Heading):
        return "#" * b.level + " " + escape_field(b.text)
    if isinstance(b, PageMarker):
        return f"[[page {b.label}]]"
    if isinstance(b, Paragraph):
        return escape_text(b.text)
    if isinstance(b, ListBlock):
        lines = []
        for n, item in enumerate(b.items, 1):
            marker = item.marker if item.marker else (f"{n}." if b.ordered else "-")
            lines.append(f"{marker} {escape_field(item.text)}")
        return "\n".join(lines)
    if isinstance(b, Figure):
        attrs = {"id": b.id, "page": b.page, "described-by": b.description_source, **b.attrs}
        return "\n".join([
            _tag("figure", attrs),
            "Caption: " + escape_field(b.caption) if b.caption else "Caption:",
            "Description: " + escape_field(b.description) if b.description else "Description:",
            "[[/figure]]",
        ])
    if isinstance(b, Table):
        attrs = {"id": b.id, "page": b.page, "header": str(b.header_rows), **b.attrs}
        lines = [_tag("table", attrs)]
        for i, r in enumerate(b.rows):
            lines.append(_row(r))
            if b.header_rows and i == b.header_rows - 1:
                lines.append("|" + "|".join("---" for _ in r) + "|")
        lines.append("Caption: " + escape_field(b.caption) if b.caption else "Caption:")
        lines.append("[[/table]]")
        return "\n".join(lines)
    if isinstance(b, Footnote):
        return f"[^{b.id}]: {escape_field(b.text)}"
    if isinstance(b, Note):
        return "[[note]]" + escape_field(b.text) + "[[/note]]"
    if isinstance(b, Skip):
        attrs = {"reason": b.reason}
        return "\n".join([_tag("skip", attrs), *b.lines, "[[/skip]]"])
    if isinstance(b, Comment):
        return f"<!-- {b.text} -->"
    raise TypeError(f"Unknown block type {type(b).__name__}")


def write_script(script: Script) -> str:
    out: list[str] = []
    meta = {"lektor-script": SCRIPT_FORMAT_VERSION, **{k: v for k, v in script.meta.items() if k != "lektor-script"}}
    out.append("---")
    for k, v in meta.items():
        out.append(f"{k}: {v}")
    out.append("---")
    out.append("")
    in_endnotes = False
    for b in script.blocks:
        is_end = isinstance(b, Footnote) and b.endnote
        if is_end and not in_endnotes:
            out.append("[[endnotes]]")
            out.append("")
            in_endnotes = True
        elif not is_end and in_endnotes:
            out.append("[[/endnotes]]")
            out.append("")
            in_endnotes = False
        out.append(write_block(b))
        out.append("")
    if in_endnotes:
        out.append("[[/endnotes]]")
        out.append("")
    return "\n".join(out)


def save_script(script: Script, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(write_script(script), encoding="utf-8")
    tmp.replace(path)
