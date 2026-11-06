"""Quality checks for one synthesised unit.

All checks are pure functions on audio and text, so they are tested without
any model. :func:`check_unit` runs them all and returns a :class:`QAResult`.

Checks:

- **words**: transcribe the audio, normalise transcript and spoken text the
  same way, and compute word and character error rates (jiwer)
- **duration**: characters per second outside the band learned for this
  voice from its first few hundred units (median +/- a robust spread)
- **silence**: an internal silence longer than ``max_silence_s`` (1.2 s)
- **clipping**: samples at full scale
- **empty**: no audio, silent audio or invalid values
- **repetition**: the transcript repeats a phrase the text does not
  (a typical failure of autoregressive voices), or is much longer than it
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

_PUNCT_RE = re.compile(r"[^\w\s']")
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                 "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                 "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
                 "thousand", "million", "billion", "oh", "point"}


def wer_normalise(text: str) -> str:
    """Normalise text for comparison: lower case, no punctuation, "and"
    inside numbers dropped, hyphens and apostrophes handled."""
    t = text.lower().replace("’", "'")
    t = re.sub(r"(\w)-(\w)", r"\1 \2", t)
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"'(?!\w)|(?<!\w)'", " ", t)
    words = t.split()
    out = []
    for i, w in enumerate(words):
        if w == "and" and 0 < i < len(words) - 1 and words[i - 1] in _NUMBER_WORDS and words[i + 1] in _NUMBER_WORDS:
            continue
        out.append(w)
    return " ".join(out)


def error_rates(reference: str, hypothesis: str) -> tuple[float, float, int]:
    """(word error rate, character error rate, number of word errors)."""
    import jiwer

    ref, hyp = wer_normalise(reference), wer_normalise(hypothesis)
    if not ref:
        return (0.0 if not hyp else 1.0), (0.0 if not hyp else 1.0), len(hyp.split())
    out = jiwer.process_words(ref, hyp)
    errors = out.substitutions + out.deletions + out.insertions
    cer = jiwer.cer(ref, hyp) if hyp else 1.0
    return float(out.wer), float(cer), int(errors)


def word_diff_html(reference: str, hypothesis: str) -> str:
    """An HTML word diff: missing words struck through, extra words underlined."""
    import difflib
    import html

    ref, hyp = wer_normalise(reference).split(), wer_normalise(hypothesis).split()
    out = []
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(ref[a0:a1])))
        if op in ("delete", "replace"):
            out.append(f"<del title='in the text, not heard'>{html.escape(' '.join(ref[a0:a1]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins title='heard, not in the text'>{html.escape(' '.join(hyp[b0:b1]))}</ins>")
    return " ".join(x for x in out if x)


def internal_silences(audio: np.ndarray, sr: int, threshold_db: float = -45.0) -> list[tuple[float, float]]:
    """Silent stretches inside the audio (leading and trailing silence ignored)."""
    frame = max(1, int(0.02 * sr))
    n = audio.size // frame
    if n < 3:
        return []
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    loud = 20 * np.log10(rms) > threshold_db
    idx = np.where(loud)[0]
    if idx.size == 0:
        return []
    first, last = idx[0], idx[-1]
    out, start = [], None
    for i in range(first, last + 1):
        if not loud[i] and start is None:
            start = i
        elif loud[i] and start is not None:
            out.append((start * frame / sr, i * frame / sr))
            start = None
    return out


def clipping_fraction(audio: np.ndarray, level: float = 0.999) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.mean(np.abs(audio) >= level))


def repetition(reference: str, hypothesis: str, n: int = 3) -> Optional[str]:
    """Describe a repetition loop in the transcript, or None."""
    ref, hyp = wer_normalise(reference).split(), wer_normalise(hypothesis).split()
    if ref and len(hyp) > max(8, 1.6 * len(ref)):
        return f"the voice said {len(hyp)} words for {len(ref)} words of text"
    grams_h = Counter(tuple(hyp[i: i + n]) for i in range(len(hyp) - n + 1))
    grams_r = Counter(tuple(ref[i: i + n]) for i in range(len(ref) - n + 1))
    for g, c in grams_h.most_common(3):
        if c >= 3 and c > grams_r.get(g, 0) + 1:
            return f"'{' '.join(g)}' was repeated {c} times"
    return None


@dataclass
class DurationBand:
    """Characters per second for one voice, learned from its first units."""

    low: float
    high: float
    median: float
    n: int

    @classmethod
    def learn(cls, rates: list[float], spread: float = 3.5) -> Optional["DurationBand"]:
        rates = [r for r in rates if r > 0 and np.isfinite(r)]
        if len(rates) < 20:
            return None
        med = statistics.median(rates)
        mad = statistics.median(abs(r - med) for r in rates) or 0.05 * med
        return cls(low=max(1.0, med - spread * 1.4826 * mad), high=med + spread * 1.4826 * mad, median=med, n=len(rates))


@dataclass
class QAResult:
    unit_id: str
    cache_key: str
    passed: bool
    flags: list[str] = field(default_factory=list)
    transcript: str = ""
    wer: float = 0.0
    cer: float = 0.0
    word_errors: int = 0
    duration: float = 0.0
    chars_per_second: float = 0.0
    seed: int = 0
    attempts: int = 1
    split: bool = False
    accepted: bool = False
    words: list = field(default_factory=list)  # [(word, start, end, probability)] from Whisper

    def to_json(self) -> dict:
        return asdict(self)

    @property
    def score(self) -> tuple:
        """Lower is better: used to keep the best attempt."""
        return (len([f for f in self.flags if not f.startswith("duration")]), self.word_errors, self.wer)


def check_unit(unit_id: str, cache_key: str, spoken: str, audio: np.ndarray, sr: int, transcript: Optional[str],
               *, max_wer: float = 0.15, max_silence_s: float = 1.2, band: Optional[DurationBand] = None,
               normaliser=None, seed: int = 0, words: Optional[list] = None) -> QAResult:
    from ..assemble import trim_silence

    flags: list[str] = []
    a = np.asarray(audio, dtype=np.float32)
    if a.size == 0 or not np.all(np.isfinite(a)) or float(np.max(np.abs(a)) if a.size else 0) < 1e-4:
        return QAResult(unit_id, cache_key, False, ["empty: no usable audio"], seed=seed)
    trimmed = trim_silence(a, sr)
    dur = trimmed.size / sr
    cps = len(spoken) / dur if dur > 0 else 0.0
    res = QAResult(unit_id, cache_key, True, duration=round(dur, 3), chars_per_second=round(cps, 2), seed=seed,
                   words=words or [])
    if transcript is not None:
        hyp = transcript
        if normaliser is not None:
            try:
                hyp = normaliser(transcript)  # "120 BPM" -> "one hundred and twenty B P M"
            except Exception:
                hyp = transcript
        res.transcript = transcript
        res.wer, res.cer, res.word_errors = error_rates(spoken, hyp)
        n_words = len(wer_normalise(spoken).split())
        if res.word_errors >= 2 and res.wer > max_wer or (n_words <= 4 and res.cer > 0.5):
            flags.append(f"words: {res.word_errors} word error(s), WER {res.wer:.0%}")
        rep = repetition(spoken, hyp)
        if rep:
            flags.append(f"repetition: {rep}")
    for s, e in internal_silences(trimmed, sr):
        if e - s > max_silence_s:
            flags.append(f"silence: {e - s:.1f} s gap at {s:.1f} s")
            break
    clip = clipping_fraction(a)
    if clip > 0.001:
        flags.append(f"clipping: {clip:.1%} of samples at full scale")
    if band is not None and (cps < band.low or cps > band.high):
        word = "slow" if cps < band.low else "fast"
        flags.append(f"duration: {cps:.1f} characters a second is unusually {word} for this voice "
                     f"(normal {band.low:.1f} to {band.high:.1f})")
    res.flags = flags
    res.passed = not flags
    return res
