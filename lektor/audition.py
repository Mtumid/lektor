"""Voice audition: let the student choose the voice before the whole book is made.

The librarian picks a passage (by default about two minutes from the start
of a chosen chapter) and a set of presets. Lektor renders the passage with
each preset and labels the results **A to H in a shuffled order**, so the
student is not swayed by engine names. The real mapping is saved
separately in ``audition/key.json``.

Outputs in ``books/<id>/audition/``:

- ``voice_A.mp3`` ... one file per voice
- ``audition.mp3``: all samples, each announced ("Voice A") by a neutral voice
- ``audition_1.5x.mp3`` and ``audition_2x.mp3``: the same, sped up with
  ffmpeg's pitch preserving ``atempo``, because many listeners use high
  speeds and some voices fall apart when sped up
- ``feedback.txt``: a short form for the student
- ``key.json``: which letter is which preset (for the librarian)

The chosen letter is saved to ``book.yaml`` with :func:`choose_voice`.
"""

from __future__ import annotations

import json
import random
import string
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from .assemble import apply_fades, normalise_loudness, pause_after, resample, trim_silence
from .cache import UnitCache
from .chunk.plan import PlanOptions, Planner, Unit
from .config import BookConfig, BookPaths, append_log, save_book_config
from .engines.registry import get_preset
from .errors import LektorError
from .package.ffmpeg import run_ffmpeg
from .prepare import build_normaliser, chapter_level_for, voice_choice
from .script.parser import read_script
from .synth import check_audio, open_engine, synth_params

DEFAULT_PRESETS = [
    "kokoro-bf-emma",
    "kokoro-bm-george",
    "kokoro-bf-isabella",
    "chatterbox-turbo-british-female",
    "chatterbox-british-male",
    "qwen-ryan",
    "qwen-british-female",
    "qwen-british-male",
]
TARGET_SECONDS = 120
CHARS_PER_SECOND = 15


@dataclass
class AuditionResult:
    folder: Path
    letters: dict[str, str]  # letter -> preset
    files: dict[str, Path]
    combined: dict[str, Path]
    failed: dict[str, str]


def passage_units(paths: BookPaths, cfg: BookConfig, preset: str, chapter: str = "", seconds: int = TARGET_SECONDS
                  ) -> list[Unit]:
    """Main-voice units of about ``seconds`` from the start of a chapter.

    ``chapter`` is a chapter number (1, 2, ...) or part of a chapter title;
    empty means the first chapter with enough text.
    """
    script, _ = read_script(paths.script)
    norm = build_normaliser(paths, cfg)
    vc = voice_choice(preset, paths.lektor_home)
    planner = Planner(norm, vc, vc, PlanOptions(chapter_level=chapter_level_for(cfg, script), footnotes="skip",
                                                uncaptioned_figures="silent", front_matter=[]))
    units = [u for u in planner.plan(script) if u.role == "main" and u.kind != "frontmatter"]
    chapters: "OrderedDict[int, list[Unit]]" = OrderedDict()
    for u in units:
        chapters.setdefault(u.chapter, []).append(u)
    chosen = None
    if chapter:
        for c, us in chapters.items():
            if (chapter.isdigit() and int(chapter) == c) or chapter.lower() in us[0].chapter_title.lower():
                chosen = us
                break
        if chosen is None:
            raise LektorError(f"No chapter matches '{chapter}'.",
                              hint="Type a chapter number, or a few words of its title, as shown in the review report.")
    else:
        for us in chapters.values():
            if sum(len(u.spoken) for u in us) >= seconds * CHARS_PER_SECOND * 0.6:
                chosen = us
                break
        chosen = chosen or (list(chapters.values())[0] if chapters else [])
    out, chars = [], 0
    for u in chosen:
        out.append(u)
        chars += len(u.spoken)
        if chars >= seconds * CHARS_PER_SECOND:
            break
    if out:
        out[-1].boundary = "chapter"
    return out


