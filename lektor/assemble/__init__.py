"""Assembly and mastering: units -> one audio file per chapter + timing map.

For each chapter:

1. load each unit from the cache and resample it to the book's sample rate
2. trim leading and trailing silence
3. join units with pauses chosen by the boundary that follows each unit
   (sentence, paragraph, before/after a heading, end of chapter), with
   seeded jitter so the rhythm never feels mechanical, and short fades
4. normalise loudness to the target (default -18 LUFS integrated) and limit
   true peaks (default -1.5 dBTP). Every chapter is normalised to the same
   target, so chapters match each other.

The **timing map** (``audio/timing.json``) records the exact start and end of
every unit and (estimated) sentence, every heading and every print page in
each chapter's audio. DAISY, M4B chapters and the future EPUB 3 writer all
read from it.

A chapter whose units and settings have not changed is not rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from ..cache import UnitCache
from ..chunk.plan import Unit, chapters_of, read_units, stable_int
from ..config import BookConfig, BookPaths, append_log
from ..errors import LektorError

ASSEMBLY_VERSION = "1"


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------
def resample(audio: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return audio.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(sr_from, sr_to)
    return resample_poly(audio, sr_to // g, sr_from // g).astype(np.float32)


def trim_silence(audio: np.ndarray, sr: int, threshold_db: float = -45.0, pad_s: float = 0.03) -> np.ndarray:
    """Remove quiet audio at both ends (frames quieter than ``threshold_db``
    relative to full scale), keeping a little padding."""
    if audio.size == 0:
        return audio
    frame = max(1, int(0.01 * sr))
    n = audio.size // frame
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    loud = np.where(db > threshold_db)[0]
    if loud.size == 0:
        return audio[:0]
    pad = int(pad_s * sr)
    start = max(0, loud[0] * frame - pad)
    end = min(audio.size, (loud[-1] + 1) * frame + pad)
    return audio[start:end]


def apply_fades(audio: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(int(sr * ms / 1000), audio.size // 2)
    if n <= 0:
        return audio
    a = audio.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    a[:n] *= ramp
    a[-n:] *= ramp[::-1]
    return a


def true_peak(audio: np.ndarray, sr: int) -> float:
    from scipy.signal import resample_poly

    if audio.size == 0:
        return 0.0
    up = resample_poly(audio, 4, 1)
    return float(np.max(np.abs(up)))


def limit_true_peak(audio: np.ndarray, sr: int, ceiling_dbtp: float) -> np.ndarray:
    """A simple look-ahead limiter: reduce gain only around peaks above the ceiling."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    from scipy.signal import resample_poly

    ceiling = 10 ** (ceiling_dbtp / 20)
    if audio.size == 0:
        return audio
    up = np.abs(resample_poly(audio, 4, 1))
    n = audio.size
    peak = up[: n * 4].reshape(-1, 4).max(axis=1) if up.size >= n * 4 else np.abs(audio)
    if float(peak.max()) <= ceiling:
        return audio
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9)).astype(np.float32)
    w = max(3, int(0.005 * sr))
    g = minimum_filter1d(need, size=2 * w + 1)
    g = uniform_filter1d(g, size=w)
    out = audio * g
    return np.clip(out, -ceiling, ceiling).astype(np.float32)


def integrated_loudness(audio: np.ndarray, sr: int) -> float:
    import pyloudnorm as pyln

    if audio.size < sr * 0.5:
        return float("-inf")
    return float(pyln.Meter(sr).integrated_loudness(audio.astype(np.float64)))


def normalise_loudness(audio: np.ndarray, sr: int, target_lufs: float, ceiling_dbtp: float) -> tuple[np.ndarray, float]:
    lufs = integrated_loudness(audio, sr)
    if not np.isfinite(lufs):
        return audio, lufs
    gain = 10 ** ((target_lufs - lufs) / 20)
    out = (audio * gain).astype(np.float32)
    out = limit_true_peak(out, sr, ceiling_dbtp)
    return out, lufs


# ---------------------------------------------------------------------------
# Pauses
# ---------------------------------------------------------------------------
def pause_after(unit: Unit, next_unit: Optional[Unit], cfg: BookConfig) -> float:
    p = cfg.pauses
    b = unit.boundary
    base = {
        "sentence": p.sentence,
        "paragraph": p.paragraph,
        "section": p.before_heading,
        "heading": p.after_heading,
        "chapter": p.chapter,
    }.get(b, p.paragraph)
    if next_unit is not None and next_unit.kind == "heading" and b not in ("heading", "chapter"):
        base = max(base, p.before_heading)
    if next_unit is not None and unit.role != next_unit.role and b == "sentence":
        base = max(base, p.sentence + 0.15)  # a voice change needs a breath
    rnd = random.Random(stable_int(unit.id, "pause"))
    return max(0.05, base * (1 + rnd.uniform(-p.jitter, p.jitter)))


