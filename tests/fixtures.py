"""Test fixtures generated at test time.

The repository never contains book content (see CLAUDE.md), so the test
documents are built here from short passages written for Lektor's tests.
They imitate the features that matter in a real book: numbered strategies
grouped under section headings, running headers, printed page numbers that
differ from the physical page, line end hyphenation, a paragraph that runs
over a page break, a footnote, a figure with a caption, a ruled table and
a bullet list.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

TITLE = "Patterns for Practice"
AUTHOR = "Lektor Test Author"

P1A = ("Every session starts with a blank arrangement and a tempo of 120 BPM. Begin with a simple "
       "four on the floor kick drum and listen for a few bars before adding anything else.")
P1B_LINES = [
    "The real challenge is not the first sound but the decision about what to keep. Try the produc-",
    "tion trick of muting every track except one, then bring the others back one at a time and",
    "notice which part carries the groove.",
]
P1B = ("The real challenge is not the first sound but the decision about what to keep. Try the production "
       "trick of muting every track except one, then bring the others back one at a time and notice which "
       "part carries the groove.")
P1C_PAGE1 = "A good beat often hides in the space between the hits, so leave room for the hi hats and"
P1C_PAGE2 = "let the snare land on beats two and four of a bar in 4/4 time."
FOOTNOTE = "Many dance producers begin at 124 BPM instead."
CAPTION = "Figure 1.1 A two bar drum pattern shown in the piano roll."
P2A = "Pick a finished track you admire and load it into your DAW next to your own sketch."
TABLE = [["Element", "Level", "Pan"], ["Kick", "-6 dB", "Centre"], ["Bass", "-8 dB", "Centre"], ["Pad", "-14 dB", "Wide"]]
BULLETS = ["Compare the low end first.", "Then compare the overall loudness.", "Finally compare the stereo width."]
P3A = ("Set a deadline and export a version of the track at the end of every session, even if it is "
       "rough. A finished sketch teaches more than a perfect loop.")
RUNNING_HEAD = "PATTERNS FOR PRACTICE"


def make_pdf(path: Path, with_page_labels: bool = False) -> Path:
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    w, h = A5
    c = canvas.Canvas(str(path), pagesize=A5)
    c.setTitle(TITLE)
    c.setAuthor(AUTHOR)
    L = 50
    R = w - 50

    def lines(y, texts, font="Times-Roman", size=11, lead=14):
        c.setFont(font, size)
        for t in texts:
            c.drawString(L, y, t)
            y -= lead
        return y

    def para(y, text, font="Times-Roman", size=11, lead=14, gap=8):
        from reportlab.lib.utils import simpleSplit

        y = lines(y, simpleSplit(text, font, size, R - L), font, size, lead)
        return y - gap

    def furniture(printed):
        c.setFont("Helvetica", 8)
        c.drawCentredString(w / 2, h - 30, RUNNING_HEAD)
        c.setFont("Times-Roman", 9)
        c.drawCentredString(w / 2, 25, str(printed))

    # Physical page 1: title page
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(w / 2, h / 2 + 40, TITLE)
    c.setFont("Helvetica", 12)
    c.drawCentredString(w / 2, h / 2, AUTHOR)
    c.showPage()

    # Physical page 2: contents
    c.setFont("Helvetica-Bold", 16)
    c.drawString(L, h - 80, "Contents")
    y = lines(h - 110, ["1. Start With a Beat ........ 1", "2. Use a Reference Track ........ 3",
                        "3. Finish Something ........ 4"])
    c.showPage()

    # Physical page 3 = printed page 1
    furniture(1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(L, h - 80, "Problems of Beginning")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(L, h - 115, "1. Start With a Beat")
    y = para(h - 140, P1A)
    y = lines(y, P1B_LINES) - 8
    # Paragraph with a superscript footnote reference, running over the page.
    from reportlab.lib.utils import simpleSplit

    first = "Most tracks in this style sit near 120 BPM."
    c.setFont("Times-Roman", 11)
    c.drawString(L, y, first)
    x = L + c.stringWidth(first, "Times-Roman", 11)
    c.setFont("Times-Roman", 7)
    c.drawString(x + 0.5, y + 4, "1")
    y -= 14
    y = lines(y, simpleSplit(P1C_PAGE1, "Times-Roman", 11, R - L))
    # footnote
    c.setFont("Times-Roman", 9)
    c.drawString(L, 60, "1 " + FOOTNOTE)
    c.showPage()

    # Physical page 4 = printed page 2
    furniture(2)
    y = lines(h - 70, simpleSplit(P1C_PAGE2, "Times-Roman", 11, R - L)) - 10
    # A figure: a simple raster image
    img = _png_bytes()
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(io.BytesIO(img)), L, y - 150, width=200, height=140)
    c.setFont("Times-Italic", 9)
    c.drawString(L, y - 165, CAPTION)
    c.showPage()

    # Physical page 5 = printed page 3
    furniture(3)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(L, h - 80, "2. Use a Reference Track")
    y = para(h - 105, P2A)
    # ruled table
    col = [L, L + 110, L + 200, L + 290]
    row_h = 18
    top = y - 5
    c.setLineWidth(0.5)
    for r, row in enumerate(TABLE):
        yy = top - r * row_h
        c.setFont("Helvetica-Bold" if r == 0 else "Helvetica", 9)
        for k, cell in enumerate(row):
            c.drawString(col[k] + 4, yy - 13, cell)
    for r in range(len(TABLE) + 1):
        c.line(col[0], top - r * row_h, col[-1], top - r * row_h)
    for x in col:
        c.line(x, top, x, top - len(TABLE) * row_h)
    y = top - len(TABLE) * row_h - 25
    c.setFont("Times-Roman", 11)
    for b in BULLETS:
        c.drawString(L, y, "•  " + b)
        y -= 16
    c.showPage()

    # Physical page 6 = printed page 4
    furniture(4)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(L, h - 80, "Problems of Finishing")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(L, h - 115, "3. Finish Something")
    para(h - 140, P3A)
    c.showPage()
    c.save()

    if with_page_labels:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(path))
        writer = PdfWriter()
        writer.append(reader)
        writer.set_page_label(0, 1, style="/r", start=1)
        writer.set_page_label(2, 5, style="/D", start=1)
        with open(path, "wb") as f:
            writer.write(f)
    return path


def _png_bytes() -> bytes:
    """A tiny generated PNG (grey squares), built without Pillow."""
    import struct
    import zlib

    width, height = 64, 40
    raw = b""
    for y in range(height):
        raw += b"\x00" + bytes(((x // 8 + y // 8) % 2) * 200 + 30 for x in range(width))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_docx(path: Path) -> Path:
    import docx

    d = docx.Document()
    d.core_properties.title = TITLE
    d.core_properties.author = AUTHOR
    d.add_heading("Problems of Beginning", level=1)
    d.add_heading("1. Start With a Beat", level=2)
    d.add_paragraph(P1A)
    d.add_paragraph(P1B)
    d.add_heading("2. Use a Reference Track", level=2)
    d.add_paragraph(P2A)
    t = d.add_table(rows=len(TABLE), cols=3)
    for r, row in enumerate(TABLE):
        for k, cell in enumerate(row):
            t.cell(r, k).text = cell
    for b in BULLETS:
        d.add_paragraph(b, style="List Bullet")
    d.add_heading("Problems of Finishing", level=1)
    d.add_heading("3. Finish Something", level=2)
    d.add_paragraph(P3A)
    d.save(str(path))
    return path


def make_epub(path: Path) -> Path:
    """A minimal EPUB 3 with page break markers and a footnote."""
    chap1 = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><title>Beginning</title></head>
<body>
<section>
<h1>Problems of Beginning</h1>
<span epub:type="pagebreak" id="page1" title="1"/>
<h2>1. Start With a Beat</h2>
<p>{P1A}</p>
<p>{P1B}</p>
<p>Most tracks in this style sit near 120 BPM.<a epub:type="noteref" href="#fn1">1</a> {P1C_PAGE1}
<span epub:type="pagebreak" id="page2" title="2"/>{P1C_PAGE2}</p>
<figure><img src="fig1.png" alt=""/><figcaption>{CAPTION}</figcaption></figure>
<aside epub:type="footnote" id="fn1"><p>1 {FOOTNOTE}</p></aside>
</section>
</body></html>"""
    rows = "".join(
        "<tr>" + "".join((f"<th>{c}</th>" if r == 0 else f"<td>{c}</td>") for c in row) + "</tr>"
        for r, row in enumerate(TABLE)
    )
    items = "".join(f"<li>{b}</li>" for b in BULLETS)
    chap2 = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><title>Reference</title></head>