def render_units(units: list[Unit], engine, preset, cache: UnitCache, home: Path, sr_out: int) -> list[np.ndarray]:
    ref = preset.reference_path(home)
    todo = [u for u in units if not cache.has(u.cache_key)]
    for i in range(0, len(todo), 4):
        chunk = todo[i: i + 4]
        audios = engine.synthesize_batch([{"text": u.spoken, "voice": preset.voice, "params": synth_params(u.params),
                                           "seed": u.seed, "reference": str(ref) if ref else None} for u in chunk])
        for u, a in zip(chunk, audios):
            a = np.asarray(a, dtype=np.float32)
            problem = check_audio(a, engine.sample_rate, u.spoken)
            if problem:
                raise LektorError(f"{preset.name}: {problem} for '{u.display[:60]}'.")
            cache.put(u.cache_key, a, engine.sample_rate)
    cache.sync(force=True)
    out = []
    for u in units:
        a, sr = cache.get(u.cache_key)
        out.append(apply_fades(trim_silence(resample(a, sr, sr_out), sr_out), sr_out, 10))
    return out


def join(units: list[Unit], clips: list[np.ndarray], cfg: BookConfig) -> np.ndarray:
    sr = cfg.audio.sample_rate
    parts = []
    for i, (u, c) in enumerate(zip(units, clips)):
        parts.append(c)
        if i + 1 < len(units):
            parts.append(np.zeros(int(pause_after(u, units[i + 1], cfg) * sr), dtype=np.float32))
    audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    return normalise_loudness(audio, sr, cfg.audio.loudness_lufs, cfg.audio.true_peak_dbtp)[0]


def _mp3(audio: np.ndarray, sr: int, out: Path, tempo: float = 1.0, kbps: int = 96) -> Path:
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        sf.write(str(wav), audio, sr, subtype="PCM_16")
        args = ["-i", str(wav)]
        if tempo != 1.0:
            # atempo accepts 0.5 to 2.0 per filter; chain for anything outside.
            filters, t = [], tempo
            while t > 2.0:
                filters.append("atempo=2.0")
                t /= 2.0
            filters.append(f"atempo={t:.4g}")
            args += ["-filter:a", ",".join(filters)]
        args += ["-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-ac", "1", str(out)]
        run_ffmpeg(args)
    return out


FEEDBACK = """VOICE AUDITION: FEEDBACK
========================

Book: {title}

Thank you for listening. There are {n} voices, called {letters}. They all read
the same passage. Please listen to as much of each as you like, at the speed
you normally use (there are also 1.5 times and 2 times speed versions).

1. Which voice do you like best? Please put them in order, best first.

   Best: ____   2nd: ____   3rd: ____   (and so on if you wish)

2. For each voice, what (if anything) sounds artificial or distracting?
   For example: rhythm, pauses, breathing, pronunciation, accent, tone,
   words run together, or it sounds odd when sped up.

{per_voice}
3. What speed do you normally listen at?  1x / 1.25x / 1.5x / 1.75x / 2x / other: ____

4. Is there anything else you would like us to know?



You can reply by email, in any format you like. Thank you.
"""


