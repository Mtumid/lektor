"""Designed voices: "design once, then clone".

A design (``designs:`` in voices.yaml) describes a voice in words. The first
time a preset needs it, Qwen3-TTS VoiceDesign reads the design's sample text
in that voice, and the clip is saved to Drive::

    MyDrive/Lektor/voices/designed/<name>.wav    the reference clip
    MyDrive/Lektor/voices/designed/<name>.txt    what it says (for cloning)
    MyDrive/Lektor/voices/designed/<name>.json   provenance: model, description, seed, date

From then on every unit is made by a cloning engine (Qwen3-TTS Base, or
Chatterbox) conditioned on that one clip, so the voice stays the same across
a ten hour book. The clip is entirely synthetic: no real person's voice is
involved, which keeps to the project's no-cloning rule.

To redesign a voice, delete its three files (or change the design's seed).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import soundfile as sf

from .errors import LektorError

MIN_SECONDS = 8.0  # Chatterbox needs more than 5 s; give some margin


def designed_dir(home: Optional[Path]) -> Path:
    return (Path(home) if home else Path(".")) / "voices" / "designed"


def design_clip_path(name: str, home: Optional[Path]) -> Path:
    return designed_dir(home) / f"{name}.wav"


def designs_needed(preset_names: Iterable[str], home: Path) -> list[str]:
    from .engines.registry import get_preset

    out = []
    for n in preset_names:
        p = get_preset(n, home)
        if p.design and not p.reference and not design_clip_path(p.design, home).exists() and p.design not in out:
            out.append(p.design)
    return out


def make_designs(names: list[str], home: Path, log: Callable[[str], None] = print,
                 engine_factory: Optional[Callable] = None, attempts: int = 3) -> list[Path]:
    """Make the reference clip for each design that does not exist yet."""
    from .engines.registry import engine_class, load_designs

    if not names:
        return []
    designs = load_designs(home)
    unknown = [n for n in names if n not in designs]
    if unknown:
        raise LektorError(f"Unknown designed voice(s): {', '.join(unknown)}.",
                          hint="Check the 'design' of your presets in voices.yaml.")
    if engine_factory is not None:
        engine = engine_factory()
    else:
        from .engines.client import WorkerEngine

        engine = WorkerEngine(engine_class("qwen3-design"), log=log)
    log("Designing voice(s): " + ", ".join(names) + " (once only; the clips are saved to Drive)...")
    engine.load()
    made = []
    try:
        for name in names:
            d = designs[name]
            out = design_clip_path(name, home)
            out.parent.mkdir(parents=True, exist_ok=True)
            audio = None
            for k in range(attempts):
                a = np.asarray(engine.synthesize(d.text, d.instruct, {"instruct": d.instruct, "language": d.language},
                                                 d.seed + k), dtype=np.float32)
                secs = a.size / engine.sample_rate
                if np.all(np.isfinite(a)) and secs >= MIN_SECONDS and float(np.max(np.abs(a))) > 0.01:
                    audio = a
                    seed_used = d.seed + k
                    break
                log(f"  attempt {k + 1} for '{name}' gave {secs:.1f} s of audio; trying again")
            if audio is None:
                raise LektorError(f"Could not design the voice '{name}'.",
                                  hint="Run the cell again. If it keeps failing, choose a Kokoro voice instead.")
            peak = float(np.max(np.abs(audio)))
            audio = audio / peak * 0.89
            sf.write(str(out), audio, engine.sample_rate, subtype="PCM_16")
            out.with_suffix(".txt").write_text(d.text.strip() + "\n", encoding="utf-8")
            out.with_suffix(".json").write_text(json.dumps({
                "design": name, "description": d.description, "instruct": d.instruct, "text": d.text,
                "seed": seed_used, "model": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", "licence": "Apache-2.0",
                "synthetic": True, "real_person": False, "created": _dt.datetime.now().isoformat(timespec="seconds"),
                "seconds": round(audio.size / engine.sample_rate, 2),
            }, indent=1), encoding="utf-8")
            log(f"  saved {out.name} ({audio.size / engine.sample_rate:.1f} s)")
            made.append(out)
    finally:
        engine.unload()
    return made


def ensure_designs(preset_names: Iterable[str], home: Path, log: Callable[[str], None] = print,
                   engine_factory: Optional[Callable] = None) -> list[Path]:
    return make_designs(designs_needed(preset_names, home), home, log, engine_factory)
