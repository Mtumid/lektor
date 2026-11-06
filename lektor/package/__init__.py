"""Packaging: DAISY 2.02, M4B and MP3, all from the timing map.

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


def _daisy(paths, cfg, timing, meta, out, log):
    from .daisy202 import write_daisy

    return write_daisy(paths, cfg, timing, meta, out, log)


WRITERS: dict[str, Callable] = {"daisy": _daisy}


def run_package(paths: BookPaths, cfg: BookConfig, formats: Optional[list[str]] = None,
                log: Callable[[str], None] = print, record: bool = True) -> PackageResult:
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
    if res.files and record:
        from ..records import append_record

        append_record(paths, cfg, list(res.files))
    _warnings(paths, cfg, res)
    return res


def _warnings(paths: BookPaths, cfg: BookConfig, res: PackageResult) -> None:
    """Things the librarian should know before sending the files."""
    if not cfg.statements.confirmed:
        res.notes.append("The accessibility statement still uses the placeholder wording. Confirm it with the "
                         "copyright lead and set statements.confirmed: true in book.yaml before sending the files.")
    if not cfg.recipient_code:
        res.notes.append("No recipient code is set for this book, so the production record has none.")
    try:
        from ..qa.runner import load_results

        results = load_results(paths)
        open_flags = sum(1 for r in results.values() if not r["passed"] and not r.get("accepted"))
        if not results:
            res.notes.append("The audio has not been through the quality check (section 6).")
        elif open_flags:
            res.notes.append(f"{open_flags} pieces of speech are still flagged by the quality check (section 6).")
    except Exception:
        pass
