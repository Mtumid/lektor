import importlib.util
import json
from pathlib import Path

import pytest

import fixtures
from lektor.books import create_or_update_book
from lektor.config import BookConfig
from lektor.ingest import read_source, run_ingest, script_was_edited
from lektor.ingest.common import RawItem, assign_heading_levels, build_script, join_hyphenated, repair_hyphenation
from lektor.ingest.docling_reader import detected_page_numbers, neutral_to_items
from lektor.ingest.pages import labels_from_detected, resolve_labels
from lektor.script.model import Figure, Footnote, Heading, ListBlock, PageMarker, Paragraph, Skip, Table, plain_text
from lektor.script.parser import read_script
from lektor.script.validator import has_errors, validate_file

HAS_DOCLING = importlib.util.find_spec("docling") is not None


def cfg_for(src: Path, reader="fallback") -> BookConfig:
    c = BookConfig(book_id="t", title=fixtures.TITLE, source=str(src))
    c.ingest.reader = reader
    return c


def script_from(src: Path, reader="fallback"):
    items, n, meta, name, notes = read_source(src, cfg_for(src, reader), log=lambda m: None)
    script, stats = build_script(items, meta={"title": "t"}, n_pages=n, skip_sections=["contents"])
    return script, stats, name, notes


def all_pages(script):
    from lektor.script.model import INLINE_PAGE_RE

    out = []
    for b in script.blocks:
        if isinstance(b, PageMarker):
            out.append(b.label)
        elif isinstance(b, Paragraph):
            out.extend(INLINE_PAGE_RE.findall(b.text))
    return out


def headings(script):
    return [(b.level, b.text) for b in script.blocks if isinstance(b, Heading)]


def all_text(script):
    parts = []
    for b in script.blocks:
        if isinstance(b, (Paragraph, Heading, Footnote)):
            parts.append(plain_text(b.text))
        elif isinstance(b, ListBlock):
            parts.extend(plain_text(i.text) for i in b.items)
    return " ".join(parts)


EXPECTED_HEADINGS = [
    (1, "Problems of Beginning"), (2, "1. Start With a Beat"), (2, "2. Use a Reference Track"),
    (1, "Problems of Finishing"), (2, "3. Finish Something"),
]


# ---------------------------------------------------------------------------
# PDF (fallback reader)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("labelled", [False, True])
def test_pdf_fallback(tmp_path, labelled):
    pdf = fixtures.make_pdf(tmp_path / "s.pdf", with_page_labels=labelled)
    script, stats, name, notes = script_from(pdf)
    assert name == "pdf-fallback"
    assert headings(script)[-5:] == EXPECTED_HEADINGS
    assert stats["chapter_level"] == 2
    text = all_text(script)
    # furniture gone
    assert fixtures.RUNNING_HEAD not in text
    # hyphenation repaired and paragraph text unchanged
    assert fixtures.P1B in text
    assert "produc-" not in text
    # paragraph across the page break, joined with an inline marker
    paras = [b for b in script.blocks if isinstance(b, Paragraph)]
    joined = [p for p in paras if "[[page 2]]" in p.text]
    assert joined, "paragraph over the page break should carry an inline page marker"
    assert plain_text(joined[0].text).endswith(fixtures.P1C_PAGE2)
    assert "[^1]" in joined[0].text
    # footnote, figure, table, list
    fns = [b for b in script.blocks if isinstance(b, Footnote)]
    assert [(f.id, f.text) for f in fns] == [("1", fixtures.FOOTNOTE)]
    figs = [b for b in script.blocks if isinstance(b, Figure)]
    assert len(figs) == 1 and figs[0].caption == fixtures.CAPTION and figs[0].page == "2"
    assert figs[0].id == "fig-001-1"
    tables = [b for b in script.blocks if isinstance(b, Table)]
    assert tables and tables[0].rows == fixtures.TABLE and tables[0].id == "tab-002-1"
    lists = [b for b in script.blocks if isinstance(b, ListBlock)]
    assert any([i.text for i in l.items] == fixtures.BULLETS for l in lists)
    # print pages 1 to 4 marked, in order
    pages = all_pages(script)
    assert [p for p in pages if p.isdigit()] == ["1", "2", "3", "4"]
    if labelled:
        assert pages[:2] == ["i", "ii"]
    # contents page skipped
    skips = [b for b in script.blocks if isinstance(b, Skip)]
    assert skips and skips[0].reason == "contents"
    assert any("Finish Something ........ 4" in l for l in skips[0].lines)


