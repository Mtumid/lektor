"""Context sensitive rules for music and music production text.

These are cases a simple lexicon cannot decide, because the same characters
mean different things in different places:

- the Roman numeral ``I`` in a chord progression versus the pronoun "I"
- the note ``A`` versus the article "a"
- ``3/4`` as a time signature ("three four") versus a fraction ("three quarters")
- ``1/8`` as a note value ("eighth") versus a fraction ("an eighth")
- ``4:1`` as a ratio ("four to one") versus a clock time
- chord symbols such as ``F#m7``, ``Bbmaj7`` and ``C/E``

Every rule returns finished spoken text, which is protected from later
passes. The rules are tested in ``tests/test_speech.py``; add a test
whenever you change one.
"""

from __future__ import annotations

import re
from typing import Callable

from num2words import num2words

from .protect import Protector

# How a lone note letter is spoken. "A" on its own is the risky one: many
# voices read it as the article "uh". Check these in the voice audition.
NOTE_LETTER = {"A": "ay", "B": "B", "C": "C", "D": "D", "E": "E", "F": "F", "G": "G"}
ACCIDENTAL = {"#": "sharp", "♯": "sharp", "b": "flat", "♭": "flat", "♮": "natural"}

ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}

QUALITY = [
    # longest first
    ("maj7", "major seven"), ("maj9", "major nine"), ("maj", "major"), ("M7", "major seven"),
    ("min7", "minor seven"), ("m7b5", "minor seven flat five"), ("m7", "minor seven"), ("m9", "minor nine"),
    ("min", "minor"), ("m", "minor"), ("dim7", "diminished seven"), ("dim", "diminished"),
    ("aug", "augmented"), ("sus2", "suspended two"), ("sus4", "suspended four"), ("sus", "suspended"),
    ("add9", "add nine"), ("add11", "add eleven"), ("°7", "diminished seven"), ("°", "diminished"),
    ("ø7", "half diminished seven"), ("ø", "half diminished"), ("+", "augmented"),
    ("13", "thirteen"), ("11", "eleven"), ("9", "nine"), ("7", "seven"), ("6", "six"), ("5", "five"),
]
_QUALITY_RE = "|".join(re.escape(q) for q, _ in QUALITY)
_QUALITY_SPOKEN = dict(QUALITY)

_NOT_WORD = r"(?![A-Za-z0-9#♯♭])"
_NOT_WORD_BEFORE = r"(?<![A-Za-z0-9#♯♭/])"

NOTE_VALUES = {1: "whole", 2: "half", 4: "quarter", 8: "eighth", 16: "sixteenth", 32: "thirty-second",
               64: "sixty-fourth"}
FRACTION_NAMES = {2: ("half", "halves"), 3: ("third", "thirds"), 4: ("quarter", "quarters"), 5: ("fifth", "fifths"),
                  6: ("sixth", "sixths"), 8: ("eighth", "eighths"), 10: ("tenth", "tenths"), 16: ("sixteenth", "sixteenths"),
                  12: ("twelfth", "twelfths"), 32: ("thirty-second", "thirty-seconds")}


def words(n) -> str:
    """A number in words, British style, without commas."""
    s = str(n)
    try:
        if "." in s:
            whole, frac = s.split(".", 1)
            return f"{words(int(whole))} point " + " ".join(num2words(int(d)) for d in frac)
        return num2words(int(n), lang="en").replace(",", "")
    except (ValueError, OverflowError):
        return s


def spoken_note(letter: str, acc: str = "") -> str:
    base = NOTE_LETTER.get(letter, letter)
    if acc:
        return f"{letter} {ACCIDENTAL[acc]}"
    return base


def spoken_quality(q: str) -> str:
    out, rest = [], q
    while rest:
        for k, v in QUALITY:
            if rest.startswith(k):
                out.append(v)
                rest = rest[len(k):]
                break
        else:
            out.append(rest)
            break
    return " ".join(out)


# ---------------------------------------------------------------------------
# Rules. Each is (name, compiled pattern, function returning spoken text or None)
# ---------------------------------------------------------------------------
Rule = tuple[str, re.Pattern, Callable[[re.Match], "str | None"]]
RULES: list[Rule] = []


def rule(name: str, pattern: str, flags: int = 0):
    def deco(fn):
        RULES.append((name, re.compile(pattern, flags), fn))
        return fn
    return deco


# Roman numeral chord progressions: ii–V–I, I-IV-V-I, vi–IV–I–V, ii7–V7–Imaj7
_RN = r"(?:vii|VII|iii|III|vi|VI|iv|IV|ii|II|v|V|i|I)(?:°|ø|\+|maj7|7|9|6)?"
_SEP = r"\s?[–—-]\s?"


def _roman_token(tok: str) -> str:
    m = re.match(r"(vii|iii|vi|iv|ii|v|i)(.*)", tok, re.I)
    num = words(ROMAN_VALUES[m.group(1).lower()])
    q = m.group(2)
    return num + (" " + spoken_quality(q) if q else "")


@rule("roman progression", rf"(?<![A-Za-z0-9]){_RN}(?:{_SEP}{_RN})+(?![A-Za-z0-9])")
def _progression(m: re.Match):
    toks = re.split(_SEP, m.group(0))
    return " ".join(_roman_token(t.strip()) for t in toks)


