"""Engine registry, presets, worker protocol, designed voices and the audition (no GPU needed)."""

import json
import sys

import numpy as np
import pytest
import yaml

import fixtures
from lektor.audition import choose_voice, run_audition
from lektor.books import create_or_update_book
from lektor.config import load_book_config, save_book_config
from lektor.engines.client import WorkerEngine
from lektor.engines.mock import MockEngine
from lektor.engines.registry import BUILTIN_VOICES, ENGINES, engine_class, get_preset, load_designs, load_presets
from lektor.envs import ENV_SPECS
from lektor.errors import EngineError
from lektor.ingest import run_ingest
from lektor.smoke import Report, Row
from lektor.synth import open_engine
from lektor.voice_design import design_clip_path, designs_needed, make_designs

PERMISSIVE = ("Apache-2.0", "MIT", "BSD")


def test_every_engine_imports_without_its_dependencies():
    for name in ENGINES:
        cls = engine_class(name)
        assert cls.name == name
        assert any(l in cls.licence for l in PERMISSIVE), (name, cls.licence)
        assert cls.sample_rate > 0 and cls.max_chars > 50
        if cls.env is not None:
            assert cls.env in ENV_SPECS, f"{name} has no environment spec"


def test_presets_are_valid_and_clone_no_real_person():
    presets = load_presets()
    designs = load_designs()
    assert {"kokoro-bf-emma", "kokoro-bf-isabella", "kokoro-bf-alice", "kokoro-bf-lily", "kokoro-bm-george",
            "kokoro-bm-lewis", "kokoro-bm-fable", "kokoro-bm-daniel"} <= set(presets)
    for name, p in presets.items():
        cls = engine_class(p.engine)
        if p.engine == "qwen3-base":
            assert p.design in designs, f"{name} must clone a designed (synthetic) voice"
        if p.design:
            assert p.design in designs, name
            assert cls.supports_reference_audio, name
        # No preset may point at an arbitrary recording: only designed clips.
        assert p.reference is None, f"{name}: reference clips must be designed (synthetic)"
    for d in designs.values():
        assert len(d.text) > 200, "design text should give a clip of more than 8 seconds"
    data = yaml.safe_load(BUILTIN_VOICES.read_text())
    assert set(data) == {"designs", "presets"}


def test_book_defaults_point_at_real_presets():
    from lektor.config import BookConfig

    cfg = BookConfig(book_id="x", title="x")
    for name in (cfg.voices.main, cfg.voices.secondary, cfg.voices.announcer):
        get_preset(name)


def test_needs_gpu_message(monkeypatch, tmp_path):
    import lektor.synth as synth

    monkeypatch.setattr(synth, "gpu_available", lambda: False)
    with pytest.raises(EngineError) as e:
        open_engine(get_preset("chatterbox-default"), tmp_path)
    assert "needs a GPU" in e.value.message and "Kokoro" in e.value.hint


# ---------------------------------------------------------------------------
# Worker protocol, using the mock engine in a real subprocess
# ---------------------------------------------------------------------------
@pytest.fixture
def worker():
    w = WorkerEngine(MockEngine, python=sys.executable, log=lambda m: None, call_timeout=60, load_timeout=60)
    w.load()
    yield w
    w.unload()


def test_worker_matches_in_process(worker):
    items = [{"text": "Hello there.", "voice": "low", "params": {}, "seed": 1},
             {"text": "A second, longer sentence here.", "voice": "high", "params": {}, "seed": 2}]
    remote = worker.synthesize_batch(items)
    local = MockEngine().synthesize_batch(items)
    assert all(np.array_equal(a, b) for a, b in zip(remote, local))
    assert worker.sample_rate == 24000


def test_worker_survives_stdout_noise_and_errors(worker):
    a = worker.synthesize("Noisy.", "low", {"chatty": True}, 1)
    assert a.size > 0
    with pytest.raises(EngineError) as e:
        worker.synthesize("x", "low", {"fail": True}, 1)
    assert "mock failure requested" in e.value.message
    # still usable after an error
    assert worker.synthesize("Again.", "low", {}, 1).size > 0


def test_worker_crash_is_reported():
    w = WorkerEngine(MockEngine, python=sys.executable, log=lambda m: None, call_timeout=60)
    w.load()
    with pytest.raises(EngineError) as e:
        w.synthesize("x", "low", {"crash": True}, 1)
    assert "stopped unexpectedly" in e.value.message
    w.unload()


# ---------------------------------------------------------------------------
# Designed voices and the audition
# ---------------------------------------------------------------------------
@pytest.fixture
def book(home, tmp_path, monkeypatch):
    monkeypatch.setenv("LEKTOR_LOCAL_CACHE", str(tmp_path / "lc"))
    src = fixtures.make_epub(tmp_path / "b.epub")
    paths, cfg, _ = create_or_update_book(home, "aud", fixtures.TITLE, author=fixtures.AUTHOR, source=str(src))
    cfg.voices.main, cfg.voices.secondary, cfg.voices.announcer = "mock-main", "mock-secondary", "mock-announcer"
    save_book_config(cfg, paths.book_yaml)
    run_ingest(paths, cfg, log=lambda m: None)
    return paths, cfg