def test_page_label_inference():
    labels = labels_from_detected(6, {3: "1", 4: "2", 5: "3", 6: "4"})
    assert labels == [None, None, "1", "2", "3", "4"]
    labels = labels_from_detected(6, {1: "i", 2: "ii", 3: "1", 4: "2", 6: "4"})
    assert labels == ["i", "ii", "1", "2", "3", "4"]
    assert resolve_labels(3, None, {}, page_offset=1)[0] == [None, "1", "2"]
    assert resolve_labels(2, None, {})[0] == [None, None]


# ---------------------------------------------------------------------------
# EPUB, DOCX, HTML, TXT, Markdown
# ---------------------------------------------------------------------------
def test_epub(epub_file):
    script, stats, name, notes = script_from(epub_file)
    assert name == "epub"
    assert headings(script) == EXPECTED_HEADINGS
    text = all_text(script)
    assert fixtures.P1A in text and fixtures.P3A in text
    assert all_pages(script) == ["1", "2", "3", "4"]
    joined = [b for b in script.blocks if isinstance(b, Paragraph) and "[[page 2]]" in b.text]
    assert joined and "[^fn1]" in joined[0].text
    fns = [b for b in script.blocks if isinstance(b, Footnote)]
    assert [(f.id, f.text) for f in fns] == [("fn1", fixtures.FOOTNOTE)]
    figs = [b for b in script.blocks if isinstance(b, Figure)]
    assert figs and figs[0].caption == fixtures.CAPTION
    tables = [b for b in script.blocks if isinstance(b, Table)]
    assert tables[0].rows == fixtures.TABLE and tables[0].header_rows == 1
    assert "Contents" not in [t for _, t in headings(script)]


def test_docx_fallback(docx_file):
    script, stats, name, _ = script_from(docx_file)
    assert name == "docx-fallback"
    assert headings(script) == EXPECTED_HEADINGS
    assert [b for b in script.blocks if isinstance(b, Table)][0].rows == fixtures.TABLE
    assert any(isinstance(b, ListBlock) and [i.text for i in b.items] == fixtures.BULLETS for b in script.blocks)


def test_html(tmp_path):
    script, _, name, _ = script_from(fixtures.make_html(tmp_path / "s.html"))
    assert name == "html-fallback"
    assert headings(script) == EXPECTED_HEADINGS


def test_txt(tmp_path):
    script, stats, name, _ = script_from(fixtures.make_txt(tmp_path / "s.txt"))
    assert name == "text"
    hs = headings(script)
    assert (1, "PROBLEMS OF BEGINNING") in hs and (2, "1. Start With a Beat") in hs
    assert stats["chapter_level"] == 2


def test_markdown(tmp_path):
    md = tmp_path / "s.md"
    md.write_text("# Part One\n\n## 1. First\n\nSome *emphasis* and a [link](http://x).[^a]\n\n"
                  "![A figure caption](f.png)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- x\n- y\n\n[^a]: Note.\n")
    script, _, name, _ = script_from(md)
    paras = [b for b in script.blocks if isinstance(b, Paragraph)]
    assert plain_text(paras[0].text) == "Some emphasis and a link."
    assert "[^a]" in paras[0].text
    assert [b for b in script.blocks if isinstance(b, Figure)][0].caption == "A figure caption"
    assert [b for b in script.blocks if isinstance(b, Table)][0].rows == [["a", "b"], ["1", "2"]]


# ---------------------------------------------------------------------------
# Docling: the neutral JSON conversion is pure Python and always tested
# ---------------------------------------------------------------------------
def test_neutral_to_items_and_build():
    neutral = {"lektor_neutral": 1, "num_pages": 3, "items": [
        {"label": "page_header", "text": "MAKING MUSIC", "page": 2, "furniture": True},
        {"label": "section_header", "text": "Problems of Beginning", "level": 1, "page": 2},
        {"label": "section_header", "text": "1. Start", "level": 1, "page": 2},
        {"label": "text", "text": "A sentence that runs over the", "page": 2},
        {"label": "page_footer", "text": "1", "page": 2, "furniture": True},
        {"label": "text", "text": "page break.", "page": 3},
        {"label": "picture", "caption": "Figure 1 A screenshot", "page": 3},
        {"label": "table", "rows": [["a", "b"], ["1", "2"]], "header_rows": 1, "page": 3},
        {"label": "list_item", "text": "• point", "page": 3},
        {"label": "footnote", "text": "1 A footnote.", "page": 3},
        {"label": "section_header", "text": "2. Next", "level": 1, "page": 3},
        {"label": "page_footer", "text": "2", "page": 3, "furniture": True},
    ]}
    assert detected_page_numbers(neutral) == {2: "1", 3: "2"}
    labels, _ = resolve_labels(3, None, detected_page_numbers(neutral))
    items = neutral_to_items(neutral, labels)
    script, stats = build_script(items, meta={}, n_pages=3)
    assert headings(script) == [(1, "Problems of Beginning"), (2, "1. Start"), (2, "2. Next")]
    paras = [b for b in script.blocks if isinstance(b, Paragraph)]
    assert paras[0].text == "A sentence that runs over the [[page 2]] page break."
    assert [b for b in script.blocks if isinstance(b, Figure)][0].caption == "Figure 1 A screenshot"
    assert [b for b in script.blocks if isinstance(b, ListBlock)][0].items[0].text == "point"
    assert [b for b in script.blocks if isinstance(b, Footnote)][0].text == "A footnote."


