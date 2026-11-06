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

from ..envs import EnvSpec, register_env
from ..errors import ConfigError
from .base import Engine

ENGINES: dict[str, str] = {
    "mock": "lektor.engines.mock:MockEngine",
    "kokoro": "lektor.engines.kokoro:KokoroEngine",
    "chatterbox": "lektor.engines.chatterbox:ChatterboxEngine",
    "chatterbox-turbo": "lektor.engines.chatterbox:ChatterboxTurboEngine",
    "qwen3-custom": "lektor.engines.qwen3:QwenCustomVoiceEngine",
    "qwen3-design": "lektor.engines.qwen3:QwenVoiceDesignEngine",
    "qwen3-base": "lektor.engines.qwen3:QwenBaseCloneEngine",
    "whisper": "lektor.qa.whisper:WhisperEngine",  # speech recognition for quality checks
}

# Isolated environments for the engines (see lektor/envs.py). Versions were
# checked on PyPI in November 2026; see docs/engines.md.
register_env(EnvSpec(
    name="kokoro",
    packages=("kokoro==0.9.4", "misaki[en]==0.9.4", "torch==2.11.*", "soundfile", "pip"),
    python="3.12",
    apt=("espeak-ng",),
    check="import kokoro, misaki",
    notes="pip is included because misaki downloads its spaCy English model with pip on first use.",
))
register_env(EnvSpec(
    name="chatterbox",
    packages=("chatterbox-tts==0.1.7",),
    python="3.11",
    check="import chatterbox.tts, chatterbox.tts_turbo",
    notes="chatterbox-tts pins torch 2.6.0, numpy<2 and transformers 5.2.0; Resemble AI test on Python 3.11.",
))
register_env(EnvSpec(
    name="whisper",
    # CTranslate2 4.x needs CUDA 12 cuBLAS and cuDNN 9; they come as pip
    # packages and are put on LD_LIBRARY_PATH by lektor.envs.env_vars.
    packages=("faster-whisper==1.2.1", "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"),
    python="3.12",
    check="import faster_whisper, ctranslate2",
))
register_env(EnvSpec(
    name="qwen3",
    # torchaudio stops at 2.11, and its compiled code must match torch's
    # version, so both are pinned (2.11 is also what Colab itself runs).
    packages=("qwen-tts==0.1.1", "torch==2.11.*", "torchaudio==2.11.*"),
    python="3.12",
    apt=("sox",),
    check="import qwen_tts",
    notes="qwen-tts pins transformers 4.57.3. No flash-attn: the T4 cannot use FlashAttention 2.",
))


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
    # Name of a designed voice (``designs:`` in voices.yaml). Its synthetic
    # reference clip is made once by Qwen3-TTS VoiceDesign and used as
    # ``reference`` (see lektor/voice_design.py).
    design: Optional[str] = None
    description: str = ""
    language: str = "en-GB"

    def fingerprint(self, home: Optional[Path] = None) -> str:
        h = hashlib.sha256(repr((self.engine, self.voice, sorted(self.params.items()),
                                 sorted(self.vary.items()), self.language)).encode())
        if self.design and not self.reference:
            # A designed voice is identified by its definition, so planning
            # gives the same cache keys before and after the clip is made.
            d = load_designs(home).get(self.design)
            h.update(repr(d).encode() if d else self.design.encode())
        else:
            ref = self.reference_path(home)
            if ref is not None and ref.exists():
                h.update(hashlib.sha256(ref.read_bytes()).digest())
            elif self.reference:
                h.update(self.reference.encode())
        return h.hexdigest()[:16]

    def reference_path(self, home: Optional[Path] = None) -> Optional[Path]:
        if self.design and not self.reference:
            from ..voice_design import design_clip_path

            return design_clip_path(self.design, home)
        if not self.reference:
            return None
        p = Path(self.reference)
        if p.is_absolute() or home is None:
            return p
        return Path(home) / p


BUILTIN_VOICES = Path(__file__).resolve().parent.parent / "data" / "voices.yaml"


def _read_presets(path: Path) -> dict[str, Preset]:
    import yaml  # imported here so engine workers do not need PyYAML

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


@dataclass
class Design:
    """A voice made from a description by Qwen3-TTS VoiceDesign."""

    name: str
    instruct: str
    text: str
    language: str = "English"
    seed: int = 1
    description: str = ""


def _read_designs(path: Path) -> dict[str, Design]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for name, d in (data.get("designs") or {}).items():
        out[name] = Design(name=name, **{k: v for k, v in d.items() if k in Design.__dataclass_fields__ and k != "name"})
    return out


def load_designs(home: Optional[Path] = None) -> dict[str, Design]:
    designs = _read_designs(BUILTIN_VOICES)
    if home is not None and (Path(home) / "voices.yaml").exists():
        designs.update(_read_designs(Path(home) / "voices.yaml"))
    return designs
