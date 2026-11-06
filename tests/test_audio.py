"""Mock engine, cache, assembly, timing map and packaging, end to end."""

import json
import zipfile

import numpy as np
import pytest
import soundfile as sf

import fixtures
from lektor.assemble import (apply_fades, integrated_loudness, limit_true_peak, page_starts, headings, run_assemble,
                             trim_silence, true_peak)
from lektor.books import create_or_update_book
from lektor.cache import UnitCache
from lektor.chunk.plan import read_units
from lektor.cli import main
from lektor.config import save_book_config
from lektor.engines.mock import MockEngine
from lektor.ingest import run_ingest
from lektor.package import run_package
from lektor.package.ffmpeg import probe
from lektor.prepare import run_prepare
from lektor.synth import run_synth


@pytest.fixture
def local_cache(tmp_path, monkeypatch):
    d = tmp_path / "local_cache"
    monkeypatch.setenv("LEKTOR_LOCAL_CACHE", str(d))
    return d


def mock_book(home, src, book_id="sample"):
    paths, cfg, _ = create_or_update_book(home, book_id, fixtures.TITLE, author=fixtures.AUTHOR,
                                          publisher="Test Press", year="2020", source=str(src))
    cfg.ingest.reader = "fallback"
    cfg.voices.main = "mock-main"
    cfg.voices.secondary = "mock-secondary"
    save_book_config(cfg, paths.book_yaml)
    run_ingest(paths, cfg, log=lambda m: None)
    return paths, cfg


def test_mock_engine_is_deterministic():
    e = MockEngine()
    a = e.synthesize("Hello there, world.", "low", {}, 1)
    b = e.synthesize("Hello there, world.", "low", {}, 1)
    c = e.synthesize("Hello there, world, and more words here.", "low", {}, 1)
    assert np.array_equal(a, b)
    assert c.size > a.size
    assert a.dtype == np.float32 and a.ndim == 1


