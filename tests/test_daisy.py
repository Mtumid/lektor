"""DAISY 2.02 writer, its checks, and production records."""

import re
import shutil
import zipfile

import pytest

import fixtures
from lektor.assemble import run_assemble
from lektor.books import create_or_update_book
from lektor.config import save_book_config
from lektor.ingest import run_ingest
from lektor.package import run_package
from lektor.package.daisy202 import clock, npt, page_class
from lektor.package.daisy_check import check_daisy, check_daisy_zip, fetch_schemas
from lektor.package.ffmpeg import probe
from lektor.prepare import run_prepare
from lektor.records import COLUMNS, read_records
from lektor.synth import run_synth


def test_formats():
    assert clock(0) == "0:00:00"
    assert clock(3723.4) == "1:02:03"
    assert clock(62.5, fraction=True) == "0:01:02.500"
    assert npt(2.1971) == "npt=2.197s"
    assert page_class("47") == "page-normal"
    assert page_class("xii") == "page-front"
    assert page_class("A3") == "page-special"


@pytest.fixture(scope="module")
def daisy_book(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("daisy")
    import os

    old = os.environ.get("LEKTOR_LOCAL_CACHE")
    os.environ["LEKTOR_LOCAL_CACHE"] = str(tmp / "lc")
    try:
        home = tmp / "Lektor"
        home.mkdir()
        pdf = fixtures.make_pdf(tmp / "s.pdf", with_page_labels=True)
        paths, cfg, _ = create_or_update_book(home, "sample", fixtures.TITLE, author=fixtures.AUTHOR,
                                              publisher="Test Press", year="2020", source=str(pdf),
                                              recipient_code="AF-0001")
        cfg.ingest.reader = "fallback"
        cfg.voices.main, cfg.voices.secondary = "mock-main", "mock-secondary"
        save_book_config(cfg, paths.book_yaml)
        run_ingest(paths, cfg, log=lambda m: None)
        run_prepare(paths, cfg, log=lambda m: None)
        run_synth(paths, cfg, progress=False, log=lambda m: None)
        timing = run_assemble(paths, cfg, log=lambda m: None)
        res = run_package(paths, cfg, log=lambda m: None)
        folder = tmp / "unzipped"
        with zipfile.ZipFile(res.files["daisy"]) as z:
            z.extractall(folder)
        yield paths, cfg, timing, res, folder
    finally:
        if old is None:
            os.environ.pop("LEKTOR_LOCAL_CACHE", None)
        else:
            os.environ["LEKTOR_LOCAL_CACHE"] = old


def test_daisy_is_valid(daisy_book):
    paths, cfg, timing, res, folder = daisy_book
    rep = check_daisy(folder, log=lambda m: None)
    assert rep.errors == [], rep.text()
    assert rep.warnings == [], rep.text()
    if fetch_schemas() is not None:
        assert any("validated against the DAISY Pipeline 2 schemas" in i for i in rep.info)
    assert check_daisy_zip(res.files["daisy"], log=lambda m: None).errors == []


def test_daisy_structure(daisy_book):
    paths, cfg, timing, res, folder = daisy_book
    names = sorted(p.name for p in folder.iterdir())
    n = len(timing["chapters"])
    assert "ncc.html" in names and len([x for x in names if x.endswith(".smil")]) == n
    assert all(re.fullmatch(r"[a-z0-9._-]+", x) for x in names)
    ncc = (folder / "ncc.html").read_text()
    assert '<h1 class="title"' in ncc and "audioFullText" in ncc and "Daisy 2.02" in ncc
    assert "1. Start With a Beat" in ncc and "<h2" in ncc
    assert ncc.count('class="page-normal"') == 4 and ncc.count('class="page-front"') == 2
    assert "print disabled" in ncc and "All rights in the original work" in ncc  # statements in metadata
    total = sum(c["duration"] for c in timing["chapters"])
    m = re.search(r'ncc:totalTime" content="(\d+):(\d\d):(\d\d)"', ncc)
    assert abs(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) - total) <= 1
    # every sentence of the book is a par with contiguous clips
    smil = (folder / "s0003.smil").read_text()
    clips = [(float(a), float(b)) for a, b in re.findall(r'clip-begin="npt=([\d.]+)s" clip-end="npt=([\d.]+)s"', smil)]
    assert clips[0][0] == 0.0
    assert all(abs(clips[k][1] - clips[k + 1][0]) < 1e-6 for k in range(len(clips) - 1))
    content = (folder / "c0003.html").read_text()
    assert content.count('class="sentence"') == len(clips)
    assert "Every session starts with a blank arrangement and a tempo of 120 BPM." in content  # display text, not spoken
    # audio
    info = probe(folder / "a0003.mp3")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert a["codec_name"] == "mp3" and a["channels"] == 1 and a["sample_rate"] == "44100"
    assert abs(float(info["format"]["duration"]) - timing["chapters"][2]["duration"]) < 0.2