@pytest.mark.docling
@pytest.mark.skipif(not HAS_DOCLING, reason="Docling is not installed")
def test_docling_docx(docx_file):
    script, stats, name, _ = script_from(docx_file, reader="docling")
    assert name == "docling"
    assert headings(script) == EXPECTED_HEADINGS
    assert [b for b in script.blocks if isinstance(b, Table)][0].rows == fixtures.TABLE


# ---------------------------------------------------------------------------
# Clean-up helpers
# ---------------------------------------------------------------------------
def test_hyphen_join_rules():
    from collections import Counter

    assert join_hyphenated("produc", "tion", Counter()) == "production"
    assert join_hyphenated("music", "making", Counter({"music-making": 3})) == "music-making"
    assert join_hyphenated("TR", "808", Counter()) == "TR-808"
    assert repair_hyphenation("the produc- tion of music", Counter()) == "the production of music"
    assert repair_hyphenation("left- and right-hand", Counter()) == "left- and right-hand"


def test_heading_levels_with_rules():
    import re

    items = [RawItem("heading", "Introduction"), RawItem("para", "x"),
             RawItem("heading", "Section A"), RawItem("heading", "1. One"), RawItem("para", "x"),
             RawItem("heading", "Try this"), RawItem("para", "x"), RawItem("para", "y"), RawItem("para", "z"),
             RawItem("para", "w"), RawItem("heading", "2. Two"), RawItem("para", "x")]
    level = assign_heading_levels(items, [])
    got = [(i.level, i.text) for i in items if i.kind == "heading"]
    assert level == 2
    assert got == [(2, "Introduction"), (1, "Section A"), (2, "1. One"), (3, "Try this"), (2, "2. Two")]
    items2 = [RawItem("heading", "Try this"), RawItem("heading", "1. One"), RawItem("heading", "2. Two")]
    assign_heading_levels(items2, [(re.compile("^Try"), 3)])
    assert items2[0].level == 3


# ---------------------------------------------------------------------------
# Whole stage: files, review, protecting edits
# ---------------------------------------------------------------------------
def test_run_ingest_protects_edits(home, pdf_file):
    paths, cfg, _ = create_or_update_book(home, "sample", fixtures.TITLE, source=str(pdf_file))
    cfg.ingest.reader = "fallback"
    res = run_ingest(paths, cfg, log=lambda m: None)
    assert res.script_path == paths.script and paths.review.exists()
    script, issues = validate_file(paths.script)
    assert not has_errors(issues), issues
    assert not script_was_edited(paths)
    html = paths.review.read_text()
    assert "Heading outline" in html and "Figure 1.1" not in html.split("Heading outline")[0][:10]
    assert "1. Start With a Beat" in html
    # librarian edits the script
    paths.script.write_text(paths.script.read_text() + "\n[[note]]Edited.[[/note]]\n")
    assert script_was_edited(paths)
    res2 = run_ingest(paths, cfg, log=lambda m: None)
    assert res2.wrote_new_copy and res2.script_path.name == "script.new.md"
    assert "Edited." in paths.script.read_text()
    res3 = run_ingest(paths, cfg, force=True, log=lambda m: None)
    assert res3.script_path == paths.script and "Edited." not in paths.script.read_text()
    assert list(paths.script_dir.glob("script.backup-*.md"))
    assert "ingest" in paths.log.read_text()


def test_review_flags(tmp_path):
    from lektor.script.review import render_review
    from lektor.script.validator import validate_text

    text = ("# A\n\n## 1. One\n\n[[page 1]]\n\n[[page 2]]\n\nThe produc- tion team.\n\nRUNNING HEAD\n\n"
            "RUNNING HEAD\n\nRUNNING HEAD\n\n[[figure id=f page=2]]\nCaption:\n[[/figure]]\n\n## 3. Three\n\n"
            "Odd � char.\n")
    script, issues = validate_text(text)
    script.meta["chapter-level"] = "2"
    html = render_review(script, issues)
    for expected in ["Broken hyphenation", "Possible header or footer", "Figure without caption",
                     "Page with no text", "Numbering gap", "Odd character"]:
        assert expected in html, expected
