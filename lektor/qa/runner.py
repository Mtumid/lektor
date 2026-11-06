"""The quality check stage: transcribe, check, retry, report.

1. Every unit not yet checked (or whose audio changed) is transcribed with
   faster-whisper and checked (:mod:`lektor.qa.checks`). The normal speaking
   rate of each voice is learned from its first 300 units.
2. Units that fail are made again, up to ``qa.retries`` times (default 3)
   with new seeds. If a multi-sentence unit still fails and
   ``qa.split_on_failure`` is on, its sentences are made one at a time and
   joined. The best attempt is kept.
3. ``qa/qa_report.html`` lists what is still flagged, with the text, what
   Whisper heard, a word diff and an audio player for each.

Results live in ``qa/qa_results.json`` keyed by cache key, with a signature
of the audio, so a rerun only checks what is new or changed. The librarian
can accept the remaining flags, or ask for particular units to be made
again (:func:`regenerate`).
"""

from __future__ import annotations

import hashlib
import json
import random
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import soundfile as sf

from ..cache import UnitCache
from ..chunk.plan import Unit, read_units, stable_int
from ..config import BookConfig, BookPaths, append_log
from ..engines.registry import get_preset
from ..errors import LektorError
from .checks import DurationBand, QAResult, check_unit

BAND_UNITS = 300
TRANSCRIBE_BATCH = 16


def audio_signature(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]


def results_path(paths: BookPaths) -> Path:
    return paths.qa_dir / "qa_results.json"


