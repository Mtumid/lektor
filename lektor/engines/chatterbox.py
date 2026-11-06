"""Chatterbox and Chatterbox Turbo by Resemble AI (MIT).

Both models ship a built-in default voice (``conds.pt``), so they work
without a reference clip. For British voices Lektor conditions them on a
**synthetic** reference clip designed with Qwen3-TTS VoiceDesign (no real
person involved; see ``lektor/voice_design.py``). Reference clips must be
longer than 5 seconds.

Parameters (original model): ``exaggeration`` (default 0.5), ``cfg_weight``
(0.5), ``temperature`` (0.8). Turbo ignores exaggeration and cfg_weight.

Every clip carries Resemble AI's imperceptible Perth watermark, which marks
it as synthetic.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .base import Engine


class _ChatterboxBase(Engine):
    licence = "MIT (weights and code)"
    sample_rate = 24000
    supports_reference_audio = True
    env = "chatterbox"
    needs_gpu = True
    turbo = False

    def load(self) -> None:
        import torch

        self.device = self.options.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.turbo:
            from chatterbox.tts_turbo import ChatterboxTurboTTS as M
        else:
            from chatterbox.tts import ChatterboxTTS as M
        self.model = M.from_pretrained(device=self.device)
        self.sample_rate = int(self.model.sr)
        self._default_conds = self.model.conds
        self._ref: Optional[str] = None
        self._ref_exag: Optional[float] = None
        self.loaded = True

    def _condition(self, reference: Optional[str], exaggeration: float) -> None:
        """Prepare conditioning once per reference clip (not once per call)."""
        if reference:
            if reference != self._ref or (not self.turbo and exaggeration != self._ref_exag):
                self.model.prepare_conditionals(reference, exaggeration=exaggeration)
                self._ref, self._ref_exag = reference, exaggeration
        elif self._ref is not None:
            self.model.conds = self._default_conds
            self._ref = None

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        import torch

        p = {**self.default_params, **(params or {})}
        exaggeration = float(p.get("exaggeration", 0.5))
        self._condition(reference, exaggeration)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        kwargs = {"temperature": float(p.get("temperature", 0.8))}
        if self.turbo:
            for k in ("top_p", "top_k", "repetition_penalty"):
                if k in p:
                    kwargs[k] = p[k]
        else:
            kwargs.update(exaggeration=exaggeration, cfg_weight=float(p.get("cfg_weight", 0.5)))
            for k in ("min_p", "top_p", "repetition_penalty"):
                if k in p:
                    kwargs[k] = p[k]
        with torch.inference_mode():
            wav = self.model.generate(text, **kwargs)
        return wav.squeeze().detach().cpu().numpy().astype(np.float32)

    def rpc_embed(self, path: str) -> list[float]:
        """Speaker embedding of an audio file (used to check voice consistency)."""
        import librosa

        wav, _ = librosa.load(path, sr=16000)
        emb = self.model.ve.embeds_from_wavs([wav], sample_rate=16000, as_spk=True)
        return [float(x) for x in np.asarray(emb).reshape(-1)]

    def runtime_info(self) -> dict:
        import torch

        return {"device": self.device, "torch": torch.__version__}

    def unload(self) -> None:
        self.model = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
        self.loaded = False


class ChatterboxEngine(_ChatterboxBase):
    name = "chatterbox"
    max_chars = 300
    paragraph_units = True
    max_paragraph_chars = 450
    default_params = {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8}


class ChatterboxTurboEngine(_ChatterboxBase):
    name = "chatterbox-turbo"
    turbo = True
    max_chars = 300
    paragraph_units = True
    max_paragraph_chars = 450
    default_params = {"temperature": 0.8}
