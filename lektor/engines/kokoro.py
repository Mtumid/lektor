"""Kokoro-82M (Apache 2.0): small, fast, stable. Works on CPU too.

Voices: British English ``bf_emma``, ``bf_isabella``, ``bf_alice``,
``bf_lily``, ``bm_george``, ``bm_lewis``, ``bm_fable``, ``bm_daniel``, and the
American ``af_*`` / ``am_*`` voices. The first letter of the voice picks the
G2P (``b`` British, ``a`` American).

Kokoro sounds best on 100 to 200 phoneme tokens and can rush very long
inputs (see the model's VOICES.md), so units are kept to about 300
characters; whole paragraphs up to 500 characters are allowed.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .base import Engine

REPO = "hexgrad/Kokoro-82M"


class KokoroEngine(Engine):
    name = "kokoro"
    licence = "Apache-2.0 (weights and code)"
    sample_rate = 24000
    max_chars = 300
    paragraph_units = True
    max_paragraph_chars = 500
    supports_reference_audio = False
    env = "kokoro"
    needs_gpu = False
    default_params = {"speed": 1.0}

    def load(self) -> None:
        import torch
        from kokoro import KModel, KPipeline

        self.device = self.options.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = KModel(repo_id=REPO).to(self.device).eval()
        self.pipelines: dict[str, Any] = {}
        self._KPipeline = KPipeline
        self.loaded = True

    def _pipeline(self, voice: str):
        lang = voice[0] if voice and voice[0] in "abefhijpz" else "b"
        if lang not in self.pipelines:
            self.pipelines[lang] = self._KPipeline(lang_code=lang, repo_id=REPO, model=False)
        return self.pipelines[lang]

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        import torch

        torch.manual_seed(seed)
        pipe = self._pipeline(voice)
        speed = float((params or {}).get("speed", 1.0))
        pieces = []
        with torch.inference_mode():
            # split_pattern=None: Lektor has already chunked the text.
            for result in pipe(text, voice=voice, speed=speed, split_pattern=None, model=self.model):
                if result.audio is not None:
                    pieces.append(result.audio.detach().cpu().numpy())
        if not pieces:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(pieces).astype(np.float32)

    def runtime_info(self) -> dict:
        import torch

        return {"device": self.device, "torch": torch.__version__}

    def unload(self) -> None:
        self.model = None
        self.pipelines = {}
        self.loaded = False
