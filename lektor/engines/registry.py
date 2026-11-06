"""Engine registry and voice presets.

Engines are registered by dotted path so that importing the registry never
imports an engine's heavy dependencies. Presets come from
``lektor/data/voices.yaml`` and, if present, ``MyDrive/Lektor/voices.yaml``
(the second may add presets or override shipped ones).
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from ..errors import ConfigError
from .base import Engine

ENGINES: dict[str, str] = {
    "mock": "lektor.engines.mock:MockEngine",
}


def register_engine(name: str, path: str) -> None:
    ENGINES[name] = path


def engine_class(name: str) -> type[Engine]:
    if name not in ENGINES:
        raise ConfigError(f"There is no speech engine called '{name}'.",
                          hint="Known engines: " + ", ".join(sorted(ENGINES)))
    mod, _, cls = ENGINES[name].partition(":")
    return getattr(importlib.import_module(mod), cls)


@dataclass
class Preset:
    name: str
    engine: str
    voice: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    # Seeded per-paragraph variation: parameter -> +/- amount
    vary: dict[str, float] = field(default_factory=dict)
    # Reference clip (for engines that clone a voice), relative to the Lektor home
    reference: Optional[str] = None
    # How a reference clip is made, for designed voices (see Qwen3-TTS VoiceDesign)
    design: Optional[dict[str, Any]] = None
    description: str = ""
    language: str = "en-GB"

    def fingerprint(self, home: Optional[Path] = None) -> str:
        h = hashlib.sha256(repr((self.engine, self.voice, sorted(self.params.items()),
                                 sorted(self.vary.items()), self.language)).encode())
        ref = self.reference_path(home)
        if ref is not None and ref.exists():
            h.update(hashlib.sha256(ref.read_bytes()).digest())
        elif self.reference:
            h.update(self.reference.encode())
        return h.hexdigest()[:16]

    def reference_path(self, home: Optional[Path] = None) -> Optional[Path]:
        if not self.reference:
            return None
        p = Path(self.reference)
        if p.is_absolute() or home is None:
            return p
        return Path(home) / p


BUILTIN_VOICES = Path(__file__).resolve().parent.parent / "data" / "voices.yaml"


def _read_presets(path: Path) -> dict[str, Preset]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML.", hint="Check indentation and colons.") from e
    out = {}
    for name, d in (data.get("presets") or {}).items():
        if not isinstance(d, dict) or "engine" not in d:
            raise ConfigError(f"The voice preset '{name}' in {path.name} has no engine.")
        out[name] = Preset(name=name, **{k: v for k, v in d.items() if k in Preset.__dataclass_fields__ and k != "name"})
    return out


def load_presets(home: Optional[Path] = None) -> dict[str, Preset]:
    presets = _read_presets(BUILTIN_VOICES)
    if home is not None and (Path(home) / "voices.yaml").exists():
        presets.update(_read_presets(Path(home) / "voices.yaml"))
    return presets


def get_preset(name: str, home: Optional[Path] = None) -> Preset:
    presets = load_presets(home)
    if name not in presets:
        raise ConfigError(f"There is no voice preset called '{name}'.",
                          hint="Choose one of: " + ", ".join(sorted(presets)))
    return presets[name]
