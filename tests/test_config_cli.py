import warnings

import pytest
import yaml

import fixtures
from lektor.cli import main
from lektor.config import BookConfig, BookPaths, load_book_config, save_book_config
from lektor.errors import ConfigError


def test_config_round_trip(tmp_path):
    cfg = BookConfig(book_id="making-music", title="Making Music", author="Dennis DeSantis", year=2015)
    p = tmp_path / "book.yaml"
    save_book_config(cfg, p)
    again = load_book_config(p)
    assert again.year == "2015"
    assert again.pauses.sentence == 0.35
    assert again.audio.loudness_lufs == -18.0
    assert again.voices.main


def test_statement_placeholders():
    cfg = BookConfig(book_id="b", title="T", author="A", publisher="P", year="2015", permission_basis="s31B")
    assert "section 31B" in cfg.statement("accessibility")
    assert "T, by A, published by P in 2015" in cfg.statement("acknowledgement")


def test_friendly_errors(tmp_path):
    p = tmp_path / "book.yaml"
    p.write_text("book_id: Bad Id\ntitle: x\npauses:\n  sentence: lots\n")
    with pytest.raises(ConfigError) as e:
        load_book_config(p)
    assert "book_id" in e.value.message and "pauses.sentence" in e.value.message
    p.write_text("book_id: ok\ntitle: [unclosed\n")
    with pytest.raises(ConfigError) as e:
        load_book_config(p)
    assert "not valid YAML" in e.value.message
    with pytest.raises(ConfigError):
        load_book_config(tmp_path / "missing.yaml")


def test_unknown_key_warns(tmp_path):
    p = tmp_path / "book.yaml"
    p.write_text("book_id: ok\ntitle: x\npauses:\n  sentnce: 0.5\n")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        load_book_config(p)
    assert any("pauses.sentnce" in str(x.message) for x in w)


def test_cli_end_to_end(home, pdf_file, capsys):
    assert main(["new-book", "--home", str(home), "--book", "sample", "--title", fixtures.TITLE,
                 "--author", fixtures.AUTHOR, "--source", str(pdf_file), "--recipient-code", "AF-001"]) == 0
    paths = BookPaths.for_book(home, "sample")
    assert (paths.source_dir / pdf_file.name).exists()
    data = yaml.safe_load(paths.book_yaml.read_text())
    assert data["recipient_code"] == "AF-001" and data["source"] == pdf_file.name
    assert (home / "lexicons").is_dir()
    assert main(["ingest", "--home", str(home), "--book", "sample", "--reader", "fallback"]) == 0
    out = capsys.readouterr().out
    assert "Script:" in out and "chapters" in out
    assert main(["validate", "--home", str(home), "--book", "sample"]) == 0
    paths.script.write_text(paths.script.read_text() + "\n[[figure id=broken]]\n")
    assert main(["validate", "--home", str(home), "--book", "sample"]) == 1
    assert "never closed" in capsys.readouterr().out


def test_cli_friendly_error(home, capsys):
    assert main(["ingest", "--home", str(home), "--book", "nope"]) == 2
    err = capsys.readouterr().err
    assert "Problem:" in err and "What to do:" in err
