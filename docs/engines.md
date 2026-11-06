# Engines and components

Every model and library Lektor runs, with its licence. Only permissive
licences (Apache 2.0, MIT, BSD or similar) are allowed. Licences were
checked on the model cards and PyPI in November 2026.

## Speech engines

| Engine | Model | Licence | Status |
|---|---|---|---|
| mock | none (test tones) | MIT (part of Lektor) | milestone 2 |
| Kokoro | [hexgrad/Kokoro-82M](https://hf.co/hexgrad/Kokoro-82M) | Apache 2.0 | milestone 3 |
| Chatterbox | [ResembleAI/chatterbox](https://hf.co/ResembleAI/chatterbox) | MIT | milestone 3 |
| Chatterbox Turbo | [ResembleAI/chatterbox-turbo](https://hf.co/ResembleAI/chatterbox-turbo) | MIT | milestone 3 |
| Qwen3-TTS CustomVoice | [Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice](https://hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) | Apache 2.0 | milestone 3 |
| Qwen3-TTS VoiceDesign | [Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) | Apache 2.0 | milestone 3 |
| Qwen3-TTS Base | [Qwen/Qwen3-TTS-12Hz-1.7B-Base](https://hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [0.6B-Base](https://hf.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) | Apache 2.0 | milestone 3 |

Strengths, memory use and speed for each engine will be added in milestone 3.

## Other models

| Purpose | Model | Licence |
|---|---|---|
| Quality checks (speech recognition) | [Systran/faster-whisper](https://hf.co/Systran/faster-whisper-small.en) (Whisper weights) | MIT |
| PDF layout analysis | [docling-project/docling-layout-heron](https://hf.co/docling-project/docling-layout-heron) | Apache 2.0 |

## Python packages

| Package | Licence | Used for |
|---|---|---|
| docling | MIT | PDF, DOCX, PPTX, HTML conversion (own environment) |
| pdfplumber, pdfminer.six | MIT | fallback PDF reader |
| pypdf | BSD-3-Clause | PDF page labels |
| pypdfium2 | Apache 2.0 / BSD-3-Clause | PDF rendering (covers) |
| beautifulsoup4, lxml | MIT, BSD-3-Clause | EPUB and HTML |
| python-docx | MIT | fallback DOCX reader |
| pyyaml, pydantic | MIT | settings |
| pysbd | MIT | sentence splitting |
| num2words | LGPL-2.1 (used unmodified as a library) | numbers to words |
| numpy, soundfile, pyloudnorm | BSD, BSD-3-Clause, MIT | audio |
| jiwer | Apache 2.0 | word error rates |
| uv | Apache 2.0 / MIT | engine environments |
| ffmpeg (preinstalled in Colab, run as a separate program) | LGPL/GPL | encoding MP3 and AAC |

Not used, deliberately: `ebooklib` (AGPL; Lektor reads EPUB itself) and
`mutagen` (GPL; tags are written with ffmpeg).

## Why each engine runs in its own environment

The speech packages pin incompatible versions of the same libraries. For
example `chatterbox-tts` 0.1.7 pins `torch==2.6.0`, `numpy<2` and
`transformers==5.2.0`, while `qwen-tts` 0.1.1 pins `transformers==4.57.3`.
Each engine therefore gets its own virtual environment under
`/content/lektor_envs/`, created by `uv` the first time it is needed (see
`lektor/envs.py`). The main notebook process never imports them.
