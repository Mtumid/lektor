"""Turn display text into spoken text.

For every sentence Lektor keeps two versions: the **display text**, exactly
as the author wrote it (used for the DAISY text and for quality checks), and
the **spoken text** made here, which is what the voice reads.

Order of passes (each pass protects what it produces, so later passes
cannot change it):

1. split units glued to numbers ("120bpm" -> "120 bpm")
2. URLs and email addresses
3. the lexicon (built in, shared and book lexicons; see ``lexicon.py``)
4. music rules (``rules.py``): progressions, chords, time signatures ...
5. general rules: currency, percentages, ranges, times, ordinals, years,
   numbers, abbreviations and symbols
6. capitals: words in capitals that are ordinary English words are lowered,
   so voices do not spell them out
7. tidy up: restore protected text, fix "one milliseconds", spacing

Changing any of this changes the sound of the book, so bump
``NORMALISER_VERSION`` (it is part of every cache key) when you do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from num2words import num2words

from .lexicon import Lexicon
from .protect import PLACEHOLDER_RE, Protector
from .rules import apply_rules, words

NORMALISER_VERSION = "1"

GLUED_UNITS = ["bpm", "BPM", "Hz", "kHz", "khz", "ms", "dB", "dBFS", "LUFS", "kbps", "Kbps", "bit", "MB", "GB", "TB"]
_GLUED_RE = re.compile(r"(?<![A-Za-z])(\d+(?:\.\d+)?)(" + "|".join(sorted(GLUED_UNITS, key=len, reverse=True)) + r")\b")

URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+[^\s<>()\"'.,;:!?]", re.I)
BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w.])((?:[a-z0-9-]+\.)+(?:com|org|net|io|fm|info|edu|gov|co\.uk|org\.uk|ac\.uk|uk|de|fr|eu))(/[^\s]*[^\s.,;:!?)])?(?![\w.])",
    re.I,
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})\b")

ABBREVIATIONS = [
    (r"\be\.\s?g\.(?=[\s,])", "for example"),
    (r"\bi\.\s?e\.(?=[\s,])", "that is"),
    (r"\betc\.(?=\s*$)", "et cetera."),
    (r"\betc\.", "et cetera"),
    (r"\bet al\.", "et al."),
    (r"\ba\.k\.a\.", "also known as"),
    (r"\bvs\.?(?=\s)", "versus"),
    (r"\bcf\.", "compare"),
    (r"\bapprox\.", "approximately"),
    (r"\bc(?:a)?\.\s?(?=\d{3,4}\b)", "circa "),
    (r"\b[Nn]o\.\s?(?=\d)", "number "),
    (r"\b[Nn]os\.\s?(?=\d)", "numbers "),
    (r"\bpp\.\s?(?=\d)", "pages "),
    (r"\bp\.\s?(?=\d)", "page "),
    (r"\b[Ff]igs?\.\s?(?=\d)", "figure "),
    (r"\b[Cc]h\.\s?(?=\d)", "chapter "),
    (r"\b[Vv]ol\.\s?(?=\d)", "volume "),
    (r"\bDr\.(?=\s[A-Z])", "Doctor"),
    (r"\bw/o\b", "without"),
    (r"(?<![A-Za-z])w/(?=\s)", "with"),
]
ABBREVIATIONS = [(re.compile(p), s) for p, s in ABBREVIATIONS]

# Abbreviations containing full stops that must not end a sentence. Used
# by the sentence splitter (see lektor.chunk).
NON_TERMINAL_ABBREVIATIONS = ["e.g.", "i.e.", "etc.", "et al.", "a.k.a.", "vs.", "cf.", "approx.", "ca.",
                              "No.", "no.", "pp.", "p.", "Fig.", "fig.", "Figs.", "Ch.", "ch.", "Vol.", "vol.",
                              "Dr.", "Mr.", "Mrs.", "Ms.", "St.", "Prof.", "Inc.", "Ltd.", "Co."]

CURRENCY = {"£": ("pound", "pounds", "penny", "pence"), "$": ("dollar", "dollars", "cent", "cents"),
            "€": ("euro", "euros", "cent", "cents")}
SCALE = {"k": "thousand", "m": "million", "bn": "billion", "million": "million", "billion": "billion"}

_CUR_RE = re.compile(r"([£$€])\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?:\s?(k|m|bn|million|billion)\b)?")
_PERCENT_RE = re.compile(r"([+\-−±]?\d+(?:\.\d+)?)\s?%")
_RANGE_RE = re.compile(r"(?<![\w.,/-])(\d+(?:\.\d+)?)\s?[–—-]\s?(\d+(?:\.\d+)?)(?![\w/-]|[.,]\d)")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?![\d:])(\s?(?:am|pm|a\.m\.|p\.m\.))?", re.I)
_ORDINAL_RE = re.compile(r"(?<![\w.])(\d+)(st|nd|rd|th)\b", re.I)
_DECADE_RE = re.compile(r"(?<![\w.])(?:['’](\d0)|(1\d|20)(\d0))s\b")
_YEAR_RE = re.compile(r"(?<![\w.,£$€×])(1[1-9]\d\d|20\d\d)(?![\w%]|[.,]\d|\s?×)(?!\s?(?:Hz|kHz|ms|dB|BPM|bpm|samples|times|people|words|pages|tracks|plugins|euros|pounds|dollars)\b)")
_NUMBER_RE = re.compile(r"(?<![\w.])([+\-−±])?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w]|\.\d)")
_DIGITS_WITH_LEADING_ZERO = re.compile(r"(?<![\w.])0\d+(?![\w]|\.\d)")
_CAPS_RUN_RE = re.compile(r"(?<![\w'’])[A-Z][A-Z'’]+(?: [A-Z][A-Z'’]+)+(?![\w'’])")
_CAPS_WORD_RE = re.compile(r"(?<![\w'’])([A-Z][A-Z'’]{3,})(?![\w'’])")
_SINGULAR = {"milliseconds": "millisecond", "decibels": "decibel", "seconds": "second", "minutes": "minute",
             "semitones": "semitone", "octaves": "octave", "bars": "bar", "beats": "beat", "cents": "cent",
             "samples": "sample", "hours": "hour", "pages": "page", "pounds": "pound", "dollars": "dollar",
             "euros": "euro", "kilobits per second": "kilobit per second"}
_SINGULAR_RE = re.compile(r"(?<![\w-])(one|minus one|plus one)\s(" + "|".join(sorted(_SINGULAR, key=len, reverse=True)) + r")\b", re.I)


@dataclass
class SpeechOptions:
    urls: str = "domain"  # "domain" or "link"
    is_heading: bool = False


@dataclass
class Normalised:
    display: str
    spoken: str
    lexicon_hits: list = field(default_factory=list)
    rule_hits: list = field(default_factory=list)


def _domain_words(host: str) -> str:
    host = re.sub(r"^www\.", "", host.lower())
    return " dot ".join(host.split("."))


def _url_spoken(url: str, opts: SpeechOptions) -> str:
    if opts.urls == "link":
        return "web link"
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split(":")[0]
    return _domain_words(host)


def _number_words(sign: Optional[str], integer: str, frac: Optional[str]) -> str:
    n = integer.replace(",", "")
    out = words(n) if not frac else words(f"{n}.{frac}")
    if sign in ("-", "−"):
        out = "minus " + out
    elif sign == "+":
        out = "plus " + out
    elif sign == "±":
        out = "plus or minus " + out
    return out


def _year_words(y: int) -> str:
    try:
        return num2words(y, to="year", lang="en").replace(",", "")
    except Exception:
        return words(y)


def _plural_last(phrase: str) -> str:
    parts = phrase.split(" ")
    last = parts[-1]
    if "-" in last:
        a, b = last.rsplit("-", 1)
        b = b[:-1] + "ies" if b.endswith("y") else b + "s"
        parts[-1] = f"{a}-{b}"
    else:
        parts[-1] = last[:-1] + "ies" if last.endswith("y") else last + "s"
    return " ".join(parts)


ROMAN_HEADING_RE = re.compile(r"\b(Part|Book|Volume|Chapter|Section|Appendix|Act|Scene|Unit|Lesson)\s+([IVXLC]{1,7})\b(?!['’])")
ROMAN_ALONE_RE = re.compile(r"^([IVXLC]{1,7})(?=[.:]?(\s|$))")
_ROMAN_VALID_RE = re.compile(r"^(?=[MDCLXVI])M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def roman_to_int(s: str) -> Optional[int]:
    if not _ROMAN_VALID_RE.match(s):
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return total


class Normaliser:
    def __init__(self, lexicon: Optional[Lexicon] = None, options: Optional[SpeechOptions] = None,
                 wordlist=None) -> None:
        self.lexicon = lexicon or Lexicon()
        self.options = options or SpeechOptions()
        self._wordlist = wordlist

    @property
    def wordlist(self):
        if self._wordlist is None:
            from .wordlist import english_words

            self._wordlist = english_words()
        return self._wordlist

    # -----------------------------------------------------------------------
    def normalise(self, text: str, heading: bool = False) -> Normalised:
        display = text
        prot = Protector()
        lex_hits: list = []
        rule_hits: list = []
        t = text

        if heading:
            t = ROMAN_HEADING_RE.sub(lambda m: f"{m.group(1)} " + prot.put(words(roman_to_int(m.group(2)) or m.group(2))), t)
            m = ROMAN_ALONE_RE.match(t)
            if m and roman_to_int(m.group(1)):
                t = prot.put(words(roman_to_int(m.group(1)))) + t[m.end():]

        t = _GLUED_RE.sub(r"\1 \2", t)
        t = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)x\b", r"\1 times", t)
        t = prot.sub(EMAIL_RE, lambda m: "an address at " + _domain_words(m.group(1))
                     if self.options.urls == "domain" else "an email address", t)
        t = prot.sub(URL_RE, lambda m: _url_spoken(m.group(0), self.options), t)
        t = prot.sub(BARE_DOMAIN_RE, lambda m: _url_spoken(m.group(1), self.options), t)
        t = self.lexicon.apply(t, prot, lex_hits)
        t = apply_rules(t, prot, rule_hits)
        t = self._general(t, prot)
        t = self._capitals(t)
        t = prot.restore(t)
        t = self._tidy(t)
        if t and t[0].islower() and (display[:1].isupper() or display[:1].isdigit()):
            t = t[0].upper() + t[1:]
        return Normalised(display, t, lex_hits, rule_hits)

    def __call__(self, text: str, heading: bool = False) -> str:
        return self.normalise(text, heading).spoken

    # -----------------------------------------------------------------------
    def _general(self, t: str, prot: Protector) -> str:
        def currency(m):
            sym, whole, frac, scale = m.groups()
            one, many, small1, small = CURRENCY[sym]
            n = int(whole.replace(",", ""))
            if scale:
                return f"{_number_words(None, whole, frac)} {SCALE[scale.lower()]} {many}"
            if n == 0 and frac:
                return f"{words(int(frac.ljust(2, '0')))} {small}"
            out = f"{words(n)} {one if n == 1 else many}"
            if frac and int(frac):
                out += f" {words(int(frac.ljust(2, '0')))}"
            return out

        # Abbreviations first: several look ahead for a digit ("p. 47").
        for rx, spoken in ABBREVIATIONS:
            t = rx.sub(lambda m, s=spoken: prot.put(s), t)
        t = prot.sub(_CUR_RE, currency, t)
        t = prot.sub(_PERCENT_RE, lambda m: _number_words(*_split_sign(m.group(1))) + " per cent", t)

        def time(m):
            h, mm, ss, ampm = m.groups()
            h_i, m_i = int(h), int(mm)
            if ss:
                return f"{words(h_i)} {words(m_i)} {words(int(ss))}"
            if m_i == 0:
                out = f"{words(h_i)} o'clock" if not ampm else words(h_i)
            elif m_i < 10:
                out = f"{words(h_i)} oh {words(m_i)}"
            else:
                out = f"{words(h_i)} {words(m_i)}"
            if ampm:
                out += " a m" if ampm.strip().lower().startswith("a") else " p m"
            return out

        t = prot.sub(_TIME_RE, time, t)
        t = prot.sub(_RANGE_RE, lambda m: f"{_range_side(m.group(1))} to {_range_side(m.group(2))}", t)
        t = prot.sub(_ORDINAL_RE, lambda m: num2words(int(m.group(1)), to="ordinal", lang="en").replace(",", ""), t)

        def decade(m):
            if m.group(1):
                return _plural_last(words(int(m.group(1))))
            return _plural_last(_year_words(int(m.group(2) + m.group(3))))

        t = prot.sub(_DECADE_RE, decade, t)
        t = prot.sub(_YEAR_RE, lambda m: _year_words(int(m.group(1))), t)
        t = prot.sub(_DIGITS_WITH_LEADING_ZERO, lambda m: " ".join("oh" if d == "0" else words(int(d)) for d in m.group(0)), t)
        t = prot.sub(_NUMBER_RE, lambda m: _number_words(m.group(1), m.group(2), m.group(3)), t)
        t = self._symbols(t)
        return t

    def _symbols(self, t: str) -> str:
        t = t.replace("&", " and ")
        t = re.sub(r"(?<=\S)\s*×\s*(?=\S)", " by ", t)
        t = re.sub(r"\s*(→|->|⇒)\s*", " to ", t)
        t = re.sub(r"\s*(←|<-)\s*", " from ", t)
        t = re.sub(r"(?<=\s)\+(?=\s)", "plus", t)
        t = re.sub(r"(?<=\s)=(?=\s)", "equals", t)
        t = re.sub(r"~\s?(?=\S)", "about ", t)
        t = re.sub(r"#(?=\S)", "number ", t)
        t = re.sub(r"\s?°", " degrees", t)
        t = re.sub(r"\s?±\s?", " plus or minus ", t)
        t = re.sub(r"(?i)\b(and)/or\b", r"\1 or", t)
        t = re.sub(r"(?<=[A-Za-z])/(?=[A-Za-z])", " or ", t)
        t = re.sub(r"(?<=\S)–(?=\S)", " to ", t)  # unspaced en dash: a range
        t = re.sub(r"\s[—–]\s|—", ", ", t)
        t = re.sub(r"[•▪◦●]", "", t)
        t = t.replace("…", "...")
        return t

    def _capitals(self, t: str) -> str:
        """Lower ordinary words written in capitals so voices do not spell them.

        A run of two or more capitalised words ("PROBLEMS OF BEGINNING") is
        lowered word by word; a single capitalised word only if it has four or
        more letters and is an English word ("NOTE"). Acronyms the lexicon
        knows were protected earlier and are not affected.
        """
        def fix_word(w: str, first: bool, any_length: bool) -> str:
            base = re.sub(r"['’]S$", "", w)
            if (any_length or len(base) >= 4) and base.lower() in self.wordlist:
                return w.capitalize() if first else w.lower()
            return w

        def run(m):
            ws = m.group(0).split(" ")
            first = m.start() == 0 or t[:m.start()].rstrip()[-1:] in ".!?:"
            return " ".join(fix_word(w, first and i == 0, True) for i, w in enumerate(ws))

        t = _CAPS_RUN_RE.sub(run, t)
        return _CAPS_WORD_RE.sub(lambda m: fix_word(m.group(1), m.start() == 0 or t[:m.start()].rstrip()[-1:] in ".!?:",
                                                     False), t)

    @staticmethod
    def _tidy(t: str) -> str:
        t = _SINGULAR_RE.sub(lambda m: f"{m.group(1)} {_SINGULAR[m.group(2)]}", t)
        t = re.sub(r"\s+([,.;:!?])", r"\1", t)
        t = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", t)
        t = re.sub(r",\s*,", ",", t)
        t = re.sub(r"\s{2,}", " ", t)
        return t.strip()


def _split_sign(s: str) -> tuple[Optional[str], str, Optional[str]]:
    sign = s[0] if s[0] in "+-−±" else None
    body = s[1:] if sign else s
    whole, _, frac = body.partition(".")
    return sign, whole, frac or None


def _range_side(s: str) -> str:
    whole, _, frac = s.partition(".")
    return _number_words(None, whole, frac or None)


__all__ = ["Normaliser", "SpeechOptions", "Normalised", "NORMALISER_VERSION", "NON_TERMINAL_ABBREVIATIONS",
           "roman_to_int", "PLACEHOLDER_RE"]