# ---------------------------------------------------------------------------
# Chapters
# ---------------------------------------------------------------------------
@dataclass
class ChapterAudio:
    chapter: int
    title: str
    path: Path
    duration: float
    entry: dict


def chapter_fingerprint(units: list[Unit], cfg: BookConfig, cache: Optional[UnitCache] = None) -> str:
    """Changes when a unit, a setting or a unit's audio changes (quality
    checks may replace a unit's audio under the same cache key)."""
    h = hashlib.sha256()
    h.update(ASSEMBLY_VERSION.encode())
    h.update(cfg.pauses.model_dump_json().encode())
    h.update(cfg.audio.model_dump_json().encode())
    for u in units:
        h.update(f"{u.id}|{u.cache_key}|{u.boundary}|{u.kind}|{u.chapter_title}".encode())
        if cache is not None and cache.has(u.cache_key):
            h.update(hashlib.sha1(cache.path(u.cache_key).read_bytes()).digest())
    return h.hexdigest()[:20]


def _sentence_times(u: Unit, start: float, end: float) -> list[dict]:
    weights = [max(1, len(s.spoken)) for s in u.sentences]
    total = sum(weights)
    out, t = [], start
    for s, w in zip(u.sentences, weights):
        d = (end - start) * w / total
        out.append({"sid": s.sid, "part": s.part, "start": round(t, 3), "end": round(t + d, 3), "pages": s.pages,
                    "display": s.display})
        t += d
    return out


