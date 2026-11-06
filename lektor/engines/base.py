"""The speech engine interface.

An engine turns text into mono float32 audio. Engines are plain classes;
the registry (:mod:`lektor.engines.registry`) maps names to classes without
importing their heavy dependencies, so the main process can read an
engine's metadata (``max_chars``, ``sample_rate`` ...) without loading it.

Real engines run inside their own environment through a worker process
(:mod:`lektor.engines.worker`); :class:`lektor.engines.client.WorkerEngine`
gives them the same interface in the main process. The mock engine runs
in-process.

To add an engine, see ``docs/adding-an-engine.md``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Optional

import numpy as np


class Engine:
    #: registry name, for example "kokoro"
    name: ClassVar[str] = "base"
    #: licence of the model weights and code, recorded in docs/engines.md
    licence: ClassVar[str] = ""
    #: native output sample rate in Hz
    sample_rate: ClassVar[int] = 24000
    #: longest text (in characters of spoken text) sent in one call
    max_chars: ClassVar[int] = 300
    #: whether the engine is stable enough for whole-paragraph units, and
    #: the longest paragraph it may be given in one go
    paragraph_units: ClassVar[bool] = False
    max_paragraph_chars: ClassVar[int] = 600
    #: whether a reference clip can condition the voice
    supports_reference_audio: ClassVar[bool] = False
    #: name of the isolated environment (lektor.envs), or None to run in-process
    env: ClassVar[Optional[str]] = None
    #: whether the engine needs a GPU (False means it works on CPU too)
    needs_gpu: ClassVar[bool] = False
    #: expressive parameters and their defaults
    default_params: ClassVar[dict[str, Any]] = {}

    def __init__(self, **options: Any) -> None:
        self.options = options
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        raise NotImplementedError

    def synthesize_batch(self, items: list[dict]) -> list[np.ndarray]:
        """Synthesise several units. Engines that batch on the GPU override this."""
        return [self.synthesize(i["text"], i["voice"], i.get("params", {}), i.get("seed", 0), i.get("reference"))
                for i in items]

    def unload(self) -> None:
        self.loaded = False

    # Helpers -------------------------------------------------------------
    @classmethod
    def unit_limit(cls) -> int:
        return cls.max_paragraph_chars if cls.paragraph_units else cls.max_chars

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "licence": cls.licence, "sample_rate": cls.sample_rate,
                "max_chars": cls.max_chars, "paragraph_units": cls.paragraph_units,
                "supports_reference_audio": cls.supports_reference_audio, "env": cls.env,
                "needs_gpu": cls.needs_gpu}


def as_mono_float32(audio) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 2:
        a = a.mean(axis=0 if a.shape[0] < a.shape[1] else 1)
    a = np.nan_to_num(a.reshape(-1), nan=0.0, posinf=0.0, neginf=0.0)
    return a
