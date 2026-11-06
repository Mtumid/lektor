import random

import pytest

from lektor.script.model import (
    Comment, Figure, Footnote, Heading, ListBlock, ListItem, Note, PageMarker, Paragraph, Script, Skip, Table,
    escape_inline, plain_text, split_inline,
)
from lektor.script.parser import parse_script
from lektor.script.validator import has_errors, validate_text
from lektor.script.writer import write_script


def strip_lines(blocks):
    out = []
    for b in blocks:
        d = {k: v for k, v in vars(b).items() if k != "line"}
        if isinstance(b, ListBlock):
            d["items"] = [(i.text, i.marker) for i in b.items]
        out.append((type(b).__name__, d))
    return out


def roundtrip(script):
    text = write_script(script)
    parsed, issues = parse_script(text)
    assert [i for i in issues if i.severity == "error"] == [], text
    assert strip_lines(parsed.blocks) == strip_lines(script.blocks), text
    return text, parsed


SAMPLE = """---
title: Test book
---

# Part One

## 12. Strategy title in C#

[[page 47]]

Paragraph text exactly as the author wrote it,[^1] across
two lines. [[page 48]] More text.

- item one
- item two
  continued
1. ordered

[[figure id=fig-012-1 page=48]]
Caption: Text of the caption.
Description:
[[/figure]]

[[table id=t1 page=49]]
| Note | Freq |
|---|---|
| A | 440 Hz |
Caption: Tuning.
[[/table]]

[[note]]A note added by the library.[[/note]]

[^1]: The footnote.

[[skip reason="table of contents"]]
Contents ..... 3
[[/skip]]

<!-- check this -->

[[endnotes]]
[^e1]: An endnote.
[[/endnotes]]
"""


def test_parse_sample():
    s, issues = parse_script(SAMPLE)
    assert issues == []
    kinds = [type(b).__name__ for b in s.blocks]
    assert kinds == ["Heading", "Heading", "PageMarker", "Paragraph", "ListBlock", "Figure", "Table", "Note",
                     "Footnote", "Skip", "Comment", "Footnote"]
    assert s.meta["title"] == "Test book"
    assert s.blocks[1].text == "12. Strategy title in C#"  # trailing # is kept
    para = s.blocks[3]
    assert para.text.startswith("Paragraph text exactly")
    assert "across two lines" in para.text
    assert split_inline(para.text)[1] == ("fnref", "1")
    assert ("page", "48") in split_inline(para.text)
    assert plain_text(para.text) == "Paragraph text exactly as the author wrote it, across two lines. More text."
    lst = s.blocks[4]
    assert [i.text for i in lst.items] == ["item one", "item two continued", "ordered"]
    fig = s.blocks[5]
    assert (fig.id, fig.page, fig.caption, fig.description) == ("fig-012-1", "48", "Text of the caption.", "")
    tab = s.blocks[6]
    assert tab.rows == [["Note", "Freq"], ["A", "440 Hz"]] and tab.header_rows == 1 and tab.caption == "Tuning."
    assert s.blocks[9].reason == "table of contents" and s.blocks[9].lines == ["Contents ..... 3"]
    assert s.blocks[8].endnote is False and s.blocks[11].endnote is True


def test_sample_round_trip():
    s, _ = parse_script(SAMPLE)
    roundtrip(s)


@pytest.mark.parametrize("text", [
    "# Not a heading but a paragraph",
    "- a dash at the start",
    "* star",
    "12. A paragraph that starts like a numbered list",
    "| pipe at start",
    "[[page 3]] page marker at the start of a paragraph",
    "[^3]: looks like a footnote",
    "Caption: looks like a field",
    "<!-- looks like a comment -->",
    "\\ starts with a backslash",
    "--- three dashes",
    "Plain text with \\[[ escaped brackets and \\[^x] too",
])
def test_escaping_round_trip(text):
    s = Script(blocks=[Heading(1, "H"), Paragraph(text)])
    out, parsed = roundtrip(s)
    assert parsed.blocks[1].text == text


@pytest.mark.parametrize("cls,kw", [
    (Heading, {"level": 2, "text": "\\odd heading"}),
    (Note, {"text": "# note starting with hash"}),
    (Footnote, {"id": "7", "text": "- footnote text"}),
])
def test_prefix_fields_round_trip(cls, kw):
    roundtrip(Script(blocks=[Heading(1, "H"), cls(**kw)]))


