"""Synthesis stage: make audio for every unit that is not cached yet.

Resumable by design: finished units are cached (see :mod:`lektor.cache`),
and on start the cache is restored from Drive, so after a disconnect you run
setup and this stage again and it carries on.

Engines are loaded one at a time (main voice first, then the secondary
voice) to keep GPU memory free, and kept warm while their units are made.
"""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from .cache import UnitCache
from .chunk.plan import Unit, read_units
from .config import BookConfig, BookPaths, append_log
from .engines.base import Engine, as_mono_float32
from .engines.registry import Preset, engine_class, get_preset
from .errors import EngineError, LektorError


@dataclass
class SynthResult:
    total: int
    already: int
    made: int
    failed: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    audio_seconds: float = 0.0


def open_engine(preset: Preset, home: Path, log: Callable[[str], None] = print) -> Engine:
    """Start an engine for a preset: in-process, or as a worker in its own environment."""
    cls = engine_class(preset.engine)
    if cls.env is None:
        eng = cls()
    else:
        from .engines.client import WorkerEngine

        eng = WorkerEngine(cls, log=log)
    eng.load()
    return eng


def check_audio(audio: np.ndarray, sample_rate: int, text: str) -> Optional[str]:
    """Basic sanity checks. Returns a problem description, or None."""
    if audio is None or audio.size == 0:
        return "the engine returned no audio"
    if not np.all(np.isfinite(audio)):
        return "the audio contains invalid values"
    if float(np.max(np.abs(audio))) < 1e-4:
        return "the audio is silent"
    if audio.size / sample_rate < 0.1 and len(text) > 5:
        return "the audio is far too short"
    return None


class Progress:
    """A progress bar weighted by characters, with an estimated time remaining."""

    def __init__(self, total_chars: int, total_units: int, desc: str, enabled: bool = True) -> None:
        self.enabled = enabled
        self.bar = None
        if enabled:
            try:
                from tqdm.auto import tqdm

                self.bar = tqdm(total=total_chars, desc=desc, unit="char", unit_scale=True,
                                bar_format="{desc}: {percentage:3.0f}%|{bar}| {postfix} [{elapsed} elapsed, "
                                           "about {remaining} left]")
            except Exception:
                self.bar = None
        self.units_total = total_units
        self.units_done = 0

    def update(self, chars: int) -> None:
        self.units_done += 1
        if self.bar is not None:
            self.bar.update(chars)
            self.bar.set_postfix_str(f"{self.units_done}/{self.units_total} units", refresh=False)

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


def pending_units(units: list[Unit], cache: UnitCache) -> list[Unit]:
    seen = set()
    out = []
    for u in units:
        if u.cache_key in seen or cache.has(u.cache_key):
            continue
        seen.add(u.cache_key)
        out.append(u)
    return out


def run_synth(paths: BookPaths, cfg: BookConfig, units: Optional[list[Unit]] = None,
              cache: Optional[UnitCache] = None, only: Optional[Iterable[str]] = None,
              log: Callable[[str], None] = print, progress: bool = True, batch: int = 4,
              engine_factory: Optional[Callable[[Preset], Engine]] = None) -> SynthResult:
    t0 = time.monotonic()
    units = units if units is not None else read_units(paths.units)
    cache = cache or UnitCache.for_book(paths)
    restored = cache.restore()
    if restored:
        log(f"Restored {restored} finished units from Drive.")
    if only is not None:
        wanted = set(only)
        for u in units:
            if u.id in wanted:
                cache.delete(u.cache_key)
        units = [u for u in units if u.id in wanted]
    todo = pending_units(units, cache)
    distinct = len({u.cache_key for u in units})
    result = SynthResult(total=distinct, already=distinct - len(todo), made=0)
    if not todo:
        log("All units are already made. Nothing to do.")
        return result
    log(f"{len(todo)} of {distinct} units to make ({result.already} already done).")

    groups: "OrderedDict[str, list[Unit]]" = OrderedDict()
    for u in sorted(todo, key=lambda u: (u.role != "main",)):
        groups.setdefault(u.preset, []).append(u)

    home = paths.lektor_home
    bar = Progress(sum(len(u.spoken) for u in todo), len(todo), "Making audio", progress)
    failures_path = paths.qa_dir / "synth_failures.json"
    try:
        for preset_name, group in groups.items():
            preset = get_preset(preset_name, home)
            log(f"Loading the {preset.engine} engine for '{preset_name}' ({len(group)} units)...")
            engine = engine_factory(preset) if engine_factory else open_engine(preset, home, log)
            ref = preset.reference_path(home)
            try:
                for i in range(0, len(group), max(1, batch)):
                    chunk = group[i: i + batch]
                    items = [{"text": u.spoken, "voice": preset.voice, "params": u.params, "seed": u.seed,
                              "reference": str(ref) if ref else None} for u in chunk]
                    try:
                        audios = engine.synthesize_batch(items)
                    except LektorError:
                        raise
                    except Exception as e:  # one bad batch: try the units one by one
                        audios = []
                        for it in items:
                            try:
                                audios.append(engine.synthesize(it["text"], it["voice"], it["params"], it["seed"],
                                                                it["reference"]))
                            except Exception as e2:  # noqa: BLE001
                                audios.append(e2)
                    for u, a in zip(chunk, audios):
                        if isinstance(a, Exception):
                            result.failed.append({"id": u.id, "problem": f"{type(a).__name__}: {a}"})
                            bar.update(len(u.spoken))
                            continue
                        a = as_mono_float32(a)
                        sr = getattr(engine, "sample_rate", 24000)
                        problem = check_audio(a, sr, u.spoken)
                        if problem:
                            result.failed.append({"id": u.id, "problem": problem})
                        else:
                            cache.put(u.cache_key, a, sr)
                            result.made += 1
                            result.audio_seconds += a.size / sr
                        bar.update(len(u.spoken))
                    cache.sync()
            finally:
                try:
                    engine.unload()
                except Exception:
                    pass
    finally:
        cache.sync(force=True)
        bar.close()
    result.seconds = time.monotonic() - t0
    paths.qa_dir.mkdir(parents=True, exist_ok=True)
    failures_path.write_text(json.dumps(result.failed, indent=1))
    rtf = result.seconds / result.audio_seconds if result.audio_seconds else math.nan
    append_log(paths, "synth", f"made {result.made} units ({result.audio_seconds / 60:.1f} min of audio) in "
                               f"{result.seconds / 60:.1f} min; {len(result.failed)} failed; real-time factor {rtf:.2f}")
    return result