def test_records(daisy_book):
    paths, cfg, timing, res, folder = daisy_book
    rows = read_records(paths.production_records)
    assert rows and list(rows[-1]) == COLUMNS
    r = rows[-1]
    assert r["book_id"] == "sample" and r["recipient_code"] == "AF-0001" and r["permission_basis"] == "s31B"
    assert set(r["formats_produced"].split(", ")) == {"daisy", "m4b", "mp3"}
    assert "mock-main" in r["engine_and_voice_preset"] and r["lektor_version"]
    assert any("placeholder wording" in n for n in res.notes)
    assert any("quality check" in n for n in res.notes)


# ---------------------------------------------------------------------------
# The checker catches broken books
# ---------------------------------------------------------------------------
def broken(folder, tmp_path, name, old, new, count=1):
    dst = tmp_path / "broken"
    shutil.copytree(folder, dst)
    p = dst / name
    text = p.read_text()
    assert old in text, old
    p.write_text(text.replace(old, new, count))
    return check_daisy(dst, log=lambda m: None, use_schemas=False)


def test_check_finds_wrong_total_time(daisy_book, tmp_path):
    _, _, _, _, folder = daisy_book
    ncc = (folder / "ncc.html").read_text()
    tt = re.search(r'ncc:totalTime" content="([^"]+)"', ncc).group(1)
    rep = broken(folder, tmp_path, "ncc.html", f'content="{tt}"', 'content="9:00:00"')
    assert any("ncc:totalTime" in e for e in rep.errors)


def test_check_finds_unreferenced_smil(daisy_book, tmp_path):
    _, _, _, _, folder = daisy_book
    rep = broken(folder, tmp_path, "ncc.html", 'href="s0004.smil#', 'href="s0003.smil#', count=-1)
    assert any("s0004.smil is never referenced" in e for e in rep.errors)


def test_check_finds_missing_id(daisy_book, tmp_path):
    _, _, _, _, folder = daisy_book
    smil = (folder / "s0003.smil").read_text()
    first_text = re.search(r'src="(c0003\.html#[^"]+)"', smil).group(1)
    rep = broken(folder, tmp_path, "s0003.smil", first_text, "c0003.html#nothing-here")
    assert any("missing id" in e for e in rep.errors)


def test_check_finds_bad_clip(daisy_book, tmp_path):
    _, _, _, _, folder = daisy_book
    rep = broken(folder, tmp_path, "s0003.smil", 'clip-begin="npt=0.000s"', 'clip-begin="0.0"')
    assert any("npt=" in e for e in rep.errors)


def test_check_finds_heading_jump(daisy_book, tmp_path):
    _, _, _, _, folder = daisy_book
    ncc = (folder / "ncc.html").read_text()
    h2 = re.search(r"<h2 [^>]*>.*?</h2>", ncc).group(0)
    rep = broken(folder, tmp_path, "ncc.html", h2, h2.replace("<h2 ", "<h4 ").replace("</h2>", "</h4>"))
    assert any("jumps from h1 to h4" in e for e in rep.errors)


def test_schema_catches_invalid_ncc(daisy_book, tmp_path):
    _, _, _, _, folder = daisy_book
    if fetch_schemas() is None:
        pytest.skip("DAISY Pipeline schemas could not be downloaded")
    dst = tmp_path / "bad"
    shutil.copytree(folder, dst)
    p = dst / "ncc.html"
    p.write_text(p.read_text().replace('<h1 class="title"', '<h1 class="nottitle"', 1))
    rep = check_daisy(dst, log=lambda m: None)
    assert any("(schema)" in e for e in rep.errors)