def snap_sentence_boundaries(sents: list[dict], audio: np.ndarray, sr: int, offset: float,
                             window: float = 0.6) -> None:
    """Move each estimated boundary between sentences of one unit to the
    quietest 40 ms nearby, which is where the voice paused. This keeps DAISY
    text highlighting in step with the voice."""
    frame = max(1, int(0.04 * sr))
    hop = max(1, frame // 2)
    if audio.size < 3 * frame:
        return
    n = 1 + (audio.size - frame) // hop
    idx = np.arange(n)[:, None] * hop + np.arange(frame)[None, :]
    energy = np.sqrt(np.mean(audio[idx] ** 2, axis=1) + 1e-12)
    lo_limit = sents[0]["start"] - offset
    for k in range(1, len(sents)):
        est = sents[k]["start"] - offset
        hi_limit = sents[k]["end"] - offset
        lo = max(lo_limit + 0.1, est - window)
        hi = min(hi_limit - 0.1, est + window)
        if hi <= lo:
            continue
        f0, f1 = int(lo * sr) // hop, min(n, int(hi * sr) // hop + 1)
        if f1 <= f0:
            continue
        seg = energy[f0:f1]
        k_min = int(np.argmin(seg))
        quiet = seg <= seg[k_min] * 2.0 + 1e-6
        a0 = k_min
        while a0 > 0 and quiet[a0 - 1]:
            a0 -= 1
        a1 = k_min
        while a1 < len(seg) - 1 and quiet[a1 + 1]:
            a1 += 1
        best = f0 + (a0 + a1) / 2  # the middle of the quietest stretch
        t = round(offset + (best * hop + frame / 2) / sr, 3)
        sents[k - 1]["end"] = t
        sents[k]["start"] = t
        lo_limit = t - offset


def build_chapter(chapter: int, title: str, units: list[Unit], cache: UnitCache, cfg: BookConfig,
                  out_path: Path) -> tuple[np.ndarray, dict]:
    sr = cfg.audio.sample_rate
    pieces: list[np.ndarray] = []
    entries = []
    t = 0.0
    lead = np.zeros(int(0.3 * sr), dtype=np.float32)  # a short breath before each chapter
    pieces.append(lead)
    t += lead.size / sr
    for i, u in enumerate(units):
        if not cache.has(u.cache_key):
            raise LektorError(f"Unit {u.id} has no audio yet.", hint="Run section 5 (Generate audio) first.")
        a, a_sr = cache.get(u.cache_key)
        a = resample(a, a_sr, sr)
        a = trim_silence(a, sr)
        a = apply_fades(a, sr, cfg.pauses.fade_ms)
        start = t
        pieces.append(a)
        t += a.size / sr
        sents = _sentence_times(u, start, t)
        if len(sents) > 1:
            snap_sentence_boundaries(sents, a, sr, start)
        entries.append({"id": u.id, "kind": u.kind, "role": u.role, "heading_level": u.heading_level,
                        "start": round(start, 3), "end": round(t, 3), "ref": u.ref, "line": u.line,
                        "sentences": sents})
        nxt = units[i + 1] if i + 1 < len(units) else None
        gap = np.zeros(int(pause_after(u, nxt, cfg) * sr), dtype=np.float32)
        pieces.append(gap)
        t += gap.size / sr
    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    audio, measured = normalise_loudness(audio, sr, cfg.audio.loudness_lufs, cfg.audio.true_peak_dbtp)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.flac")
    sf.write(str(tmp), audio, sr, format="FLAC", subtype="PCM_16")
    tmp.replace(out_path)
    entry = {"chapter": chapter, "title": title, "file": out_path.name, "duration": round(audio.size / sr, 3),
             "loudness_before": round(measured, 2) if np.isfinite(measured) else None, "units": entries}
    return audio, entry


def audio_dir(paths: BookPaths) -> Path:
    return paths.root / "audio"


def timing_path(paths: BookPaths) -> Path:
    return audio_dir(paths) / "timing.json"


def load_timing(paths: BookPaths) -> dict:
    p = timing_path(paths)
    if not p.exists():
        raise LektorError("The audio has not been assembled yet.", hint="Run the assemble step first.")
    return json.loads(p.read_text(encoding="utf-8"))


def run_assemble(paths: BookPaths, cfg: BookConfig, units: Optional[list[Unit]] = None,
                 cache: Optional[UnitCache] = None, log: Callable[[str], None] = print) -> dict:
    units = units if units is not None else read_units(paths.units)
    cache = cache or UnitCache.for_book(paths)
    cache.restore()
    missing = [u.id for u in units if not cache.has(u.cache_key)]
    if missing:
        raise LektorError(f"{len(missing)} units have no audio yet (for example {missing[0]}).",
                          hint="Run section 5 (Generate audio) until it finishes, then try again.")
    d = audio_dir(paths)
    d.mkdir(parents=True, exist_ok=True)
    old = {}
    if timing_path(paths).exists():
        try:
            old = {c["chapter"]: c for c in json.loads(timing_path(paths).read_text())["chapters"]}
        except Exception:
            old = {}
    chapters = []
    built = 0
    for c, title, cu in chapters_of(units):
        fp = chapter_fingerprint(cu, cfg, cache)
        out = d / f"{c:03d}.flac"
        prev = old.get(c)
        if prev and prev.get("fingerprint") == fp and out.exists():
            chapters.append(prev)
            continue
        log(f"Assembling chapter {c}: {title}")
        _, entry = build_chapter(c, title, cu, cache, cfg, out)
        entry["fingerprint"] = fp
        chapters.append(entry)
        built += 1
    valid = {f"{c['chapter']:03d}.flac" for c in chapters}
    for f in d.glob("*.flac"):
        if f.name not in valid:
            f.unlink()
    timing = {"version": 1, "sample_rate": cfg.audio.sample_rate, "book_id": cfg.book_id, "title": cfg.title,
              "total_duration": round(sum(c["duration"] for c in chapters), 3), "chapters": chapters}
    tmp = timing_path(paths).with_suffix(".tmp")
    tmp.write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(timing_path(paths))
    append_log(paths, "assemble", f"{len(chapters)} chapters ({built} rebuilt), "
                                  f"{timing['total_duration'] / 3600:.2f} hours")
    return timing


# ---------------------------------------------------------------------------
# Reading the timing map
# ---------------------------------------------------------------------------
def headings(chapter_entry: dict) -> list[dict]:
    return [{"level": u["heading_level"], "text": " ".join(s["display"] for s in u["sentences"]), "start": u["start"],
             "id": u["id"]} for u in chapter_entry["units"] if u["kind"] == "heading"]


def page_starts(chapter_entry: dict) -> list[dict]:
    out = []
    for u in chapter_entry["units"]:
        for s in u["sentences"]:
            for p in s["pages"]:
                out.append({"label": p, "start": s["start"], "sid": s["sid"], "unit": u["id"]})
    return out