def run_audition(paths: BookPaths, cfg: BookConfig, presets: Optional[list[str]] = None, chapter: str = "",
                 seconds: int = TARGET_SECONDS, log: Callable[[str], None] = print,
                 engine_factory: Optional[Callable] = None, speeds=(1.5, 2.0)) -> AuditionResult:
    presets = presets or DEFAULT_PRESETS
    if len(presets) > 26:
        raise LektorError("An audition can have at most 26 voices.")
    home = paths.lektor_home
    for p in presets:
        get_preset(p, home)  # fail early on a typo
    announcer = cfg.voices.announcer
    if engine_factory is None:
        from .voice_design import ensure_designs

        ensure_designs(presets + [announcer], home, log)
    folder = paths.audition_dir
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"{cfg.book_id}|{'|'.join(presets)}")
    shuffled = presets[:]
    rng.shuffle(shuffled)
    letters = {string.ascii_uppercase[i]: p for i, p in enumerate(shuffled)}
    cache = UnitCache.for_book(paths)
    cache.restore()
    sr = cfg.audio.sample_rate

    by_engine: "OrderedDict[str, list[str]]" = OrderedDict()
    for p in presets:
        by_engine.setdefault(get_preset(p, home).engine, []).append(p)
    samples: dict[str, np.ndarray] = {}
    failed: dict[str, str] = {}
    for engine_name, names in by_engine.items():
        log(f"Rendering with {engine_name}: {', '.join(names)}")
        try:
            first = get_preset(names[0], home)
            engine = engine_factory(first) if engine_factory else open_engine(first, home, log)
        except Exception as e:  # noqa: BLE001 - one engine failing must not stop the others
            for n in names:
                failed[n] = str(getattr(e, "message", e))
            log(f"  could not start {engine_name}: {failed[names[0]]}")
            continue
        try:
            for name in names:
                try:
                    preset = get_preset(name, home)
                    units = passage_units(paths, cfg, name, chapter, seconds)
                    clips = render_units(units, engine, preset, cache, home, sr)
                    samples[name] = join(units, clips, cfg)
                    log(f"  {name}: {samples[name].size / sr:.0f} s")
                except Exception as e:  # noqa: BLE001
                    failed[name] = str(getattr(e, "message", e))
                    log(f"  {name} failed: {failed[name]}")
        finally:
            engine.unload()

    # Announcements ("Voice A") in the neutral announcer voice
    ann = get_preset(announcer, home)
    try:
        engine = engine_factory(ann) if engine_factory else open_engine(ann, home, log)
        try:
            announcements = {}
            for letter in letters:
                a = engine.synthesize(f"Voice {letter}.", ann.voice, synth_params(ann.params), 7)
                a = resample(np.asarray(a, dtype=np.float32), engine.sample_rate, sr)
                announcements[letter] = normalise_loudness(trim_silence(a, sr), sr, cfg.audio.loudness_lufs,
                                                           cfg.audio.true_peak_dbtp)[0]
        finally:
            engine.unload()
    except Exception as e:  # noqa: BLE001
        log(f"The announcer voice failed ({getattr(e, 'message', e)}); using a short tone instead.")
        t = np.arange(int(0.4 * sr)) / sr
        tone = (0.2 * np.sin(2 * np.pi * 660 * t)).astype(np.float32)
        announcements = {letter: tone for letter in letters}

    files: dict[str, Path] = {}
    parts = []
    gap_short = np.zeros(int(0.8 * sr), dtype=np.float32)
    gap_long = np.zeros(int(2.0 * sr), dtype=np.float32)
    for letter, preset in letters.items():
        if preset not in samples:
            continue
        files[letter] = _mp3(samples[preset], sr, folder / f"voice_{letter}.mp3")
        parts += [announcements[letter], gap_short, samples[preset], gap_long]
    combined: dict[str, Path] = {}
    if parts:
        whole = np.concatenate(parts)
        combined["1x"] = _mp3(whole, sr, folder / "audition.mp3")
        for s in speeds:
            label = f"{s:g}x"
            combined[label] = _mp3(whole, sr, folder / f"audition_{label}.mp3", tempo=s)
    available = [l for l, p in letters.items() if p in samples]
    per_voice = "".join(f"   Voice {l}: ______________________________________________\n\n" for l in available)
    (folder / "feedback.txt").write_text(FEEDBACK.format(title=cfg.title, n=len(available),
                                                         letters=", ".join(available), per_voice=per_voice),
                                         encoding="utf-8")
    key = {"letters": letters, "failed": failed, "chapter": chapter, "seconds": seconds,
           "note": "Keep this file to yourself: the student should not see which engine is which."}
    (folder / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    append_log(paths, "audition", f"{len(files)} voices rendered ({', '.join(f'{l}={p}' for l, p in letters.items())}); "
                                  f"{len(failed)} failed")
    return AuditionResult(folder, letters, files, combined, failed)


def choose_voice(paths: BookPaths, cfg: BookConfig, letter: str, secondary: str = "") -> str:
    key_file = paths.audition_dir / "key.json"
    if not key_file.exists():
        raise LektorError("There is no audition for this book yet.", hint="Run section 4 first.")
    letters = json.loads(key_file.read_text())["letters"]
    letter = letter.strip().upper()
    if letter not in letters:
        raise LektorError(f"There is no voice {letter} in the audition.", hint="Choose one of: " + ", ".join(letters))
    cfg.voices.main = letters[letter]
    if secondary:
        cfg.voices.secondary = secondary
    save_book_config(cfg, paths.book_yaml)
    append_log(paths, "audition", f"voice {letter} chosen: main={cfg.voices.main}, secondary={cfg.voices.secondary}")
    return letters[letter]
