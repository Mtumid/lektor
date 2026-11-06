"""Smoke test: run every real component once on a tiny sample.

Used by ``notebooks/Smoke_Test.ipynb``. Prints a compact PASS / FAIL table
plus environment details, formatted so the librarian can paste it straight
back to the developer. Audio samples are saved to ``MyDrive/Lektor/smoke/``
so they can be listened to.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import __version__

SAMPLE = ("Set the tempo to 120 BPM, then sidechain the pad at -6 dB with a 4:1 ratio. "
          "The ii–V–I progression in F#m sounds warm in 4/4 time.")
CONSISTENCY = [
    "The first sentence comes from the very start of the book.",
    "This one might be read five hours later, in a different chapter entirely.",
    "And this last one is about mixing, compression and the low end of a track.",
]


@dataclass
class Row:
    name: str
    status: str = "SKIP"
    seconds: float = 0.0
    detail: str = ""
    trace: str = ""


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    env: str = ""

    def add(self, row: Row) -> Row:
        self.rows.append(row)
        return row

    def table(self) -> str:
        w = max(len(r.name) for r in self.rows) if self.rows else 10
        lines = [f"LEKTOR SMOKE TEST  lektor {__version__}  {_dt.datetime.now():%Y-%m-%d %H:%M}", self.env, "",
                 f"{'STEP'.ljust(w)}  RESULT  TIME    DETAIL"]
        for r in self.rows:
            lines.append(f"{r.name.ljust(w)}  {r.status:<6}  {r.seconds:>5.0f}s  {r.detail}")
        fails = [r for r in self.rows if r.status == "FAIL"]
        lines += ["", f"{sum(r.status == 'PASS' for r in self.rows)} passed, {len(fails)} failed, "
                      f"{sum(r.status == 'SKIP' for r in self.rows)} skipped"]
        for r in fails:
            lines += ["", f"--- {r.name} ---", r.trace.strip()[-1500:]]
        return "\n".join(lines)


def _run(report: Report, name: str, fn: Callable[[], str], log: Callable[[str], None]) -> Row:
    row = report.add(Row(name))
    log(f"▶ {name} ...")
    t = time.monotonic()
    try:
        row.detail = fn() or ""
        row.status = "PASS"
    except Exception as e:  # noqa: BLE001 - every failure is reported
        row.status = "FAIL"
        row.detail = (getattr(e, "message", None) or f"{type(e).__name__}: {e}").splitlines()[0][:110]
        row.trace = traceback.format_exc()
        tail = getattr(e, "message", "")
        if tail and "\n" in tail:
            row.trace = tail + "\n" + row.trace
    row.seconds = time.monotonic() - t
    log(f"  {row.status} ({row.seconds:.0f}s) {row.detail}")
    return row


def _save_sample(audio: np.ndarray, sr: int, out: Path) -> None:
    import soundfile as sf

    from .package.ffmpeg import run_ffmpeg

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        sf.write(str(wav), audio, sr, subtype="PCM_16")
        run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "96k", "-ac", "1", str(out)])


def run_smoke(home: Path, presets: Optional[list[str]] = None, log: Callable[[str], None] = print,
              include_docling: bool = True, include_qa: bool = True) -> Report:
    from .notebook import environment_report

    home = Path(home)
    out_dir = home / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = Report(env=environment_report())
    presets = presets or ["kokoro-bf-emma", "chatterbox-default", "chatterbox-turbo-default", "qwen-ryan",
                          "qwen-british-female", "chatterbox-british-female", "chatterbox-turbo-british-female"]

    # Test fixtures need reportlab and python-docx (test extras).
    if importlib.util.find_spec("reportlab") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "reportlab"], capture_output=True)
    repo_tests = Path(__file__).resolve().parent.parent / "tests"
    if str(repo_tests) not in sys.path:
        sys.path.insert(0, str(repo_tests))

    from .engines.registry import engine_class, get_preset
    from .speech.lexicon import BUILTIN_DIR, Lexicon, read_lexicon_csv
    from .speech.normaliser import Normaliser
    from .synth import check_audio, open_engine, synth_params

    norm = Normaliser(Lexicon(read_lexicon_csv(BUILTIN_DIR / "music_production.csv")))
    spoken = norm(SAMPLE)

    # 1. Docling
    if include_docling:
        def docling_step():
            import fixtures

            from .config import BookConfig
            from .ingest import read_source
            from .ingest.common import build_script
            from .script.model import Heading

            with tempfile.TemporaryDirectory() as td:
                pdf = fixtures.make_pdf(Path(td) / "sample.pdf")
                cfg = BookConfig(book_id="smoke", title="Smoke")
                cfg.ingest.reader = "docling"
                items, n, _, reader, notes = read_source(pdf, cfg, log=lambda m: None)
                script, stats = build_script(items, meta={}, n_pages=n, skip_sections=["contents"])
            hs = [b.text for b in script.blocks if isinstance(b, Heading)]
            ok = "1. Start With a Beat" in hs
            if not ok:
                raise AssertionError(f"headings found: {hs}")
            return f"{len(hs)} headings, {stats['figures']} figures, {stats['tables']} tables; {'; '.join(notes)[:60]}"
        _run(report, "docling: PDF ingest", docling_step, log)

    # 2. Designed voices (needed by the British Chatterbox and Qwen presets)
    from .voice_design import designs_needed, make_designs

    needed = designs_needed(presets, home)

    def design_step():
        made = make_designs(needed, home, log)
        return ", ".join(f"{p.stem} {p.stat().st_size // 1024} KB" for p in made) or "already made"
    if needed or any(get_preset(p, home).design for p in presets):
        _run(report, "qwen3-design: voice design", design_step, log)

    # 3. Each engine/preset
    by_engine: dict[str, list[str]] = {}
    for p in presets:
        by_engine.setdefault(get_preset(p, home).engine, []).append(p)
    clone_outputs: dict[str, list[Path]] = {}
    for engine_name, names in by_engine.items():
        holder: dict = {}

        def load_step(engine_name=engine_name, names=names):
            t = time.monotonic()
            holder["engine"] = open_engine(get_preset(names[0], home), home, log)
            info = getattr(holder["engine"], "info", {}) or {}
            return f"loaded in {time.monotonic() - t:.0f}s; " + ", ".join(f"{k}={v}" for k, v in info.items()
                                                                          if k in ("device", "dtype", "torch"))
        if _run(report, f"{engine_name}: load", load_step, log).status != "PASS":
            continue
        engine = holder["engine"]
        try:
            for name in names:
                def synth_step(name=name):
                    preset = get_preset(name, home)
                    ref = preset.reference_path(home)
                    t = time.monotonic()
                    a = np.asarray(engine.synthesize(spoken, preset.voice, synth_params(preset.params), 1234,
                                                     str(ref) if ref else None), dtype=np.float32)
                    took = time.monotonic() - t
                    problem = check_audio(a, engine.sample_rate, spoken)
                    if problem:
                        raise AssertionError(problem)
                    secs = a.size / engine.sample_rate
                    _save_sample(a, engine.sample_rate, out_dir / f"{name}.mp3")
                    if engine_name == "qwen3-base" or (preset.design and engine_name == "chatterbox"):
                        files = []
                        for k, sent in enumerate(CONSISTENCY):
                            b = np.asarray(engine.synthesize(norm(sent), preset.voice, synth_params(preset.params),
                                                             100 + k, str(ref) if ref else None), dtype=np.float32)
                            f = out_dir / f"{name}_consistency_{k + 1}.wav"
                            import soundfile as sf

                            sf.write(str(f), b, engine.sample_rate)
                            files.append(f)
                        clone_outputs[name] = files
                    return (f"{secs:.1f}s audio at {engine.sample_rate} Hz in {took:.1f}s "
                            f"(RTF {took / secs:.2f}); peak {float(np.max(np.abs(a))):.2f}")
                _run(report, f"{engine_name}: {name}", synth_step, log)
        finally:
            engine.unload()

    # 4. Voice consistency of designed voices (speaker embeddings)
    if clone_outputs:
        def consistency_step():
            from .engines.client import WorkerEngine

            enc = WorkerEngine(engine_class("chatterbox"), log=log)
            enc.load()
            try:
                parts = []
                for name, files in clone_outputs.items():
                    design = get_preset(name, home).design
                    ref = get_preset(name, home).reference_path(home)
                    vecs = [np.asarray(enc.call("embed", path=str(f))) for f in [ref, *files]]
                    vecs = [v / (np.linalg.norm(v) + 1e-9) for v in vecs]
                    to_ref = [float(np.dot(vecs[0], v)) for v in vecs[1:]]
                    between = [float(np.dot(vecs[i], vecs[j])) for i in range(1, len(vecs)) for j in range(i + 1, len(vecs))]
                    parts.append(f"{name}: to ref min {min(to_ref):.2f}, between min {min(between):.2f}")
                    (out_dir / f"{name}_consistency.json").write_text(json.dumps(
                        {"design": design, "to_reference": to_ref, "between": between}, indent=1))
                return "; ".join(parts)
            finally:
                enc.unload()
        _run(report, "voice consistency (cosine)", consistency_step, log)

    # 5. Whisper: transcribe every sample, word error rate per voice
    samples = [(p, out_dir / f"{p}.mp3") for p in presets if (out_dir / f"{p}.mp3").exists()]
    if include_qa and samples:
        def whisper_step():
            from .qa.checks import error_rates
            from .qa.whisper import WhisperTranscriber

            with WhisperTranscriber("auto", log=log) as tr:
                info = tr.worker.info
                outs = tr.transcribe([str(f) for _, f in samples])
            parts = []
            for (name, _), o in zip(samples, outs):
                wer, _, _ = error_rates(spoken, norm(o["text"]))
                parts.append(f"{name} {wer:.0%}")
            (out_dir / "whisper_transcripts.json").write_text(json.dumps(
                {n: o["text"] for (n, _), o in zip(samples, outs)}, indent=1))
            return f"{info.get('model')} {info.get('compute_type')} on {info.get('device')}; WER " + ", ".join(parts)
        _run(report, "whisper: transcribe samples", whisper_step, log)

    # 6. Whole pipeline with the mock voice, all packagers
    def pipeline_step():
        import fixtures

        from .assemble import run_assemble
        from .books import create_or_update_book
        from .config import save_book_config
        from .ingest import run_ingest
        from .package import run_package
        from .package.ffmpeg import probe
        from .prepare import run_prepare
        from .synth import run_synth

        with tempfile.TemporaryDirectory() as td:
            h = Path(td) / "Lektor"
            pdf = fixtures.make_pdf(Path(td) / "s.pdf")
            paths, cfg, _ = create_or_update_book(h, "smoke", "Smoke Test Book", author="Lektor", source=str(pdf))
            cfg.ingest.reader = "fallback"
            cfg.voices.main, cfg.voices.secondary = "mock-main", "mock-secondary"
            save_book_config(cfg, paths.book_yaml)
            run_ingest(paths, cfg, log=lambda m: None)
            run_prepare(paths, cfg, log=lambda m: None)
            run_synth(paths, cfg, progress=False, log=lambda m: None)
            t = run_assemble(paths, cfg, log=lambda m: None)
            res = run_package(paths, cfg, log=lambda m: None)
            info = probe(res.files["m4b"])
            return (f"{len(t['chapters'])} chapters; m4b {len(info['chapters'])} chapter marks; "
                    + ", ".join(sorted(res.files)))
    _run(report, "pipeline + packagers (mock voice)", pipeline_step, log)
    return report
