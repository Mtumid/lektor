"""An English wordlist, used to spot unusual words and words in capitals.

Uses the word frequency list that ships with ``pyspellchecker`` (MIT). If
it is not installed, a small built in list is used, which makes the unknown
term detector noisier but keeps everything working.
"""

from __future__ import annotations

from functools import lru_cache

_FALLBACK = set("""
a about after all also an and any are as at back be because been before being between both but by can could
day did do does down each even every first for from get give go good great had has have he her here him his
how i if in into is it its just know last like little long look make many may me more most much must my
never new no not now of off on one only or other our out over own part people place point right said same
see she should show so some sound still such take than that the their them then there these they thing
think this those three through time to track two under up us use very want was way we well were what when
where which while who why will with work would year you your music note notes beat beats bar bars song
songs sounds drum drums bass sample samples loop loops mix mixing record recording level levels track
tracks problems beginning finishing progressing strategy strategies part chapter introduction contents
""".split())


@lru_cache(maxsize=1)
def english_words() -> frozenset:
    try:
        from spellchecker import SpellChecker

        wf = SpellChecker(language="en", distance=1).word_frequency
        words = {w for w in wf.dictionary.keys() if w.isalpha() or "'" in w}
        return frozenset(words | _FALLBACK)
    except Exception:
        return frozenset(_FALLBACK)


def is_english(word: str, words=None) -> bool:
    """True if ``word`` (any case) is English, accepting British spellings
    (centre, colour, organise, analyse, catalogue, travelled)."""
    words = words if words is not None else english_words()
    w = word.lower()
    if w in words:
        return True
    variants = []
    if w.endswith("re"):
        variants.append(w[:-2] + "er")
    if w.endswith("res"):
        variants.append(w[:-3] + "ers")
    variants += [w.replace("our", "or"), w.replace("ise", "ize"), w.replace("isa", "iza"), w.replace("yse", "yze"),
                 w.replace("ogue", "og"), w.replace("ae", "e"), w.replace("oe", "e"), w.replace("ence", "ense"), w.replace("mme", "m")]
    for suf in ("lled", "lling", "ller", "llers"):
        if w.endswith(suf):
            variants.append(w[: -len(suf)] + suf[1:])
    return any(v != w and v in words for v in variants)
