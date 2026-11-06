"""Sentence segmentation that never splits inside an abbreviation or a
lexicon entry."""

from __future__ import annotations

import re
import warnings
from functools import lru_cache
from typing import Iterable

_MASK = "\U000E0001"  # stands in for "." inside protected words; same length


@lru_cache(maxsize=1)
def _segmenter():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import pysbd

    return pysbd.Segmenter(language="en", clean=False, char_span=True)


def split_sentences(text: str, protected: Iterable[str] = ()) -> list[str]:
    """Split text into sentences. Joining the result with "" gives back the
    text (apart from whitespace at the joins)."""
    if not text.strip():
        return []
    masked = text
    for word in sorted(set(protected), key=len, reverse=True):
        if any(c in word for c in ".!?"):
            masked_word = word.replace(".", _MASK).replace("!", _MASK).replace("?", _MASK)
            masked = re.sub(rf"(?<![A-Za-z0-9]){re.escape(word)}", masked_word, masked)
    # Decimal numbers and versions (2.5, 1.2.3) are safe in pysbd already.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spans = _segmenter().segment(masked)
    out: list[str] = []
    for sp in spans:
        piece = text[sp.start: sp.end]
        if piece.strip():
            out.append(piece.strip())
    # pysbd occasionally loses text at the very end; make sure nothing is dropped.
    joined = "".join(re.sub(r"\s", "", s) for s in out)
    if joined != re.sub(r"\s", "", text):
        return _fallback_split(text, protected)
    return _merge_fragments(out)


def _merge_fragments(sents: list[str]) -> list[str]:
    """Join pieces that are not real sentences (a lone closing quote, a
    number) back on to the previous sentence."""
    out: list[str] = []
    for s in sents:
        if out and (len(s) < 3 or not re.search(r"[A-Za-z0-9]", s)):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return out


def _fallback_split(text: str, protected: Iterable[str]) -> list[str]:
    prot = sorted({p for p in protected if "." in p}, key=len, reverse=True)
    masked = text
    for p in prot:
        masked = masked.replace(p, p.replace(".", _MASK))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘(])", masked)
    return [p.replace(_MASK, ".").strip() for p in parts if p.strip()]


CLAUSE_RE = re.compile(r"(?<=[,;:—–)])\s+")


def split_long(text: str, limit: int, measure=len) -> list[str]:
    """Split a long sentence at clause boundaries, then at word boundaries,
    into pieces whose measured length is at most ``limit``."""
    if measure(text) <= limit:
        return [text]
    clauses = CLAUSE_RE.split(text)
    pieces: list[str] = []
    buf = ""
    for c in clauses:
        cand = (buf + " " + c).strip() if buf else c
        if measure(cand) <= limit:
            buf = cand
            continue
        if buf:
            pieces.append(buf)
        if measure(c) <= limit:
            buf = c
        else:
            words = c.split(" ")
            buf = ""
            for w in words:
                cand = (buf + " " + w).strip() if buf else w
                if measure(cand) <= limit or not buf:
                    buf = cand
                else:
                    pieces.append(buf)
                    buf = w
    if buf:
        pieces.append(buf)
    return pieces
