"""MockEngine: deterministic fake speech for tests.

Returns a soft tone whose length depends on the text length (about 15
characters a second, like a real voice), with gentle "syllable" amplitude
modulation and short gaps at commas, so that trimming, pauses, loudness,
timing, packaging and QA statistics can all be tested without any model.
The same text, voice and seed always give exactly the same samples.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import numpy as np

from .base import Engine

VOICE_PITCH = {"low": 140.0, "mid": 190.0, "high": 240.0, "announcer": 170.0}


class MockEngine(Engine):
    name = "mock"
    licence = "MIT (part of Lektor)"
    sample_rate = 24000
    max_chars = 280
    paragraph_units = False
    supports_reference_audio = True
    env = None
    default_params = {"rate": 15.0, "silence": False}

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        p = {**self.default_params, **(params or {})}
        # Test hooks for the worker protocol tests.
        if p.get("chatty"):
            print("some library printing to stdout")
        if p.get("fail"):
            raise RuntimeError("mock failure requested")
        if p.get("crash"):
            import os

            os._exit(3)
        sr = self.sample_rate
        rate = float(p.get("rate", 15.0))
        dur = max(0.25, len(text) / rate)
        n = int(dur * sr)
        t = np.arange(n, dtype=np.float64) / sr
        h = int(hashlib.sha256(f"{text}|{voice}|{seed}".encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(h)
        if p.get("silence"):
            audio = np.zeros(n)
        else:
            f0 = VOICE_PITCH.get(voice, 180.0) * (1 + 0.02 * rng.standard_normal())
            audio = 0.3 * np.sin(2 * np.pi * f0 * t) + 0.1 * np.sin(2 * np.pi * 2 * f0 * t)
            syll = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t + rng.uniform(0, np.pi)) ** 2
            audio *= syll
            # short dips at commas, proportional to their position in the text
            for k, ch in enumerate(text):
                if ch in ",;:":
                    c = int(k / max(1, len(text)) * n)
                    audio[c: c + int(0.12 * sr)] *= 0.02
        # 60 ms of quiet lead-in and tail, like a real engine, so trimming is exercised
        pad = np.zeros(int(0.06 * sr))
        out = np.concatenate([pad, audio, pad]).astype(np.float32)
        fade = min(len(out) // 4, int(0.01 * sr))
        if fade:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            out[:fade] *= ramp
            out[-fade:] *= ramp[::-1]
        return out
