"""Quality checks, retries, report and acceptance, with a fake transcriber."""

import json

import numpy as np
import pytest

import fixtures
from lektor.assemble import run_assemble
from lektor.books import create_or_update_book
from lektor.cache import UnitCache
from lektor.chunk.plan import read_units
from lektor.config import save_book_config
from lektor.engines.mock import MockEngine
from lektor.ingest import run_ingest
from lektor.prepare import run_prepare
from lektor.qa.checks import (DurationBand, check_unit, clipping_fraction, error_rates, internal_silences, repetition,
                              wer_normalise, word_diff_html)
from lektor.qa.runner import accept_all, audio_signature, load_results, regenerate, run_qa, spot_check
from lektor.synth import run_synth

SR = 24000


def tone(seconds, sr=SR, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Pure checks
# ---------------------------------------------------------------------------
def test_wer_normalise():
    assert wer_normalise("One hundred and twenty B P M, please!") == "one hundred twenty b p m please"
    assert wer_normalise("Rock and roll") == "rock and roll"
    assert wer_normalise("side-chain it's") == "side chain it's"


def test_error_rates():
    assert error_rates("the quick brown fox", "the quick brown fox")[0] == 0.0
    wer, cer, n = error_rates("the quick brown fox", "the quick fox jumps")
    assert n == 2 and 0 < wer <= 0.5 and cer > 0


def test_word_diff():
    d = word_diff_html("the quick brown fox", "the quick red fox")
    assert "<del" in d and "brown" in d and "<ins" in d and "red" in d


def test_silence_and_clipping():
    a = np.concatenate([tone(1), np.zeros(int(1.5 * SR), dtype=np.float32), tone(1)])
    gaps = internal_silences(a, SR)
    assert len(gaps) == 1 and 1.4 < gaps[0][1] - gaps[0][0] < 1.6
    assert internal_silences(np.concatenate([np.zeros(SR, dtype=np.float32), tone(1)]), SR) == []
    clipped = np.clip(tone(1, amp=2.0), -1, 1)
    assert clipping_fraction(clipped) > 0.1 and clipping_fraction(tone(1)) == 0


def test_repetition():
    assert repetition("set the tempo now", "set the tempo set the tempo set the tempo set the tempo now")
    assert repetition("a quick test", "a quick test") is None


def test_duration_band():
    rng = np.random.default_rng(0)
    band = DurationBand.learn(list(15 + rng.normal(0, 0.5, 100)))
    assert band.low < 15 < band.high and band.high - band.low < 10
    assert DurationBand.learn([15.0] * 5) is None


def test_check_unit_flags():
    spoken = "Set the tempo to one hundred and twenty B P M."
    ok = check_unit("u", "k", spoken, tone(3), SR, "Set the tempo to 120 BPM.", normaliser=lambda s: s.replace(
        "120 BPM", "one hundred and twenty B P M"))
    assert ok.passed, ok.flags
    bad = check_unit("u", "k", spoken, tone(3), SR, "Set the temple to two hundred.")
    assert not bad.passed and bad.flags[0].startswith("words:")
    empty = check_unit("u", "k", spoken, np.zeros(SR, dtype=np.float32), SR, "")
    assert empty.flags == ["empty: no usable audio"]
    band = DurationBand(low=12, high=18, median=15, n=100)
    slow = check_unit("u", "k", spoken, tone(10), SR, spoken, band=band)
    assert any(f.startswith("duration:") and "slow" in f for f in slow.flags)


# ---------------------------------------------------------------------------
# The stage, with a fake transcriber and the mock voice
# ---------------------------------------------------------------------------
class FakeTranscriber:
    """Returns the expected text, except for chosen units: their first N
    transcriptions are wrong (as if the voice mumbled), then correct."""

    def __init__(self, units, bad: dict[str, int]):
        self.units = {u.id: u for u in units}
        self.bad = dict(bad)
        self.calls = 0

    def transcribe(self, files, ids=None):
        out = []
        for f, uid in zip(files, ids):
            self.calls += 1
            out.append(self.answer(f, self.units[uid]))
        return out

    def answer(self, f, u):
        if self.bad.get(u.id, 0) > 0:
            self.bad[u.id] -= 1
            return {"text": "mumble mumble something else entirely here", "words": []}
        return {"text": u.spoken, "words": [["x", 0.0, 0.1, 0.9]]}


@pytest.fixture
def made_book(home, tmp_path, monkeypatch):
    monkeypatch.setenv("LEKTOR_LOCAL_CACHE", str(tmp_path / "lc"))
    src = fixtures.make_epub(tmp_path / "b.epub")
    paths, cfg, _ = create_or_update_book(home, "qa", fixtures.TITLE, author=fixtures.AUTHOR, source=str(src))
    cfg.voices.main, cfg.voices.secondary = "mock-main", "mock-secondary"
    save_book_config(cfg, paths.book_yaml)
    run_ingest(paths, cfg, log=lambda m: None)
    run_prepare(paths, cfg, log=lambda m: None)
    run_synth(paths, cfg, progress=False, log=lambda m: None)
    units = read_units(paths.units)
    return paths, cfg, units


def distinct_lengths(units):
    """Two or more ordinary paragraph units to play with."""
    return [u for u in units if u.kind == "paragraph" and len(u.spoken) > 60]


def test_qa_retries_and_report(made_book):
    paths, cfg, units = made_book
    picks = distinct_lengths(units)
    fixable, stubborn = picks[0], picks[1]
    tr = FakeTranscriber(units, {fixable.id: 1, stubborn.id: 99})
    s = run_qa(paths, cfg, transcriber=tr, engine_factory=lambda p: MockEngine(), progress=False, log=lambda m: None)
    assert s.checked == s.total_units
    assert s.fixed_by_retry >= 1 and s.still_flagged == 1 and s.flagged_ids == [stubborn.id]
    results = load_results(paths)
    assert results[fixable.cache_key]["passed"] and results[fixable.cache_key]["attempts"] == 2
    assert results[stubborn.cache_key]["attempts"] >= cfg.qa.retries + 1
    html = paths.qa_report.read_text()
    assert stubborn.id in html and "mumble" in html and "data:audio/mpeg;base64," in html
    assert fixable.id not in html.split("Still flagged")[1]
    # Rerunning checks nothing new
    tr2 = FakeTranscriber(units, {})
    run_qa(paths, cfg, transcriber=tr2, engine_factory=lambda p: MockEngine(), progress=False, log=lambda m: None)
    assert tr2.calls == 0
    # Accepting clears the flag
    assert accept_all(paths) == 1
    s3 = run_qa(paths, cfg, transcriber=tr2, engine_factory=lambda p: MockEngine(), progress=False, log=lambda m: None)
    assert s3.still_flagged == 0 and s3.accepted == 1


def test_retry_changes_audio_and_reassembles(made_book):
    paths, cfg, units = made_book
    run_assemble(paths, cfg, log=lambda m: None)
    before = json.loads((paths.root / "audio" / "timing.json").read_text())
    fixable = distinct_lengths(units)[0]
    cache = UnitCache.for_book(paths)
    sig = audio_signature(cache.path(fixable.cache_key))
    tr = FakeTranscriber(units, {fixable.id: 1})
    run_qa(paths, cfg, transcriber=tr, engine_factory=lambda p: MockEngine(), progress=False, log=lambda m: None)
    assert audio_signature(cache.path(fixable.cache_key)) != sig  # a new seed gave new audio
    after = run_assemble(paths, cfg, log=lambda m: None)
    changed = [a["chapter"] for a, b in zip(after["chapters"], before["chapters"]) if a["fingerprint"] != b["fingerprint"]]
    assert changed == [fixable.chapter]


def test_split_on_failure(made_book):
    paths, cfg, units = made_book
    multi = next(u for u in units if len(u.sentences) > 1)
    calls = {"n": 0}

    class SplitFake(FakeTranscriber):
        def answer(self, f, u):
            import soundfile as sf

            a, sr = sf.read(f)
            # The sentence-by-sentence version is longer than one call (gaps added).
            if u.id == multi.id and a.size / sr < len(u.spoken) / 15.0 + 0.2:
                return {"text": "wrong words entirely here now", "words": []}
            return {"text": u.spoken, "words": []}

    s = run_qa(paths, cfg, transcriber=SplitFake(units, {}), engine_factory=lambda p: MockEngine(), progress=False,
               log=lambda m: None)
    r = load_results(paths)[multi.cache_key]
    assert r["passed"] and r["split"] is True


def test_regenerate_and_spot_check(made_book):
    paths, cfg, units = made_book
    tr = FakeTranscriber(units, {})
    run_qa(paths, cfg, transcriber=tr, engine_factory=lambda p: MockEngine(), progress=False, log=lambda m: None)
    target = distinct_lengths(units)[0]
    sig = audio_signature(UnitCache.for_book(paths).path(target.cache_key))
    res = regenerate(paths, cfg, [target.id], transcriber=tr, engine_factory=lambda p: MockEngine(), log=lambda m: None)
    assert res[0].passed and audio_signature(UnitCache.for_book(paths).path(target.cache_key)) != sig
    with pytest.raises(Exception):
        regenerate(paths, cfg, ["c999-p0000-u01"], transcriber=tr, engine_factory=lambda p: MockEngine())
    picked = spot_check(paths, 3, seed=1)
    assert len(picked) == 3 and all(p.exists() for _, p in picked)