def test_signal_helpers():
    sr = 24000
    tone = (0.5 * np.sin(np.linspace(0, 2 * np.pi * 300, sr))).astype(np.float32)
    padded = np.concatenate([np.zeros(sr // 2), tone, np.zeros(sr // 2)]).astype(np.float32)
    trimmed = trim_silence(padded, sr)
    assert sr <= trimmed.size <= sr + int(0.1 * sr)
    faded = apply_fades(tone, sr, 10)
    assert abs(faded[0]) < 1e-6 and abs(faded[-1]) < 1e-3
    loud = (tone * 1.9).astype(np.float32)
    limited = limit_true_peak(loud, sr, -1.5)
    assert true_peak(limited, sr) <= 10 ** (-1.5 / 20) * 1.02


def test_cache_sync_and_restore(tmp_path):
    drive = tmp_path / "drive_cache"
    c1 = UnitCache(drive, tmp_path / "local1", batch_size=2)
    for k in ("aa11", "bb22", "cc33"):
        c1.put(k, np.zeros(100, dtype=np.float32) + 0.1, 24000)
        c1.sync()
    assert len(list((drive / "batches").glob("*.zip"))) == 1  # first two units
    c1.sync(force=True)
    # a fresh machine (new local folder) restores everything from Drive
    c2 = UnitCache(drive, tmp_path / "local2")
    assert c2.restore() == 3
    assert c2.keys() == {"aa11", "bb22", "cc33"}
    a, sr = c2.get("bb22")
    assert sr == 24000 and a.size == 100
    assert c2.restore() == 0
    c2.compact()
    assert len(list((drive / "batches").glob("*.zip"))) == 1


def test_end_to_end_pdf(home, pdf_file, local_cache):
    paths, cfg = mock_book(home, pdf_file)
    prep = run_prepare(paths, cfg, log=lambda m: None)
    assert paths.units.exists() and paths.lexicon_suggestions.exists()
    r = run_synth(paths, cfg, progress=False, log=lambda m: None)
    assert r.made == r.total and not r.failed
    timing = run_assemble(paths, cfg, log=lambda m: None)

    # Timing map: monotonic, consistent with the audio files
    for ch in timing["chapters"]:
        audio, sr = sf.read(str(paths.root / "audio" / ch["file"]))
        assert sr == cfg.audio.sample_rate
        assert abs(audio.size / sr - ch["duration"]) < 0.01
        last = 0.0
        for u in ch["units"]:
            assert last <= u["start"] < u["end"] <= ch["duration"] + 1e-6
            last = u["end"]
            for s in u["sentences"]:
                assert u["start"] - 1e-6 <= s["start"] <= s["end"] <= u["end"] + 1e-6
        lufs = integrated_loudness(audio.astype(np.float32), sr)
        if ch["duration"] > 3:
            assert abs(lufs - cfg.audio.loudness_lufs) < 1.0, (ch["title"], lufs)
        assert true_peak(audio.astype(np.float32), sr) <= 10 ** (cfg.audio.true_peak_dbtp / 20) * 1.05
    assert timing["chapters"][0]["title"] == "About this audiobook"
    pages = [p["label"] for ch in timing["chapters"] for p in page_starts(ch)]
    assert pages == ["i", "ii", "1", "2", "3", "4"] or [p for p in pages if p.isdigit()] == ["1", "2", "3", "4"]
    hs = [h["text"] for ch in timing["chapters"] for h in headings(ch)]
    assert "1. Start With a Beat" in hs and "Problems of Finishing" in hs

    # Assembly again: nothing rebuilt
    t2 = run_assemble(paths, cfg, log=lambda m: None)
    assert [c["fingerprint"] for c in t2["chapters"]] == [c["fingerprint"] for c in timing["chapters"]]

    res = run_package(paths, cfg, formats=["m4b", "mp3"], log=lambda m: None)
    info = probe(res.files["m4b"])
    titles = [c["tags"]["title"] for c in info["chapters"]]
    assert titles == [c["title"] for c in timing["chapters"]]
    starts = [float(c["start_time"]) for c in info["chapters"]]
    assert starts == sorted(starts) and starts[0] == 0.0
    assert abs(float(info["format"]["duration"]) - timing["total_duration"]) < 0.5
    audio_streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert audio_streams[0]["codec_name"] == "aac" and audio_streams[0]["channels"] == 1
    tags = info["format"]["tags"]
    assert tags["title"] == fixtures.TITLE and tags["artist"] == fixtures.AUTHOR
    assert "print disabled" in tags["comment"] and tags["composer"].startswith("Synthetic voice")
    assert any(s["codec_type"] == "video" for s in info["streams"])  # cover art from page 1

    with zipfile.ZipFile(res.files["mp3"]) as z:
        names = z.namelist()
        assert names[0].endswith("/01 About this audiobook.mp3")
        assert len(names) == len(timing["chapters"])
        z.extract(names[2], paths.root / "x")
    mp3 = probe(paths.root / "x" / names[2])
    assert mp3["format"]["tags"]["track"].startswith("3/")
    assert mp3["format"]["tags"]["album"] == fixtures.TITLE


def test_edit_regenerates_only_changed_units(home, pdf_file, local_cache):
    paths, cfg = mock_book(home, pdf_file)
    run_prepare(paths, cfg, log=lambda m: None)
    run_synth(paths, cfg, progress=False, log=lambda m: None)
    text = paths.script.read_text().replace("Pick a finished track", "Pick a finished, polished track")
    paths.script.write_text(text)
    run_prepare(paths, cfg, log=lambda m: None)
    r = run_synth(paths, cfg, progress=False, log=lambda m: None)
    assert r.made == 1


def test_resume_after_disconnect(home, pdf_file, tmp_path, monkeypatch):
    monkeypatch.setenv("LEKTOR_LOCAL_CACHE", str(tmp_path / "machine1"))
    paths, cfg = mock_book(home, pdf_file)
    run_prepare(paths, cfg, log=lambda m: None)
    units = read_units(paths.units)
    run_synth(paths, cfg, units=units[:10], progress=False, log=lambda m: None)
    # a new Colab machine: empty local cache, same Drive
    monkeypatch.setenv("LEKTOR_LOCAL_CACHE", str(tmp_path / "machine2"))
    r = run_synth(paths, cfg, progress=False, log=lambda m: None)
    assert r.already >= 10 and r.made == r.total - r.already


@pytest.mark.parametrize("maker", ["make_epub", "make_docx"])
def test_end_to_end_other_formats(home, tmp_path, local_cache, maker):
    ext = {"make_epub": "epub", "make_docx": "docx"}[maker]
    src = getattr(fixtures, maker)(tmp_path / f"s.{ext}")
    paths, cfg = mock_book(home, src, book_id=f"sample-{ext}")
    run_prepare(paths, cfg, log=lambda m: None)
    run_synth(paths, cfg, progress=False, log=lambda m: None)
    timing = run_assemble(paths, cfg, log=lambda m: None)
    res = run_package(paths, cfg, formats=["m4b"], log=lambda m: None)
    assert len(probe(res.files["m4b"])["chapters"]) == len(timing["chapters"]) >= 3


def test_cli_full_run(home, pdf_file, local_cache, capsys):
    paths, cfg = mock_book(home, pdf_file)
    args = ["--home", str(home), "--book", "sample"]
    assert main(["prepare", *args]) == 0
    assert main(["synth", *args, "--no-progress"]) == 0
    assert main(["assemble", *args]) == 0
    assert main(["package", *args, "--formats", "m4b,mp3"]) == 0
    out = capsys.readouterr().out
    assert "m4b:" in out and "mp3:" in out
    assert "synth" in paths.log.read_text() and "package" in paths.log.read_text()
