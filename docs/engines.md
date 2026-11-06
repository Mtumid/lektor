# Engines and components

Every model and library Lektor runs, with its licence. Only permissive
licences (Apache 2.0, MIT, BSD or similar) are allowed. Licences were
checked on the model cards and PyPI in November 2026.

## Speech engines

| Engine (registry name) | Model | Licence | GPU? | Environment |
|---|---|---|---|---|
| `mock` | none (test tones) | MIT (part of Lektor) | no | main process |
| `kokoro` | [hexgrad/Kokoro-82M](https://hf.co/hexgrad/Kokoro-82M) v1.0 | Apache 2.0 | optional | `kokoro` (Python 3.12, `kokoro==0.9.4`, espeak-ng) |
| `chatterbox` | [ResembleAI/chatterbox](https://hf.co/ResembleAI/chatterbox) (original, English) | MIT | yes | `chatterbox` (Python 3.11, `chatterbox-tts==0.1.7`) |
| `chatterbox-turbo` | [ResembleAI/chatterbox-turbo](https://hf.co/ResembleAI/chatterbox-turbo) | MIT | yes | `chatterbox` |
| `qwen3-custom` | [Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice](https://hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) | Apache 2.0 | yes | `qwen3` (Python 3.12, `qwen-tts==0.1.1`) |
| `qwen3-design` | [Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) | Apache 2.0 | yes | `qwen3` |
| `qwen3-base` | [Qwen/Qwen3-TTS-12Hz-1.7B-Base](https://hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) | Apache 2.0 | yes | `qwen3` |

### Kokoro

- **Strengths**: small (82 million parameters), fast, very stable; never
  hallucinates or loops; the only engine that is usable without a GPU.
  British voices: `bf_emma` (best graded British voice), `bf_isabella`,
  `bf_alice`, `bf_lily`, `bm_george`, `bm_lewis`, `bm_fable`, `bm_daniel`.
- **Weaknesses**: less expressive; can sound slightly flat over long
  passages; its own model card notes it rushes very long inputs and is weak
  on very short ones, so Lektor keeps units between about 1 and 500
  characters (paragraph units allowed up to 500).
- **Memory and speed (estimates, to be confirmed by the smoke test)**:
  about 1 GB of GPU memory; roughly 20 to 50 times faster than real time on
  a T4, and 2 to 4 times faster than real time on Colab's CPU. A ten hour
  book takes well under an hour on a GPU.
- Uses espeak-ng as a fallback for unknown words (installed automatically).

### Chatterbox and Chatterbox Turbo

- **Strengths**: very natural, expressive delivery; Chatterbox (original)
  has `exaggeration` and `cfg_weight` controls. Turbo is faster and
  lighter.
- **Voices**: both models ship a built-in default voice (`conds.pt`), so
  `chatterbox-default` works without a reference. (The brief expected only
  Turbo to have one; the model files show both do.) British presets are
  conditioned on a **designed** clip (see below). Reference clips must be
  longer than 5 seconds; designed clips are about 20 seconds.
- **Parameters** (original): `exaggeration` 0.5, `cfg_weight` 0.5,
  `temperature` 0.8; Lektor varies `exaggeration` by ±0.1 per paragraph,
  seeded so it is reproducible. Turbo ignores `exaggeration` and
  `cfg_weight`.
- **Watermark**: every clip carries Resemble AI's imperceptible Perth
  watermark, which identifies it as synthetic.
- **Memory and speed (estimates)**: original about 4 to 6 GB, Turbo about 3
  to 4 GB of GPU memory; original roughly real time on a T4, Turbo two to
  four times faster. A ten hour book with the original model needs several
  Colab sessions; resumability handles this.
- Pins `torch==2.6.0`, `numpy<2`, `transformers==5.2.0`: isolated in its own
  environment.

### Qwen3-TTS

- **CustomVoice** (`qwen3-custom`): nine built-in speakers; the English
  ones are `Ryan` and `Aiden` (American). No British speaker. A style
  instruction (`params.instruct`) asks for narration-style reading.
- **VoiceDesign** (`qwen3-design`): makes a voice from a written
  description. Used **once per designed voice** to make a reference clip.
- **Base** (`qwen3-base`): clones the voice of a reference clip. Lektor
  only ever gives it the synthetic designed clips.
- **Design once, then clone**: VoiceDesign alone would vary from sentence
  to sentence over a long book. Lektor designs the voice once, saves the
  clip to Drive, and makes every unit with Base from that one clip. The
  smoke test measures how consistent this is (speaker embedding similarity
  between widely separated sentences and the reference).
- **T4 notes**: no FlashAttention 2 and no fast bfloat16 on Turing GPUs, so
  Lektor loads Qwen3-TTS with SDPA attention in float16. If float16 ever
  produces silent or broken audio, set `params: {dtype: float32}` on the
  preset (slower, about twice the memory).
- **Memory and speed (estimates)**: about 5 to 7 GB of GPU memory in
  float16; roughly real time or a little slower on a T4.
- Pins `transformers==4.57.3`: isolated in its own environment.

## Designed voices

Defined under `designs:` in `lektor/data/voices.yaml` (`british-female`,
`british-male`). The clip, its text and a provenance record (model,
description, seed, date, `synthetic: true`, `real_person: false`) are saved
in `My Drive/Lektor/voices/designed/`. No real person's voice is used
anywhere; consent-based cloning is a later phase.

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
| pyspellchecker | MIT | English wordlist for the unknown term detector |
| numpy, scipy, soundfile, pyloudnorm | BSD, BSD, BSD-3-Clause, MIT | audio |
| jiwer | Apache 2.0 | word error rates |
| uv | Apache 2.0 / MIT | engine environments |
| kokoro, misaki | Apache 2.0 | Kokoro engine |
| chatterbox-tts | MIT | Chatterbox engines |
| qwen-tts | Apache 2.0 | Qwen3-TTS engines |
| ffmpeg (preinstalled in Colab, run as a separate program) | LGPL/GPL | encoding MP3 and AAC, atempo |

Not used, deliberately: `ebooklib` (AGPL; Lektor reads EPUB itself) and
`mutagen` (GPL; tags are written with ffmpeg).

## How the environments work

The speech packages pin incompatible versions of the same libraries, so
each engine runs in its own virtual environment under
`/content/lektor_envs/<name>`, created the first time it is needed:

1. `uv venv --python <version>` makes the environment (uv downloads that
   Python if Colab does not have it).
2. `uv pip install --torch-backend auto <packages>` installs the engine;
   `--torch-backend auto` picks the PyTorch build that matches the GPU
   driver, or the CPU build when there is no GPU.
3. A check (`import kokoro`, ...) confirms it works, and a marker file
   records the specification, so a changed specification is rebuilt.

uv caches every wheel, so torch is downloaded once per Colab session and
hard-linked into each environment. The environments are not kept on Drive
(Drive is too slow for thousands of small files); they are rebuilt in each
new Colab session, a few minutes per engine.

Environments do **not** reuse Colab's own torch (`--system-site-packages`):
tested with uv, system packages do not count as already installed, and
Chatterbox needs torch 2.6.0 anyway.

The main notebook process never imports an engine. It starts a worker
(`python -m lektor.engines.worker --engine <name>`) inside the environment
and exchanges JSON lines with it; audio comes back as temporary `.npy` files.
The worker keeps the model loaded until the stage finishes.
