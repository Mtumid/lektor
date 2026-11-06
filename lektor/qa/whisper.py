"""faster-whisper (MIT) as a worker, for transcribing units.

Runs in its own environment (``whisper``) like the speech engines. Model:
``distil-large-v3`` (English, MIT) on a GPU in int8_float16, which needs
about 1 GB of GPU memory, so it fits on the T4 next to any speech engine;
``small.en`` on CPU. Word timestamps are kept: they are used later to place
sentence boundaries precisely inside multi-sentence units (DAISY sync).
"""

from __future__ import annotations

from typing import Any, Optional

from ..engines.base import Engine


class WhisperEngine(Engine):
    name = "whisper"
    licence = "MIT (faster-whisper code; Whisper and distil-whisper weights)"
    sample_rate = 16000
    env = "whisper"
    needs_gpu = False

    def load(self) -> None:
        import ctranslate2
        from faster_whisper import WhisperModel

        cuda = ctranslate2.get_cuda_device_count() > 0
        model = self.options.get("model") or "auto"
        if model == "auto":
            model = "distil-large-v3" if cuda else "small.en"
        self.device = "cuda" if cuda else "cpu"
        self.compute_type = self.options.get("compute_type") or ("int8_float16" if cuda else "int8")
        self.model_name = model
        self.model = WhisperModel(model, device=self.device, compute_type=self.compute_type)
        self.loaded = True

    def synthesize(self, *a, **k):  # pragma: no cover - not a voice
        raise NotImplementedError("Whisper does not speak.")

    def rpc_transcribe(self, files: list[str], prompts: Optional[list[str]] = None) -> list[dict[str, Any]]:
        out = []
        for i, f in enumerate(files):
            prompt = prompts[i] if prompts and i < len(prompts) else None
            segments, info = self.model.transcribe(
                f, language="en", beam_size=5, word_timestamps=True, vad_filter=False,
                condition_on_previous_text=False, initial_prompt=prompt, without_timestamps=False)
            words, texts, logprob, nospeech = [], [], [], []
            for seg in segments:
                texts.append(seg.text)
                logprob.append(seg.avg_logprob)
                nospeech.append(seg.no_speech_prob)
                for w in seg.words or []:
                    words.append([w.word.strip(), round(w.start, 3), round(w.end, 3), round(w.probability, 3)])
            out.append({"text": " ".join(t.strip() for t in texts).strip(), "words": words,
                        "avg_logprob": min(logprob) if logprob else None,
                        "no_speech_prob": max(nospeech) if nospeech else None})
        return out

    def runtime_info(self) -> dict:
        return {"device": self.device, "compute_type": self.compute_type, "model": self.model_name}


class WhisperTranscriber:
    """Main-process helper: transcribe audio files through the worker."""

    def __init__(self, model: str = "auto", log=print) -> None:
        from ..engines.client import WorkerEngine

        self.worker = WorkerEngine(WhisperEngine, log=log, options={"model": model})

    def __enter__(self) -> "WhisperTranscriber":
        self.worker.load()
        return self

    def __exit__(self, *exc) -> None:
        self.worker.unload()

    def transcribe(self, files: list[str], ids: Optional[list[str]] = None) -> list[dict]:
        # No prompt is given: priming Whisper with the expected text would hide errors.
        return self.worker.call("transcribe", files=files)
