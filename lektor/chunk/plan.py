"""Turn a script into synthesis units (``units.jsonl``).

A **unit** is what is sent to the voice in one call: one or more whole
sentences (or, for a very long sentence, clauses of it), never crossing a
paragraph boundary. Each unit records:

- a stable id ``cCCC-pPPPP-uUU`` (chapter, block within the chapter, unit)
- its sentences, each with **display** text (as written) and **spoken** text
- the print page(s) that start in it
- the boundary that follows it: sentence, paragraph, section (a heading
  follows), heading (it is a heading) or chapter (the chapter ends)
- its voice role (main, or secondary for anything the library added) and
  the voice preset, engine, parameters and seed
- a cache key: a hash of spoken text, engine, preset, parameters, seed and
  normaliser version. Synthesis is skipped when the key is already cached.

Chapters: a heading at or above the chapter level starts a new chapter (an
audio file and an M4B chapter). A heading directly followed by another
chapter heading (``# Part One`` then ``## 1. Strategy``) shares its chapter.
Chapter 0 is always the spoken front matter.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..script.model import (
    INLINE_PAGE_RE,
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
    split_inline,
)
from ..speech.normaliser import NON_TERMINAL_ABBREVIATIONS, NORMALISER_VERSION, Normaliser
from .sentences import split_long, split_sentences

PAGE_SENTINEL = "\U000E0002"
BOUNDARIES = ("sentence", "paragraph", "section", "heading", "chapter")


@dataclass
class Sentence:
    sid: str
    display: str
    spoken: str
    pages: list[str] = field(default_factory=list)
    part: int = 0  # >0 when a long sentence was split into clauses


@dataclass
class Unit:
    id: str
    chapter: int
    chapter_title: str
    kind: str  # heading, paragraph, list_item, figure, table, table_row, footnote, note, frontmatter
    role: str  # main or secondary
    preset: str
    engine: str
    sentences: list[Sentence]
    boundary: str = "sentence"
    heading_level: Optional[int] = None
    page: Optional[str] = None  # print page current at the start of the unit
    block: int = 0
    line: int = 0
    ref: str = ""  # figure / table / footnote id
    params: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    cache_key: str = ""

    @property
    def spoken(self) -> str:
        return " ".join(s.spoken for s in self.sentences).strip()

    @property
    def display(self) -> str:
        return " ".join(s.display for s in self.sentences).strip()

    @property
    def pages(self) -> list[str]:
        return [p for s in self.sentences for p in s.pages]

    def to_json(self) -> dict:
        d = asdict(self)
        d["spoken"] = self.spoken
        d["display"] = self.display
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Unit":
        d = dict(d)
        d.pop("spoken", None)
        d.pop("display", None)
        d["sentences"] = [Sentence(**s) for s in d["sentences"]]
        return cls(**d)


@dataclass
class VoiceChoice:
    """What the planner needs to know about a preset."""

    name: str
    engine: str
    limit: int
    fingerprint: str
    params: dict[str, Any] = field(default_factory=dict)
    vary: dict[str, float] = field(default_factory=dict)


@dataclass
class PlanOptions:
    chapter_level: int = 1
    footnotes: str = "end_of_paragraph"
    endnotes: str = "end_of_chapter"
    uncaptioned_figures: str = "page"
    max_table_cells: int = 40
    front_matter: list[str] = field(default_factory=list)  # sentences, already filled in
    front_matter_title: str = "About this audiobook"


def stable_int(*parts: Any, bits: int = 31) -> int:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:12], 16) % (2 ** bits)


def cache_key(spoken: str, voice: VoiceChoice, params: dict, seed: int) -> str:
    payload = json.dumps([spoken, voice.engine, voice.name, voice.fingerprint, params, seed, NORMALISER_VERSION],
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


class Planner:
    def __init__(self, normaliser: Normaliser, main: VoiceChoice, secondary: VoiceChoice,
                 options: PlanOptions) -> None:
        self.norm = normaliser
        self.voices = {"main": main, "secondary": secondary}
        self.opt = options
        self.units: list[Unit] = []
        self.chapter = 0
        self.chapter_title = options.front_matter_title
        self.chapter_has_content = False
        self.block = 0
        self.pending_pages: list[str] = []
        self.current_page: Optional[str] = None
        self.sentence_no = 0
        self.footnotes: dict[str, Footnote] = {}
        self.read_notes: set[str] = set()
        self.chapter_notes: list[Footnote] = []
        self.protected_words = list(NON_TERMINAL_ABBREVIATIONS) + [
            e.written for e in normaliser.lexicon.entries if not e.is_regex and "." in e.written]

    # -- helpers -----------------------------------------------------------
    def _new_block(self) -> None:
        self.block += 1

    def _sid(self) -> str:
        self.sentence_no += 1
        return f"s{self.sentence_no:06d}"

    def _sentences(self, text: str, heading: bool = False, voice: str = "main") -> list[Sentence]:
        """Split block text (which may contain inline page markers and
        footnote references) into sentences with display and spoken text."""
        pieces = []
        for kind, val in split_inline(text):
            if kind == "text":
                pieces.append(val.replace("\\[[", "[[").replace("\\[^", "[^"))
            elif kind == "page":
                pieces.append(f" {PAGE_SENTINEL}{val}{PAGE_SENTINEL} ")
        flat = re.sub(r"\s+", " ", "".join(pieces)).strip()
        # Pull page labels out, remembering which sentence they fall in.
        labels = re.findall(f"{PAGE_SENTINEL}([^{PAGE_SENTINEL}]+){PAGE_SENTINEL}", flat)
        marked = re.sub(f"{PAGE_SENTINEL}[^{PAGE_SENTINEL}]+{PAGE_SENTINEL}", PAGE_SENTINEL, flat)
        raw = [marked] if heading else split_sentences(marked, self.protected_words)
        limit = self.voices[voice].limit
        out: list[Sentence] = []
        label_iter = iter(labels)
        for r in raw:
            n_markers = r.count(PAGE_SENTINEL)
            pages = [next(label_iter) for _ in range(n_markers)]
            display = re.sub(r"\s+", " ", r.replace(PAGE_SENTINEL, " ")).strip()
            # A marker at the very end of a sentence belongs to the next one.
            if pages and r.rstrip().endswith(PAGE_SENTINEL) and r is not raw[-1]:
                carry = pages.pop()
            else:
                carry = None
            if not display:
                if pages:
                    self.pending_pages.extend(pages)
                continue
            pages = self.pending_pages + pages
            self.pending_pages = []
            if carry:
                self.pending_pages.append(carry)
            spoken = self.norm(display, heading=heading)
            parts = split_long(display, limit, measure=lambda s: len(self.norm(s, heading=heading)))
            sid = self._sid()
            if len(parts) == 1:
                out.append(Sentence(sid, display, spoken, pages))
            else:
                for k, part in enumerate(parts, 1):
                    out.append(Sentence(sid, part, self.norm(part, heading=heading), pages if k == 1 else [], k))
            if pages:
                self.current_page = pages[-1]
        return out

    def _emit(self, kind: str, sentences: list[Sentence], role: str, boundary: str = "paragraph",
              heading_level: Optional[int] = None, line: int = 0, ref: str = "") -> None:
        """Group sentences into units no longer than the voice's limit."""
        if not sentences:
            return
        if self.chapter == 0 and kind != "frontmatter":
            # The book's own opening pages (before its first chapter heading)
            # get their own chapter after the spoken front matter.
            self._start_chapter("Opening pages")
        if kind != "heading":
            self.chapter_has_content = True
        voice = self.voices[role]
        limit = voice.limit
        groups: list[list[Sentence]] = []
        for s in sentences:
            if groups and kind != "heading":
                cur = groups[-1]
                cur_len = sum(len(x.spoken) + 1 for x in cur)
                if cur_len + len(s.spoken) <= limit:
                    cur.append(s)
                    continue
            groups.append([s])
        # Seeded variation per block (paragraph), from its text, so it is
        # the same on every run and does not move when other blocks change.
        block_text = " ".join(s.display for s in sentences)
        rnd = random.Random(stable_int(block_text, voice.name))
        params = dict(voice.params)
        for p, amount in voice.vary.items():
            base = float(params.get(p, 0.0))
            params[p] = round(base + rnd.uniform(-amount, amount), 4)
        for i, g in enumerate(groups):
            page = (g[0].pages[0] if g[0].pages else self.current_page)
            u = Unit(
                id=f"c{self.chapter:03d}-p{self.block:04d}-u{i + 1:02d}",
                chapter=self.chapter, chapter_title=self.chapter_title, kind=kind, role=role,
                preset=voice.name, engine=voice.engine, sentences=g,
                boundary="sentence" if i < len(groups) - 1 else boundary,
                heading_level=heading_level, page=page, block=self.block, line=line, ref=ref,
                params=params,
            )
            u.seed = stable_int(u.spoken, voice.name)
            u.cache_key = cache_key(u.spoken, voice, params, u.seed)
            self.units.append(u)
        self._new_block()

    def _set_last_boundary(self, boundary: str) -> None:
        if self.units:
            order = {b: i for i, b in enumerate(BOUNDARIES)}
            last = self.units[-1]
            if last.boundary != "heading" or boundary == "chapter":
                if order[boundary] > order.get(last.boundary, 0) or boundary == "chapter":
                    last.boundary = boundary

    # -- chapters ----------------------------------------------------------
    def _start_chapter(self, title: str) -> None:
        self._flush_chapter_notes()
        self._set_last_boundary("chapter")
        self.chapter += 1
        self.chapter_title = title
        self.chapter_has_content = False
        self.block = 0

    def _flush_chapter_notes(self) -> None:
        notes, self.chapter_notes = self.chapter_notes, []
        for fn in notes:
            self._footnote(fn)

    # -- block handlers ----------------------------------------------------
    def front_matter(self) -> None:
        for text in self.opt.front_matter:
            if text.strip():
                self._emit("frontmatter", self._sentences(text, voice="secondary"), "secondary", "paragraph")

    def heading(self, b: Heading) -> None:
        if b.level <= self.opt.chapter_level:
            if self.chapter == 0 or self.chapter_has_content:
                self._start_chapter(b.text)
            else:
                # Two chapter-level headings in a row share a chapter.
                self.chapter_title = f"{self.chapter_title}: {b.text}"
                for u in self.units:
                    if u.chapter == self.chapter:
                        u.chapter_title = self.chapter_title
        else:
            self._set_last_boundary("section")
        sents = self._sentences(b.text, heading=True)
        self._emit("heading", sents, "main", "heading", heading_level=b.level, line=b.line)

    def paragraph(self, text: str, kind: str, line: int) -> None:
        refs = [v for k, v in split_inline(text) if k == "fnref"]
        self._emit(kind, self._sentences(text), "main", "paragraph", line=line)
        for r in refs:
            fn = self.footnotes.get(r)
            if fn is None or r in self.read_notes:
                continue
            mode = self.opt.endnotes if fn.endnote else self.opt.footnotes
            if mode == "skip":
                self.read_notes.add(r)
            elif mode == "end_of_paragraph":
                self._footnote(fn)
            else:
                self.chapter_notes.append(fn)
                self.read_notes.add(r)

    def _footnote(self, fn: Footnote) -> None:
        self.read_notes.add(fn.id)
        sents = self._sentences("Note: " + fn.text, voice="secondary")
        self._emit("footnote", sents, "secondary", "paragraph", line=fn.line, ref=fn.id)

    def figure(self, b: Figure) -> None:
        texts = []
        if b.caption:
            cap = b.caption.strip()
            texts.append(cap if re.match(r"(?i)fig(ure)?\b|fig\.", cap) else f"Figure. {cap}")
        if b.description:
            texts.append(f"Description: {b.description.strip()}")
        if not texts and self.opt.uncaptioned_figures == "page":
            page = b.page or self.current_page
            texts.append(f"Figure on page {page}." if page else "Figure.")
        if texts:
            sents = [s for t in texts for s in self._sentences(t, voice="secondary")]
            self._emit("figure", sents, "secondary", "paragraph", line=b.line, ref=b.id)

    def table(self, b: Table) -> None:
        page = b.page or self.current_page
        cap = (b.caption.strip() + " ") if b.caption else ""
        if b.n_cells > self.opt.max_table_cells or not b.rows:
            where = f"see page {page} of the print book" if page else "see the print book"
            self._emit("table", self._sentences(f"Table. {cap}A table is omitted here; {where}.", voice="secondary"),
                       "secondary", "paragraph", line=b.line, ref=b.id)
            return
        headers = b.rows[0] if b.header_rows else []
        body = b.rows[b.header_rows:] if b.header_rows else b.rows
        ncols = max(len(r) for r in b.rows)
        intro = f"Table. {cap}{len(body)} rows, {ncols} columns."
        self._emit("table", self._sentences(intro, voice="secondary"), "secondary", "paragraph", line=b.line, ref=b.id)
        for row in body:
            if headers and len(headers) == len(row):
                cells = [f"{h}: {c}" if h else c for h, c in zip(headers, row) if c]
            else:
                cells = [c for c in row if c]
            text = "; ".join(cells)
            if text and not text.endswith("."):
                text += "."
            self._emit("table_row", self._sentences(text), "main", "paragraph", line=b.line, ref=b.id)

    def note(self, b: Note) -> None:
        self._emit("note", self._sentences(b.text, voice="secondary"), "secondary", "paragraph", line=b.line)

    # -- main ----------------------------------------------------------------
    def plan(self, script: Script) -> list[Unit]:
        self.footnotes = script.footnotes()
        referenced = {v for b in script.blocks for _, t in _texts(b) for k, v in split_inline(t) if k == "fnref"}
        self.front_matter()
        for b in script.blocks:
            if isinstance(b, PageMarker):
                self.pending_pages.append(b.label)
            elif isinstance(b, Heading):
                self.heading(b)
            elif isinstance(b, Paragraph):
                self.paragraph(b.text, "paragraph", b.line)
            elif isinstance(b, ListBlock):
                for item in b.items:
                    self.paragraph(item.text, "list_item", item.line or b.line)
            elif isinstance(b, Figure):
                self.figure(b)
            elif isinstance(b, Table):
                self.table(b)
            elif isinstance(b, Note):
                self.note(b)
            elif isinstance(b, Footnote):
                mode = self.opt.endnotes if b.endnote else self.opt.footnotes
                if b.id not in referenced and mode != "skip" and b.id not in self.read_notes:
                    self._footnote(b)  # unreferenced notes are read where they stand
            elif isinstance(b, Skip):
                for line in b.lines:
                    m = re.fullmatch(r"\s*\[\[page\s+([^\]\s]+)\s*\]\]\s*", line)
                    if m:
                        self.pending_pages.append(m.group(1))
            elif isinstance(b, Comment):
                pass
        self._flush_chapter_notes()
        self._set_last_boundary("chapter")
        if self.pending_pages and self.units:
            # Pages at the very end with nothing after them attach to the last unit.
            self.units[-1].sentences[-1].pages.extend(self.pending_pages)
            self.pending_pages = []
        return self.units


def _texts(b):
    if isinstance(b, (Paragraph, Note)):
        yield b.line, b.text
    elif isinstance(b, ListBlock):
        for i in b.items:
            yield i.line, i.text


def write_units(units: list[Unit], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for u in units:
            f.write(json.dumps(u.to_json(), ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_units(path: Path) -> list[Unit]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(Unit.from_json(json.loads(line)))
    return out


def chapters_of(units: list[Unit]) -> list[tuple[int, str, list[Unit]]]:
    out: dict[int, list[Unit]] = {}
    titles: dict[int, str] = {}
    for u in units:
        out.setdefault(u.chapter, []).append(u)
        titles[u.chapter] = u.chapter_title
    return [(c, titles[c], out[c]) for c in sorted(out)]
