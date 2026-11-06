"""Unknown term detector: writes ``script/lexicon_suggestions.csv``.

Lists words a voice may get wrong, most frequent first, with an example
sentence and page, so the librarian can type how each should be said:

- words in capitals (``DAW``, ``LUFS``) that the lexicon does not cover
- words mixing letters with digits or symbols (``TR-8S``, ``Push2``)
- words not found in an English wordlist (``DeSantis``, ``glitchy``)

Rows already in the file keep whatever the librarian typed in ``spoken``
and ``notes`` when the file is regenerated. Rows with a spoken form are
used as a lexicon straight away (see ``lexicon.py``), so there is no need
to copy them anywhere.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from .lexicon import COLUMNS
from .normaliser import Normaliser
from .wordlist import is_english

EXTRA_COLUMNS = ["count", "kind", "example", "page"]
TOKEN_RE = re.compile(r"(?<![\w#♯♭'’])([A-Za-z][\w#♯♭'’+./-]*[\w#♯♭+]|[A-Za-z]|\d+[A-Za-z][\w-]*)(?![\w#♯♭])")
COMMON_ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}


def classify(token: str, wordlist) -> Optional[str]:
    core = re.sub(r"['’]s$", "", token)
    letters = re.sub(r"[^A-Za-z]", "", core)
    if not letters:
        return None
    if core in COMMON_ROMAN:
        return None
    has_digit = any(c.isdigit() for c in core)
    has_symbol = any(c in "#♯♭+/" for c in core) or ("-" in core and has_digit)
    if has_digit or has_symbol:
        return "letters with digits or symbols"
    if len(letters) >= 2 and core.isupper():
        return "capitals"
    if "-" in core:
        parts = [p for p in core.split("-") if p]
        if all(is_english(p, wordlist) for p in parts):
            return None
        return "not in dictionary"
    if is_english(core, wordlist):
        return None
    if len(core) == 1:
        return None
    return "not in dictionary"


def find_terms(sentences: Iterable[tuple[str, Optional[str]]], normaliser: Normaliser) -> list[dict]:
    """``sentences`` yields (display text, page). Returns rows, most frequent first."""
    wl = normaliser.wordlist
    counts: Counter[str] = Counter()
    kinds: dict[str, str] = {}
    examples: dict[str, tuple[str, Optional[str]]] = {}
    handled_cache: dict[str, bool] = {}

    def handled(tok: str) -> bool:
        if tok not in handled_cache:
            n = normaliser.normalise(tok)
            handled_cache[tok] = bool(n.lexicon_hits or n.rule_hits)
        return handled_cache[tok]

    for text, page in sentences:
        for m in TOKEN_RE.finditer(text):
            tok = m.group(1).rstrip(".")
            kind = classify(tok, wl)
            if not kind:
                continue
            # Capitalised words at the start of a sentence that are ordinary
            # words in lower case are not interesting.
            if kind == "not in dictionary" and tok[:1].isupper() and is_english(tok, wl):
                continue
            if handled(tok):
                continue
            counts[tok] += 1
            kinds.setdefault(tok, kind)
            examples.setdefault(tok, (text, page))
    rows = []
    for tok, n in counts.most_common():
        ex, page = examples[tok]
        rows.append({"written": tok, "spoken": "", "case_sensitive": "yes", "whole_word": "yes", "is_regex": "no",
                     "notes": "", "count": str(n), "kind": kinds[tok], "example": ex[:200], "page": page or ""})
    return rows


def write_suggestions(rows: list[dict], path: Path) -> int:
    """Write suggestions, keeping what the librarian already typed."""
    path = Path(path)
    existing: dict[str, dict] = {}
    if path.exists():
        for r in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            if r.get("written"):
                existing[r["written"]] = r
    out_rows = []
    seen = set()
    for r in rows:
        old = existing.get(r["written"])
        if old:
            for k in ("spoken", "notes", "case_sensitive", "whole_word", "is_regex"):
                if old.get(k):
                    r[k] = old[k]
        out_rows.append(r)
        seen.add(r["written"])
    # Keep rows the librarian filled in even if the word no longer appears.
    for w, old in existing.items():
        if w not in seen and old.get("spoken"):
            old = {k: old.get(k, "") for k in COLUMNS + EXTRA_COLUMNS}
            old["count"] = "0"
            out_rows.append(old)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS + EXTRA_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(out_rows)
    path.write_text(buf.getvalue(), encoding="utf-8")
    return len(out_rows)