<body>
<div role="doc-pagebreak" id="page3" aria-label="3"></div>
<h2>2. Use a Reference Track</h2>
<p>{P2A}</p>
<table>{rows}</table>
<ul>{items}</ul>
<span epub:type="pagebreak" id="page4" title="4"/>
<h1>Problems of Finishing</h1>
<h2>3. Finish Something</h2>
<p>{P3A}</p>
</body></html>"""
    nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head><body>
<nav epub:type="toc"><h1>Contents</h1><ol><li><a href="ch1.xhtml">Beginning</a></li>
<li><a href="ch2.xhtml">Reference</a></li></ol></nav></body></html>"""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000001</dc:identifier>
<dc:title>{TITLE}</dc:title><dc:creator>{AUTHOR}</dc:creator><dc:language>en</dc:language>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
<item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
<item id="fig1" href="fig1.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="nav" linear="no"/><itemref idref="ch1"/><itemref idref="ch2"/></spine>
</package>"""
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/ch1.xhtml", chap1)
        z.writestr("OEBPS/ch2.xhtml", chap2)
        z.writestr("OEBPS/fig1.png", _png_bytes())
    return path


def make_html(path: Path) -> Path:
    path.write_text(f"""<!doctype html><html><head><title>{TITLE}</title></head><body>
<h1>Problems of Beginning</h1><h2>1. Start With a Beat</h2><p>{P1A}</p><p>{P1B}</p>
<h2>2. Use a Reference Track</h2><p>{P2A}</p>
<ul>{''.join(f'<li>{b}</li>' for b in BULLETS)}</ul>
<h1>Problems of Finishing</h1><h2>3. Finish Something</h2><p>{P3A}</p></body></html>""", encoding="utf-8")
    return path


def make_txt(path: Path) -> Path:
    path.write_text(f"""{TITLE}

PROBLEMS OF BEGINNING

1. Start With a Beat

{P1A}

{P1B}

2. Use a Reference Track

{P2A}

PROBLEMS OF FINISHING

3. Finish Something

{P3A}
""", encoding="utf-8")
    return path
