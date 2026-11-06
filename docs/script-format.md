# The Lektor script format

`script/script.md` is the single source of truth for a book. Everything that
follows (speech, audio, DAISY, M4B) is made from it, so fixing something
here fixes it everywhere.

It is a small, plain-text format based on Markdown. You can edit it in
Colab's file editor (notebook section 3). After editing, run *Check the
script again*: the validator tells you about any problem, with its line
number, in plain English.

> **The golden rule.** Never rewrite, summarise or reorder the author's
> words. You may correct conversion mistakes (a word split at a line end, a
> running header that slipped through, a heading at the wrong level), and you
> may add clearly marked library notes. See
> [accessibility-and-copyright.md](accessibility-and-copyright.md).

## A short example

```
---
lektor-script: 1
title: Making Music
chapter-level: 2
---

# Problems of Beginning

## 12. Strategy title

[[page 47]]

Paragraph text exactly as the author wrote it. A paragraph can run over
several lines; a blank line ends it.

Another paragraph, with a footnote reference.[^3] [[page 48]] The rest of
the paragraph is on page 48.

- A bullet point
- Another bullet point

[[figure id=fig-012-1 page=48]]
Caption: Figure 12.1 The piano roll view.
Description:
[[/figure]]

[[note]]A note added by the library.[[/note]]

[^3]: The footnote text.
```

## Everything the format can express

### Front matter

The lines between the first two `---` lines. Lektor writes them; you rarely
need to change them. `chapter-level` records which heading level becomes a
chapter (it can also be set in `book.yaml`).

### Headings: `#` to `######`

One `#` is the top level (for example a part of the book), two `##` the next
level down, and so on. There must be a space after the `#` signs.

Headings become **navigation points** in DAISY (EasyReader lets the
listener move by heading level). Headings at the chapter level also become
**chapters** in the M4B audiobook and each starts a new audio file.

To move a heading up or down a level, change the number of `#` signs.
Avoid skipping levels (`#` followed directly by `###`); the validator warns
about it.

### Print page markers: `[[page 47]]`

Silent markers saying where a print page starts. They let the listener go to
a page by its print number, which matters in class ("turn to page 47").

- On a line of its own: the page starts before the next block.
- Inside a paragraph: the page starts at that point in the paragraph.

The page label is whatever is printed on the page: `47`, `xii`, `A3`. It
cannot contain spaces.

### Paragraphs

Ordinary lines of text. Consecutive lines are joined into one paragraph; a
blank line ends it. Lektor writes each paragraph on one long line.

### Lists

Bullets start with `- ` (or `* ` or `+ `). Numbered items start with `1. ` or
`1) `. A line indented by two spaces continues the item above.

### Figures

```
[[figure id=fig-012-1 page=48]]
Caption: Figure 12.1 The piano roll view.
Description: A grid of notes rising from left to right, showing a C minor arpeggio.
[[/figure]]
```

- `id` must be unique. Lektor makes them as `fig-<chapter>-<number>`.
- `page` is the print page the figure is on.
- **Caption** is the author's caption. It is read in the secondary voice,
  preceded by "Figure.".
- **Description** is optional. Type a description here and it is read after
  the caption, also in the secondary voice. If the book supplied its own
  description (EPUB alt text), Lektor fills it in and adds
  `described-by=publisher` to the tag.
- A figure with neither caption nor description is either silent or
  announced as "Figure on page 48" (the `speech.uncaptioned_figures` setting).

### Tables

```
[[table id=tab-003-1 page=50 header=1]]
| Element | Level | Pan |
|---|---|---|
| Kick | -6 dB | Centre |
Caption: Table 3.1 Starting levels.
[[/table]]
```

Rows start with `|` and cells are separated by `|`. The `|---|` line marks the
end of the header rows (or use `header=` with the number of header rows). To
put a `|` inside a cell, write `\|`.

Small tables are read row by row with their column headings. Tables with
more cells than `speech.max_table_cells` are replaced by a spoken notice:
"A table is omitted here; see page 50 of the print book."

### Footnotes and endnotes

A reference in the text: `[^3]`. The note itself, on a line of its own:

```
[^3]: The footnote text.
```

Endnotes are notes inside an endnotes block:

```
[[endnotes]]
[^e1]: First endnote.
[^e2]: Second endnote.
[[/endnotes]]
```

Notes are read in the secondary voice as "Note: ...". Whether they are read
at the end of the paragraph, at the end of the chapter, or not at all is set
in `book.yaml` (`speech.footnotes` and `speech.endnotes`).

### Library notes: `[[note]]...[[/note]]`

Words added by the library, read in the **secondary voice** so the listener
always knows they are not the author's. Use them sparingly, for example to
explain that a figure is a screenshot of a particular screen.

```
[[note]]The following figure is a screenshot of the Session View.[[/note]]
```

A note can also span several lines:

```
[[note]]
First line of the note
and its continuation.
[[/note]]
```

### Skip blocks: `[[skip]]...[[/skip]]`

Text kept in the file but **not read aloud**: the table of contents, the
index, a page of advertisements. Lektor puts the contents and index in skip
blocks automatically. You can add a reason:

```
[[skip reason="index"]]
...
[[/skip]]
```

Anything inside a skip block is ignored completely, including tags.

### Comments: `<!-- ... -->`

Notes to yourself. Kept in the file, never read, never shown to the student.

```
<!-- Check this paragraph against the print copy. -->
```

## When text looks like markup: the backslash

If the author's text itself starts with something Lektor would read as
markup (a `#`, a `- `, `12. `, `|`, `[[`, `Caption:` ...), put a backslash in
front of it. Lektor removes one leading backslash when reading.

```
\# This line is ordinary text, not a heading.
\12. This paragraph starts with a number but is not a list item.
```

Inside a paragraph, a literal `[[` or `[^` that is part of the book's text is
written `\[[` or `\[^`. Lektor writes these escapes for you when it converts
a book; you only need them for text you type yourself.

## What the validator checks

Errors (audio will not be made until they are fixed):

- a figure, table, note, skip, endnotes block or comment that is never closed
- a closing tag with no matching opening tag
- an unknown tag such as `[[picture]]`
- a page marker written incorrectly
- a footnote reference with no footnote text
- two figures or tables with the same id
- a script with nothing to read

Warnings (worth a look):

- heading level jumps, and no headings at the chapter level
- `#` without a space after it
- page numbers that go backwards or appear twice
- table rows with different numbers of cells
- `Caption:` outside a figure or table, `|` rows outside a table
- footnotes that are never referred to

Messages always start with the line number, for example
`Line 412: a figure block was opened but never closed. Add [[/figure]] where it should end.`