def test_make_designs_with_mock(home):
    from lektor.engines.registry import Preset

    before = get_preset("qwen-british-female", home).fingerprint(home)
    assert designs_needed(["qwen-british-female", "chatterbox-british-female", "kokoro-bf-emma"], home) == \
        ["british-female"]
    made = make_designs(["british-female"], home, log=lambda m: None,
                        engine_factory=lambda: MockEngine())
    clip = design_clip_path("british-female", home)
    assert made == [clip] and clip.exists()
    meta = json.loads(clip.with_suffix(".json").read_text())
    assert meta["synthetic"] is True and meta["real_person"] is False
    assert clip.with_suffix(".txt").read_text().startswith("Welcome.")
    assert designs_needed(["qwen-british-female"], home) == []
    # cache keys do not change once the clip exists
    assert get_preset("qwen-british-female", home).fingerprint(home) == before
    assert get_preset("qwen-british-female", home).reference_path(home) == clip


def test_audition_with_mock(book):
    paths, cfg = book
    r = run_audition(paths, cfg, ["mock-main", "mock-secondary"], chapter="", seconds=20, log=lambda m: None,
                     engine_factory=lambda preset: MockEngine())
    assert set(r.letters) == {"A", "B"} and set(r.letters.values()) == {"mock-main", "mock-secondary"}
    assert all(p.exists() for p in r.files.values())
    assert set(r.combined) == {"1x", "1.5x", "2x"}
    from lektor.package.ffmpeg import probe

    d1 = float(probe(r.combined["1x"])["format"]["duration"])
    d2 = float(probe(r.combined["2x"])["format"]["duration"])
    assert abs(d2 - d1 / 2) < 1.0
    key = json.loads((paths.audition_dir / "key.json").read_text())
    assert key["letters"] == r.letters
    fb = (paths.audition_dir / "feedback.txt").read_text()
    assert "Voice A" in fb and "speed" in fb
    letter = next(l for l, p in r.letters.items() if p == "mock-secondary")
    choose_voice(paths, cfg, letter)
    assert load_book_config(paths.book_yaml).voices.main == "mock-secondary"


def test_audition_chapter_choice(book):
    from lektor.audition import passage_units

    paths, cfg = book
    units = passage_units(paths, cfg, "mock-main", chapter="Reference", seconds=5)
    assert units and units[0].chapter_title.endswith("2. Use a Reference Track")
    with pytest.raises(Exception):
        passage_units(paths, cfg, "mock-main", chapter="No such chapter")


def test_smoke_report_table():
    r = Report(env="python 3.12")
    r.add(Row("kokoro: load", "PASS", 12.3, "device=cuda"))
    r.add(Row("qwen3-base: x", "FAIL", 1.0, "boom", "Traceback ..."))
    t = r.table()
    assert "kokoro: load" in t and "1 passed, 1 failed" in t and "--- qwen3-base: x ---" in t


@pytest.mark.slow
@pytest.mark.network
def test_worker_in_isolated_uv_environment(tmp_path, monkeypatch):
    """A real uv venv with only numpy: the worker must run there, finding
    Lektor through PYTHONPATH and nothing from the main environment."""
    import shutil
    import subprocess

    from lektor import envs
    from lektor.envs import EnvSpec, ensure_env, register_env

    if not shutil.which("uv"):
        pytest.skip("uv is not installed")
    monkeypatch.setenv("LEKTOR_ENV_ROOT", str(tmp_path / "envs"))
    monkeypatch.setenv("LEKTOR_TORCH_BACKEND", "cpu")
    register_env(EnvSpec(name="selftest", packages=("numpy",), python=f"{sys.version_info.major}.{sys.version_info.minor}",
                         check="import numpy"))
    try:
        py = ensure_env("selftest", log=lambda m: None)
    except Exception as e:  # offline sandbox
        pytest.skip(f"could not build a uv environment here: {e}")
    # The environment must not see the main environment's packages.
    out = subprocess.run([str(py), "-c", "import yaml"], capture_output=True, env=envs.env_vars("selftest"))
    assert out.returncode != 0, "the isolated environment should not see PyYAML from the main environment"
    w = WorkerEngine(MockEngine, python=str(py), log=lambda m: None, call_timeout=60, load_timeout=60)
    w.load()
    try:
        a = w.synthesize("Isolated hello.", "low", {}, 3)
        assert np.array_equal(a, MockEngine().synthesize("Isolated hello.", "low", {}, 3))
    finally:
        w.unload()
    assert envs.is_ready("selftest")


@pytest.mark.slow
@pytest.mark.network
def test_real_kokoro_on_cpu(monkeypatch):
    """Optional: build the Kokoro environment and synthesise on CPU.

    Needs PyPI, the PyTorch index and Hugging Face. Skipped unless
    LEKTOR_RUN_NETWORK_TESTS=1, and skipped when the network blocks them.
    """
    import os
    import urllib.request

    if os.environ.get("LEKTOR_RUN_NETWORK_TESTS") != "1":
        pytest.skip("set LEKTOR_RUN_NETWORK_TESTS=1 to run (downloads torch and the Kokoro model)")
    try:
        urllib.request.urlopen("https://huggingface.co/api/models/hexgrad/Kokoro-82M", timeout=10)
    except Exception as e:
        pytest.skip(f"Hugging Face is not reachable here: {e}")
    monkeypatch.setenv("LEKTOR_TORCH_BACKEND", "cpu")
    w = WorkerEngine(engine_class("kokoro"), log=print, options={"device": "cpu"})
    try:
        w.load()
    except EngineError as e:
        pytest.skip(f"Kokoro could not be installed or loaded here: {e.message[:200]}")
    try:
        a = w.synthesize("Set the tempo to one hundred and twenty B P M.", "bf_emma", {"speed": 1.0}, 1)
        assert a.size / w.sample_rate > 1.0 and np.isfinite(a).all()
    finally:
        w.unload()
