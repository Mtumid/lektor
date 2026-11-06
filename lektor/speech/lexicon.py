"""Pronunciation lexicons.

A lexicon is a CSV file with the columns::

    written, spoken, case_sensitive, whole_word, is_regex, notes

- ``written``: the text as it appears in the book (or a regular expression)
- ``spoken``: what the voice should say instead
- ``case_sensitive``: yes/no. Blank means "yes if ``written`` has a capital letter"
- ``whole_word``: yes/no (default yes): only match whole words
- ``is_regex``: yes/no (default no): ``written`` is a Python regular expression;
  ``spoken`` may use ``\\1`` for groups
- ``notes``: anything; ignored by Lektor

Load order (later entries win when ``written`` is the same):

1. built in defaults shipped inside Lektor (``lektor/data/lexicons/_*.csv``)
2. shared lexicons in ``MyDrive/Lektor/lexicons/*.csv`` (alphabetical)
3. the book's ``script/lexicon_suggestions.csv`` (only rows with a spoken form)
4. the book's ``script/lexicon.csv``

A row whose ``spoken`` is empty is ignored, so a half-finished suggestions
file is harmless. To stop an earlier entry applying, give it the spoken
form ``=`` (meaning "say it as written").
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..errors import ConfigError
from .protect import Protector

COLUMNS = ["written", "spoken", "case_sensitive", "whole_word", "is_regex", "notes"]
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "data" / "lexicons"
_WORD_CHARS = "A-Za-z0-9"


def _bool(value: Optional[str], default: Optional[bool]) -> Optional[bool]:
    if value is None:
        return default
    v = value.strip().lower()
    if v == "":
        return default
    if v in ("y", "yes", "true", "1", "t"):
        return True
    if v in ("n", "no", "false", "0", "f"):
        return False
    raise ValueError(f"'{value}' should be yes or no")


@dataclass
class Entry:
    written: str
    spoken: str
    case_sensitive: bool
    whole_word: bool
    is_regex: bool
    notes: str = ""
    source: str = ""
    line: int = 0

    @property
    def key(self) -> tuple:
        return (self.written if self.case_sensitive else self.written.lower(), self.is_regex)

    def pattern(self) -> str:
        core = self.written if self.is_regex else re.escape(self.written)
        if self.whole_word:
            core = f"(?<![{_WORD_CHARS}])(?:{core})(?![{_WORD_CHARS}])"
        return core


def read_lexicon_csv(path: Path, only_with_spoken: bool = True) -> list[Entry]:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    if "written" not in header or "spoken" not in header:
        raise ConfigError(f"{path.name} does not start with a header row containing 'written' and 'spoken'.",
                          hint="The first line should be: " + ",".join(COLUMNS))
    idx = {h: i for i, h in enumerate(header)}
    out: list[Entry] = []
    problems: list[str] = []
    for n, row in enumerate(rows[1:], start=2):
        if not row or not any(c.strip() for c in row):
            continue
        get = lambda col: row[idx[col]] if col in idx and idx[col] < len(row) else None  # noqa: E731
        written = (get("written") or "").strip()
        spoken = (get("spoken") or "").strip()
        if not written or written.startswith("#"):
            continue
        if not spoken and only_with_spoken:
            continue
        try:
            is_regex = _bool(get("is_regex"), False)
            whole = _bool(get("whole_word"), True)
            cs = _bool(get("case_sensitive"), None)
        except ValueError as e:
            problems.append(f"line {n}: {e}")
            continue
        if cs is None:
            cs = any(ch.isupper() for ch in written)
        if is_regex:
            try:
                re.compile(written)
            except re.error as e:
                problems.append(f"line {n}: the regular expression '{written}' is not valid ({e})")
                continue
        out.append(Entry(written, spoken, cs, whole, is_regex, (get("notes") or "").strip(), path.name, n))
    if problems:
        raise ConfigError(f"{path.name} has {len(problems)} problem(s): " + "; ".join(problems),
                          hint=f"Open {path.name}, fix the line(s) mentioned and save it.")
    return out


class Lexicon:
    """A merged set of entries, applied longest-first."""

    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        merged: dict[tuple, Entry] = {}
        for e in entries:
            merged.pop(e.key, None)  # keep insertion order of the winner
            merged[e.key] = e
        self.entries = [e for e in merged.values() if e.spoken]
        self._compile()

    def _compile(self) -> None:
        plain = sorted((e for e in self.entries if not e.is_regex), key=lambda e: -len(e.written))
        self._plain_cs = {e.written: e for e in plain if e.case_sensitive}
        self._plain_ci = {e.written.lower(): e for e in plain if not e.case_sensitive}
        parts_cs = [e.pattern() for e in plain if e.case_sensitive]
        parts_ci = [e.pattern() for e in plain if not e.case_sensitive]
        self._re_cs = re.compile("|".join(parts_cs)) if parts_cs else None
        self._re_ci = re.compile("|".join(parts_ci), re.I) if parts_ci else None
        self._regex = [(re.compile(e.pattern(), 0 if e.case_sensitive else re.I), e)
                       for e in self.entries if e.is_regex]

    def __len__(self) -> int:
        return len(self.entries)

    def digest(self) -> str:
        h = hashlib.sha256()
        for e in self.entries:
            h.update(repr((e.written, e.spoken, e.case_sensitive, e.whole_word, e.is_regex)).encode())
        return h.hexdigest()[:12]

    def lookup(self, written: str) -> Optional[Entry]:
        return self._plain_cs.get(written) or self._plain_ci.get(written.lower())

    def apply(self, text: str, prot: Protector, hits: Optional[list] = None) -> str:
        """Replace every match with a protected placeholder."""
        def spoken_for(entry: Entry, m: re.Match) -> str:
            if entry.spoken == "=":
                return m.group(0)
            return m.expand(entry.spoken) if entry.is_regex else entry.spoken

        for rx, entry in self._regex:
            def _r(m, entry=entry):
                if hits is not None:
                    hits.append((m.group(0), entry))
                # Keep leading space outside the placeholder, and make sure a
                # unit glued to a number ("7st") is separated from it.
                lead = re.match(r"\s*", m.group(0)).group(0)
                if not lead and m.start() > 0 and m.string[m.start() - 1].isalnum():
                    lead = " "
                return lead + prot.put(spoken_for(entry, m).strip())
            text = rx.sub(_r, text)
        for rx, table, fold in ((self._re_cs, self._plain_cs, False), (self._re_ci, self._plain_ci, True)):
            if rx is None:
                continue
            def _r(m, table=table, fold=fold):
                w = m.group(0)
                entry = table.get(w.lower() if fold else w)
                if entry is None:
                    return w
                if hits is not None:
                    hits.append((w, entry))
                return prot.put(spoken_for(entry, m))
            text = rx.sub(_r, text)
        return text


def builtin_files() -> list[Path]:
    return sorted(p for p in BUILTIN_DIR.glob("_*.csv"))


def load_lexicon(shared_dir: Optional[Path] = None, book_files: Iterable[Path] = ()) -> Lexicon:
    entries: list[Entry] = []
    for p in builtin_files():
        entries.extend(read_lexicon_csv(p))
    if shared_dir is not None and Path(shared_dir).is_dir():
        for p in sorted(Path(shared_dir).glob("*.csv")):
            entries.extend(read_lexicon_csv(p))
    for p in book_files:
        if Path(p).exists():
            entries.extend(read_lexicon_csv(Path(p)))
    return Lexicon(entries)


def load_for_book(paths) -> Lexicon:
    """The lexicon for a book, in the documented load order."""
    if not paths.shared_lexicons.is_dir():
        # Shared lexicons not installed yet (for example in tests): use the
        # ones shipped with Lektor so behaviour matches a fresh Drive folder.
        shared = BUILTIN_DIR
        entries = [e for p in sorted(shared.glob("[!_]*.csv")) for e in read_lexicon_csv(p)]
        base = load_lexicon(None, ())
        lex = Lexicon(base.entries + entries + [e for f in (paths.lexicon_suggestions, paths.book_lexicon)
                                                if Path(f).exists() for e in read_lexicon_csv(Path(f))])
        return lex
    return load_lexicon(paths.shared_lexicons, [paths.lexicon_suggestions, paths.book_lexicon])