def load_results(paths: BookPaths) -> dict[str, dict]:
    p = results_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_results(paths: BookPaths, results: dict[str, dict]) -> None:
    paths.qa_dir.mkdir(parents=True, exist_ok=True)
    tmp = results_path(paths).with_suffix(".tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    tmp.replace(results_path(paths))


def _result(d: dict) -> QAResult:
    return QAResult(**{k: v for k, v in d.items() if k in QAResult.__dataclass_fields__})


@dataclass
class QASummary:
    checked: int = 0
    passed_first_time: int = 0
    fixed_by_retry: int = 0
    still_flagged: int = 0
    accepted: int = 0
    total_units: int = 0
    seconds: float = 0.0
    flagged_ids: list[str] = field(default_factory=list)


class QARunner:
    def __init__(self, paths: BookPaths, cfg: BookConfig, units: Optional[list[Unit]] = None,
                 cache: Optional[UnitCache] = None, transcriber=None, engine_factory=None,
                 log: Callable[[str], None] = print) -> None:
        self.paths, self.cfg = paths, cfg
        self.units = units if units is not None else read_units(paths.units)
        self.cache = cache or UnitCache.for_book(paths)
        self.transcriber = transcriber
        self.engine_factory = engine_factory
        self.log = log
        self.results = load_results(paths)
        from ..prepare import build_normaliser

        self.norm = build_normaliser(paths, cfg)
        self.bands: dict[str, Optional[DurationBand]] = {}
        self.engines: dict[str, object] = {}

    # -- helpers -----------------------------------------------------------
    def unique_units(self) -> list[Unit]:
        seen, out = set(), []
        for u in self.units:
            if u.cache_key not in seen:
                seen.add(u.cache_key)
                out.append(u)
        return out

    def learn_bands(self) -> None:
        """Characters-per-second band per preset, from its first units in book order."""
        rates: dict[str, list[float]] = defaultdict(list)
        from ..assemble import trim_silence

        for u in self.unique_units():
            if len(rates[u.preset]) >= BAND_UNITS or not self.cache.has(u.cache_key) or len(u.spoken) < 20:
                continue
            r = self.results.get(u.cache_key)
            if r and r.get("chars_per_second"):
                rates[u.preset].append(r["chars_per_second"])
                continue
            a, sr = self.cache.get(u.cache_key)
            d = trim_silence(a, sr).size / sr
            if d > 0:
                rates[u.preset].append(len(u.spoken) / d)
        self.bands = {p: DurationBand.learn(r) for p, r in rates.items()}
        bands_out = {p: (b.__dict__ if b else None) for p, b in self.bands.items()}
        self.paths.qa_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.qa_dir / "voice_stats.json").write_text(json.dumps(bands_out, indent=1))

    def _check(self, u: Unit, audio: np.ndarray, sr: int, tr: dict, seed: int) -> QAResult:
        return check_unit(u.id, u.cache_key, u.spoken, audio, sr, tr.get("text"), max_wer=self.cfg.qa.max_wer,
                          max_silence_s=self.cfg.qa.max_silence_s, band=self.bands.get(u.preset),
                          normaliser=self.norm, seed=seed, words=tr.get("words"))

    def _transcribe(self, files: list[Path], ids: list[str]) -> list[dict]:
        # ``ids`` is a hint for test doubles; Whisper itself ignores it.
        return self.transcriber.transcribe([str(f) for f in files], ids=ids)

    def _store(self, u: Unit, res: QAResult) -> None:
        d = res.to_json()
        d["audio"] = audio_signature(self.cache.path(u.cache_key))
        d["preset"] = u.preset
        d["chapter"] = u.chapter
        old = self.results.get(u.cache_key)
        if old and old.get("accepted") and old.get("audio") == d["audio"]:
            d["accepted"] = True
        self.results[u.cache_key] = d

    # -- phase 1 -----------------------------------------------------------
    def check_all(self, progress: bool = True) -> int:
        todo = []
        for u in self.unique_units():
            if not self.cache.has(u.cache_key):
                continue
            r = self.results.get(u.cache_key)
            if r is None or r.get("audio") != audio_signature(self.cache.path(u.cache_key)):
                todo.append(u)
        if not todo:
            return 0
        self.log(f"Checking {len(todo)} units with Whisper...")
        bar = None
        if progress:
            try:
                from tqdm.auto import tqdm

                bar = tqdm(total=len(todo), desc="Checking", unit="unit")
            except Exception:
                bar = None
        for i in range(0, len(todo), TRANSCRIBE_BATCH):
            batch = todo[i: i + TRANSCRIBE_BATCH]
            trs = self._transcribe([self.cache.path(u.cache_key) for u in batch], [u.id for u in batch])
            for u, tr in zip(batch, trs):
                a, sr = self.cache.get(u.cache_key)
                prev = self.results.get(u.cache_key, {})
                res = self._check(u, a, sr, tr, prev.get("seed", u.seed))
                res.attempts = prev.get("attempts", 1)
                self._store(u, res)
            if bar is not None:
                bar.update(len(batch))
            if (i // TRANSCRIBE_BATCH) % 10 == 9:
                save_results(self.paths, self.results)
        if bar is not None:
            bar.close()
        save_results(self.paths, self.results)
        return len(todo)

    # -- phase 2 -----------------------------------------------------------
    def _engine(self, preset_name: str):
        if preset_name not in self.engines:
            from ..synth import open_engine

            preset = get_preset(preset_name, self.paths.lektor_home)
            self.log(f"Loading {preset.engine} for retries ({preset_name})...")
            self.engines[preset_name] = (self.engine_factory(preset) if self.engine_factory
                                         else open_engine(preset, self.paths.lektor_home, self.log))
        return self.engines[preset_name]

    def close(self) -> None:
        for e in self.engines.values():
            try:
                e.unload()
            except Exception:
                pass
        self.engines = {}

    def _synth(self, u: Unit, text: str, seed: int) -> tuple[np.ndarray, int]:
        from ..synth import synth_params

        preset = get_preset(u.preset, self.paths.lektor_home)
        eng = self._engine(u.preset)
        ref = preset.reference_path(self.paths.lektor_home)
        a = eng.synthesize(text, preset.voice, synth_params(u.params), seed, str(ref) if ref else None)
        return np.asarray(a, dtype=np.float32).reshape(-1), eng.sample_rate

    def _try(self, u: Unit, audio: np.ndarray, sr: int, seed: int) -> QAResult:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "try.flac"
            sf.write(str(f), np.clip(audio, -1, 1), sr, format="FLAC", subtype="PCM_24")
            tr = self._transcribe([f], [u.id])[0]
        return self._check(u, audio, sr, tr, seed)

    def retry(self, u: Unit, attempts: int, split: bool, keep_original: bool = True) -> QAResult:
        """Make a unit again with new seeds; keep the best attempt in the cache."""
        current = _result(self.results[u.cache_key]) if u.cache_key in self.results else None
        best: Optional[tuple[QAResult, np.ndarray, int]] = None
        if keep_original and current is not None:
            a, sr = self.cache.get(u.cache_key)
            best = (current, a, sr)
        n_done = current.attempts if current else 1
        tries = 0
        for k in range(1, attempts + 1):
            tries += 1
            seed = stable_int(u.seed, "retry", n_done + k)
            try:
                audio, sr = self._synth(u, u.spoken, seed)
            except Exception as e:  # noqa: BLE001
                self.log(f"  {u.id}: attempt {k} failed ({getattr(e, 'message', e)})")
                continue
            res = self._try(u, audio, sr, seed)
            res.attempts = n_done + k
            if best is None or res.score < best[0].score:
                best = (res, audio, sr)
            if res.passed:
                break
        if split and best is not None and not best[0].passed and len(u.sentences) > 1:
            pieces, sr_out = [], None
            seed = stable_int(u.seed, "split")
            gap = None
            from ..assemble import trim_silence

            tries += 1
            try:
                for s in u.sentences:
                    audio, sr = self._synth(u, s.spoken, stable_int(seed, s.sid, s.part))
                    sr_out = sr
                    if gap is None:
                        gap = np.zeros(int(self.cfg.pauses.sentence * sr), dtype=np.float32)
                    pieces += [trim_silence(audio, sr), gap]
                joined = np.concatenate(pieces[:-1])
                res = self._try(u, joined, sr_out, seed)
                res.split = True
                res.attempts = best[0].attempts + 1
                if res.score < best[0].score:
                    best = (res, joined, sr_out)
            except Exception as e:  # noqa: BLE001
                self.log(f"  {u.id}: sentence-by-sentence attempt failed ({getattr(e, 'message', e)})")
        if best is None:
            raise LektorError(f"Unit {u.id} could not be made again.")
        res, audio, sr = best
        # Record every try, even when the original stays best, so a stubborn
        # unit is not retried again on every run.
        res.attempts = n_done + tries
        if current is None or res is not current:
            self.cache.put(u.cache_key, audio, sr)
        self._store(u, res)
        return res

    def retry_failures(self) -> tuple[int, int]:
        failing = [u for u in self.unique_units() if u.cache_key in self.results
                   and not self.results[u.cache_key]["passed"] and not self.results[u.cache_key].get("accepted")
                   and self.results[u.cache_key].get("attempts", 1) <= self.cfg.qa.retries]
        if not failing:
            return 0, 0
        self.log(f"Making {len(failing)} flagged units again (up to {self.cfg.qa.retries} tries each)...")
        fixed = 0
        failing.sort(key=lambda u: u.preset)
        for u in failing:
            res = self.retry(u, self.cfg.qa.retries, self.cfg.qa.split_on_failure)
            fixed += int(res.passed)
            self.log(f"  {u.id}: {'fixed' if res.passed else 'still flagged: ' + '; '.join(res.flags)}")
            save_results(self.paths, self.results)
            self.cache.sync()
        self.cache.sync(force=True)
        return len(failing), fixed

    def summary(self) -> QASummary:
        s = QASummary(total_units=len(self.unique_units()))
        for u in self.unique_units():
            r = self.results.get(u.cache_key)
            if not r:
                continue
            s.checked += 1
            if r["passed"] and r.get("attempts", 1) == 1:
                s.passed_first_time += 1
            elif r["passed"]:
                s.fixed_by_retry += 1
            elif r.get("accepted"):
                s.accepted += 1
            else:
                s.still_flagged += 1
                s.flagged_ids.append(u.id)
        return s


def _default_transcriber(cfg: BookConfig, log):
    from .whisper import WhisperTranscriber

    return WhisperTranscriber(cfg.qa.whisper_model, log=log)


def run_qa(paths: BookPaths, cfg: BookConfig, units: Optional[list[Unit]] = None, cache: Optional[UnitCache] = None,
           transcriber=None, engine_factory=None, retry: bool = True, progress: bool = True,
           log: Callable[[str], None] = print) -> QASummary:
    t0 = time.monotonic()
    cache = cache or UnitCache.for_book(paths)
    cache.restore()
    own = transcriber is None
    tr = transcriber or _default_transcriber(cfg, log)
    if own:
        tr.__enter__()
    runner = QARunner(paths, cfg, units, cache, tr, engine_factory, log)
    try:
        missing = [u.id for u in runner.unique_units() if not cache.has(u.cache_key)]
        if missing:
            log(f"Note: {len(missing)} units have no audio yet and were not checked (run section 5 first).")
        runner.learn_bands()
        runner.check_all(progress)
        if retry:
            runner.retry_failures()
    finally:
        runner.close()
        if own:
            tr.__exit__(None, None, None)
    save_results(paths, runner.results)
    s = runner.summary()
    s.seconds = time.monotonic() - t0
    from .report import write_report

    write_report(paths, cfg, runner.unique_units(), runner.results, cache, s)
    append_log(paths, "qa", f"checked {s.checked}/{s.total_units}; first time {s.passed_first_time}, fixed "
                            f"{s.fixed_by_retry}, accepted {s.accepted}, still flagged {s.still_flagged}")
    return s


def accept_all(paths: BookPaths) -> int:
    results = load_results(paths)
    n = 0
    for r in results.values():
        if not r["passed"] and not r.get("accepted"):
            r["accepted"] = True
            n += 1
    save_results(paths, results)
    append_log(paths, "qa", f"librarian accepted {n} remaining flags")
    return n


def regenerate(paths: BookPaths, cfg: BookConfig, unit_ids: Iterable[str], transcriber=None, engine_factory=None,
               log: Callable[[str], None] = print) -> list[QAResult]:
    """Make particular units again with new seeds (the librarian heard a problem)."""
    wanted = {i.strip() for i in unit_ids if i.strip()}
    units = read_units(paths.units)
    chosen = [u for u in units if u.id in wanted]
    unknown = wanted - {u.id for u in chosen}
    if unknown:
        raise LektorError(f"Unknown unit id(s): {', '.join(sorted(unknown))}.",
                          hint="Copy the ids from the QA report, for example c003-p0012-u01.")
    cache = UnitCache.for_book(paths)
    cache.restore()
    own = transcriber is None
    tr = transcriber or _default_transcriber(cfg, log)
    if own:
        tr.__enter__()
    runner = QARunner(paths, cfg, units, cache, tr, engine_factory, log)
    out = []
    try:
        runner.learn_bands()
        for u in chosen:
            if u.cache_key in runner.results:
                runner.results[u.cache_key]["accepted"] = False
            out.append(runner.retry(u, max(1, cfg.qa.retries), cfg.qa.split_on_failure, keep_original=False))
        cache.sync(force=True)
    finally:
        runner.close()
        if own:
            tr.__exit__(None, None, None)
    save_results(paths, runner.results)
    append_log(paths, "qa", f"regenerated {', '.join(sorted(wanted))}")
    return out


def spot_check(paths: BookPaths, n: int = 10, seed: Optional[int] = None) -> list[tuple[Unit, Path]]:
    """A random sample of units for a person to listen to."""
    units = read_units(paths.units)
    cache = UnitCache.for_book(paths)
    have = [u for u in units if cache.has(u.cache_key) and u.role == "main"]
    rnd = random.Random(seed if seed is not None else time.time_ns())
    picked = sorted(rnd.sample(have, min(n, len(have))), key=lambda u: u.id)
    return [(u, cache.path(u.cache_key)) for u in picked]
