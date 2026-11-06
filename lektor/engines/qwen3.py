"""Qwen3-TTS by the Qwen team (Apache 2.0).

Three engines share one environment:

- ``qwen3-custom``: CustomVoice, built-in speakers (``Ryan``, ``Aiden`` for
  English) with an optional style instruction (``params.instruct``)
- ``qwen3-design``: VoiceDesign, a voice made from a text description; used
  once per designed voice to make a reference clip (``lektor/voice_design.py``)
- ``qwen3-base``: Base, which clones the voice in a reference clip. Lektor
  only ever gives it **synthetic** clips made by ``qwen3-design``.

"Design once, then clone": the designed reference clip is saved to Drive
and every unit is made by ``qwen3-base`` from that one clip, so the voice
stays the same across a long book (VoiceDesign alone would drift).

The T4 is a Turing GPU: no FlashAttention 2 and no fast bfloat16, so the
models load with SDPA attention in float16 (``params.dtype`` can be set to
``float32`` if float16 ever gives silent or broken audio).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from .base import Engine

MODELS = {
    "custom": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "base": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
}
SAMPLING = ("do_sample", "top_k", "top_p", "temperature", "repetition_penalty", "max_new_tokens",
            "subtalker_dosample", "subtalker_top_k", "subtalker_top_p", "subtalker_temperature")


class _QwenBase(Engine):
    licence = "Apache-2.0 (weights and code)"
    sample_rate = 24000
    env = "qwen3"
    needs_gpu = True
    max_chars = 300
    paragraph_units = True
    max_paragraph_chars = 500
    kind = "custom"
    default_params = {"language": "English"}

    def load(self) -> None:
        import torch
        from qwen_tts import Qwen3TTSModel

        cuda = torch.cuda.is_available()
        self.device = self.options.get("device") or ("cuda:0" if cuda else "cpu")
        want = self.options.get("dtype", "auto")
        if want == "auto":
            if cuda and torch.cuda.get_device_capability(0)[0] >= 8:
                dtype = torch.bfloat16
            elif cuda:
                dtype = torch.float16
            else:
                dtype = torch.float32
        else:
            dtype = getattr(torch, want)
        repo = self.options.get("model") or MODELS[self.kind]
        self.model = Qwen3TTSModel.from_pretrained(repo, device_map=self.device, dtype=dtype,
                                                   attn_implementation="sdpa")
        self.dtype = str(dtype)
        self.repo = repo
        self._prompts: dict[str, Any] = {}
        self.loaded = True

    def _gen_kwargs(self, p: dict) -> dict:
        return {k: p[k] for k in SAMPLING if k in p}

    def _seed(self, seed: int) -> None:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    def _out(self, wavs, sr) -> np.ndarray:
        self.sample_rate = int(sr)
        return np.asarray(wavs[0], dtype=np.float32).reshape(-1)

    def runtime_info(self) -> dict:
        import torch

        return {"device": self.device, "dtype": self.dtype, "model": self.repo, "torch": torch.__version__}

    def unload(self) -> None:
        self.model = None
        self._prompts = {}
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
        self.loaded = False


class QwenCustomVoiceEngine(_QwenBase):
    name = "qwen3-custom"
    kind = "custom"

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        p = {**self.default_params, **(params or {})}
        self._seed(seed)
        wavs, sr = self.model.generate_custom_voice(text=text, speaker=voice, language=p.get("language", "English"),
                                                     instruct=p.get("instruct") or None, **self._gen_kwargs(p))
        return self._out(wavs, sr)


class QwenVoiceDesignEngine(_QwenBase):
    name = "qwen3-design"
    kind = "design"

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        p = {**self.default_params, **(params or {})}
        self._seed(seed)
        wavs, sr = self.model.generate_voice_design(text=text, instruct=p.get("instruct", voice),
                                                     language=p.get("language", "English"), **self._gen_kwargs(p))
        return self._out(wavs, sr)


class QwenBaseCloneEngine(_QwenBase):
    name = "qwen3-base"
    kind = "base"
    supports_reference_audio = True

    def _prompt(self, reference: str):
        if reference not in self._prompts:
            ref_text_file = Path(reference).with_suffix(".txt")
            ref_text = ref_text_file.read_text(encoding="utf-8").strip() if ref_text_file.exists() else None
            self._prompts[reference] = self.model.create_voice_clone_prompt(
                ref_audio=reference, ref_text=ref_text, x_vector_only_mode=ref_text is None)
        return self._prompts[reference]

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        if not reference:
            raise ValueError("Qwen3-TTS Base needs a reference clip (a designed voice).")
        p = {**self.default_params, **(params or {})}
        self._seed(seed)
        wavs, sr = self.model.generate_voice_clone(text=text, language=p.get("language", "English"),
                                                    voice_clone_prompt=self._prompt(reference), **self._gen_kwargs(p))
        return self._out(wavs, sr)
