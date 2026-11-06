"""Packaging: DAISY 2.02 (milestone 5), M4B and MP3 from the timing map.

Every package carries the accessibility statement and acknowledgement in
its metadata and starts with the spoken front matter (chapter 0).
EPUB 3 with Media Overlays is planned as another thin writer on top of the
same timing map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..assemble import audio_dir, load_timing
from ..config import BookConfig, BookPaths, append_log
from .common import book_meta, make_cover, safe_filename
from .m4b import write_m4b
from .mp3 import write_mp3_zip


@dataclass
class PackageResult:
    files: dict[str, Path] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def output_name(cfg: BookConfig, kind: str) -> str:
    base = safe_filename(cfg.book_id, 60)
    return {"m4b": f"{base}.m4b", "mp3": f"{base}_mp3.zip", "daisy": f"{base}_daisy.zip"}[kind]


WRITERS: dict[str, Callable] = {}


def run_package(paths: BookPaths, cfg: BookConfig, formats: Optional[list[str]] = None,
                log: Callable[[str], None] = print) -> PackageResult:
    timing = load_timing(paths)
    meta = book_meta(cfg, timing)
    adir = audio_dir(paths)
    files = [adir / c["file"] for c in timing["chapters"]]
    cover = make_cover(paths, cfg, paths.output_dir / "cover.jpg")
    res = PackageResult()
    wanted = formats or list(cfg.package.formats)
    for kind in wanted:
        out = paths.output_dir / output_name(cfg, kind)
        if kind == "m4b":
            log("Making the M4B audiobook...")
            res.files["m4b"] = write_m4b(files, timing, meta, out, cfg.audio.aac_bitrate_kbps, cover)
        elif kind == "mp3":
            log("Making the MP3 files...")
            res.files["mp3"] = write_mp3_zip(adir, timing, meta, out, safe_filename(f"{cfg.title}", 60),
                                             cfg.audio.mp3_bitrate_kbps, cover)
        elif kind in WRITERS:
            log(f"Making the {kind.upper()} package...")
            res.files[kind] = WRITERS[kind](paths, cfg, timing, meta, out, log)
        else:
            res.notes.append(f"The {kind} format is not available yet.")
    append_log(paths, "package", ", ".join(f"{k}: {v.name}" for k, v in res.files.items()))
    return res