@rule("roman chord", rf"(?<![A-Za-z0-9])({_RN})(?=\s+(?:chords?|triads?)\b)")
def _roman_chord(m: re.Match):
    return _roman_token(m.group(1))


# Time signatures and note values (before generic fractions)
@rule("note value", r"(?<![\d/.])1/(1|2|4|8|16|32|64)(th)?([TtD])?(?![\d/])(\s+(?:notes?|triplets?|rests?|beats?|of|note))?")
def _note_value(m: re.Match):
    den = int(m.group(1))
    name = NOTE_VALUES[den]
    mod = m.group(3) or ""
    tail = m.group(4) or ""
    tail_word = tail.strip().lower()
    if tail_word == "of":
        article = "an" if name[0] in "aeio" else "a"
        if den == 2:
            return f"a half of"
        return f"{article} {name} of"
    prefix = "dotted " if mod == "D" else ""
    suffix = " triplet" if mod in ("T", "t") else ""
    return f"{prefix}{name}{suffix}{(' ' + tail.strip()) if tail else ''}"


@rule("time signature or fraction", r"(?<![\d/.:])(\d{1,2})/(2|4|8|16|32)(?![\d/])(\s+(?:time|meter|metre|signature|of|bars?)\b)?")
def _time_signature(m: re.Match):
    num, den = int(m.group(1)), int(m.group(2))
    tail = (m.group(3) or "").strip().lower()
    before = m.string[max(0, m.start() - 12): m.start()].lower()
    fraction_context = tail == "of" or re.search(r"(about|nearly|almost|roughly|over|under|only)\s$", before)
    if fraction_context and num < den:
        sing, plur = FRACTION_NAMES.get(den, (None, None))
        if sing:
            return f"{words(num)} {sing if num == 1 else plur}" + (" of" if tail == "of" else "")
    spoken = f"{words(num)} {words(den)}"
    return spoken + (f" {tail}" if tail else "")


# Ratios such as 4:1, 1.5:1, ∞:1
@rule("ratio", r"(?<![\d:])(\d+(?:\.\d+)?|∞)\s?:\s?([1-9](?:\.\d+)?)(?![\d:])")
def _ratio(m: re.Match):
    a = "infinity" if m.group(1) == "∞" else words(m.group(1))
    return f"{a} to {words(m.group(2))}"


# Chord symbols with an accidental or a quality: F#m7, Bbmaj7, C#, Eb, Am7, G7, C/E
@rule("chord symbol",
      _NOT_WORD_BEFORE + r"([A-G])([#♯♭b]?)(" + _QUALITY_RE + r")?(?:/([A-G])([#♯♭b]?))?" + _NOT_WORD)
def _chord(m: re.Match):
    letter, acc, qual, bass, bass_acc = m.groups()
    whole = m.group(0)
    if not acc and not qual and not bass:
        return None  # a bare letter: handled by the note letter rule
    if whole == "Am" and re.match(r"\s+(I|we|you|he|she|they)\b", m.string[m.end():]):
        return None  # "Am I ..."
    spoken = spoken_note(letter, acc)
    if qual:
        spoken += " " + spoken_quality(qual)
    if bass:
        spoken += " over " + spoken_note(bass, bass_acc)
    return spoken


# The note A (versus the article "a"). In running text the article is
# written "a"; a capital A in the middle of a sentence is almost always the
# letter or the note ("in A", "the A string", "section A"). At the start of a
# sentence it is the article, unless a musical word follows ("A minor ...").
_A_AFTER = r"(?=\s+(?:major|minor|flat|sharp|natural|string|note|chord|key|scale|440|pedal)\b|[,;.)]|\s*$)"


@rule("note A mid-sentence", r"(?<=[^\s]\s)A(?![A-Za-z0-9#♯♭'’-])")
def _note_a_mid(m: re.Match):
    nxt = m.string[m.end():]
    prev_tok = m.string[:m.start()].rstrip().split(" ")[-1] if m.string[:m.start()].strip() else ""
    # After a number, a colon, a closing bracket or an opening quote, "A" is
    # usually the article starting a caption or quotation: "Figure 1.1 A two
    # bar pattern", "Tip: A good idea", '"A quiet start"'.
    if re.search(r"(\d|[:)\]]|[\"“‘'])$", prev_tok) and re.match(r"\s+[a-z]", nxt):
        return None
    if re.match(r"\s+[A-Z][a-z]", nxt) and not re.match(r"\s+(?:Major|Minor)\b", nxt):
        return None  # a title: "Part of A Tribe Called Quest"
    return NOTE_LETTER["A"]


@rule("note A at start", r"^A" + _A_AFTER)
def _note_a_start(m: re.Match):
    return NOTE_LETTER["A"]


@rule("note with octave", _NOT_WORD_BEFORE + r"([A-G])([#♯♭b]?)(-?[0-8])(?![\d/:]|\.\d)")
def _note_octave(m: re.Match):
    letter, acc, octave = m.groups()
    o = int(octave)
    return f"{spoken_note(letter, acc) if acc else NOTE_LETTER.get(letter, letter)} {'minus ' + words(-o) if o < 0 else words(o)}"


def apply_rules(text: str, prot: Protector, hits: list | None = None) -> str:
    for name, rx, fn in RULES:
        def _r(m, fn=fn, name=name):
            out = fn(m)
            if out is None:
                return m.group(0)
            if hits is not None:
                hits.append((m.group(0), name))
            return prot.put(out)
        text = rx.sub(_r, text)
    return text