def test_figure_and_table_round_trip():
    s = Script(blocks=[
        Heading(1, "Chapter"),
        Figure(id="fig-1", page="4", caption="Figure 1: A | B", description="Two bars; the second louder.",
               description_source="librarian", attrs={"kind": "screenshot"}),
        Table(id="t", page="iv", rows=[["a|b", "c"], ["1", "2"], ["3", "4"]], header_rows=1, caption="Values"),
        Table(id="t2", rows=[["x", "y"]], header_rows=0),
        Skip(["[[figure id=x]]", "anything at all", ""], reason="index"),
        Comment("a\nmulti line comment"),
        ListBlock([ListItem("first", "1."), ListItem("second", "2.")], ordered=True),
        Footnote("e1", "End.", endnote=True),
        Footnote("e2", "End two.", endnote=True),
    ])
    roundtrip(s)


def test_random_round_trip():
    rng = random.Random(1234)
    alphabet = ["word", "C#", "4/4", "[[", "[^", "|", "#", "-", "*", "1.", "\\", "Caption:", "]]", "<!--", "é", "—"]
    for _ in range(200):
        words = [rng.choice(alphabet) for _ in range(rng.randint(1, 8))]
        text = " ".join(words)
        text = escape_inline(text)
        s = Script(blocks=[Heading(1, "H"), Paragraph(text), ListBlock([ListItem(text, "-")]),
                           Note(text), Footnote("f", text)])
        roundtrip(s)


def test_crlf_and_unicode():
    s, issues = parse_script("# Título\r\n\r\nCafé – naïve “quotes”.\r\n")
    assert issues == []
    assert s.blocks[1].text == "Café – naïve “quotes”."


# ---------------------------------------------------------------------------
# Validator messages
# ---------------------------------------------------------------------------
def messages(text):
    _, issues = validate_text(text)
    return [str(i) for i in issues]


def test_unclosed_figure():
    msgs = messages("# A\n\n[[figure id=f1]]\nCaption: x\n")
    assert any("Line 3: a figure block was opened but never closed" in m for m in msgs)


def test_unclosed_figure_before_heading():
    msgs = messages("# A\n\n[[figure id=f1]]\nCaption: x\n\n## B\n")
    assert any(m.startswith("Line 3:") and "heading appears on line 6" in m for m in msgs)


def test_close_without_open():
    msgs = messages("# A\n\ntext\n[[/table]]\n")
    assert any("Line 4: [[/table]] closes a table block, but no table block is open" in m for m in msgs)


def test_unknown_tag():
    msgs = messages("# A\n\n[[picture id=1]]\n")
    assert any("Line 3: [[picture]] is not a tag Lektor knows" in m for m in msgs)


def test_bad_page_marker():
    msgs = messages("# A\n\n[[page 4 5]]\n")
    assert any("Line 3: this page marker is not written correctly" in m for m in msgs)


def test_missing_footnote_definition():
    msgs = messages("# A\n\nSome text.[^9]\n")
    assert any("Line 3: the text refers to footnote [^9]" in m for m in msgs)


def test_duplicate_ids():
    text = "# A\n\n[[figure id=x]]\nCaption: a\n[[/figure]]\n\n[[figure id=x]]\nCaption: b\n[[/figure]]\n"
    assert any("used by more than one" in m for m in messages(text))


def test_heading_jump_and_page_order_warnings():
    msgs = messages("# A\n\n### C\n\n[[page 5]]\n\ntext\n\n[[page 3]]\n\nmore\n")
    assert any("jumps from level 1 to level 3" in m for m in msgs)
    assert any("page 3 comes after page 5" in m for m in msgs)


def test_heading_without_space_warning():
    msgs = messages("# A\n\n##Strategy\n")
    assert any("no space after it" in m for m in msgs)


def test_empty_script_is_error():
    _, issues = validate_text("<!-- nothing -->\n")
    assert has_errors(issues)


def test_unclosed_front_matter():
    msgs = messages("---\ntitle: x\n# A\n")
    assert any("front matter" in m for m in msgs)


def test_valid_script_has_no_errors():
    _, issues = validate_text(SAMPLE)
    assert not has_errors(issues)
