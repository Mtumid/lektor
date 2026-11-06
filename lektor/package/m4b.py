"""M4B audiobook: AAC mono, chapter markers from the timing map, cover, tags."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from .common import BookMeta
from .ffmpeg import escape_meta, run_ffmpeg


def chapter_marks(timing: dict) -> list[tuple[int, int, str]]:
    """(start ms, end ms, title) for every chapter, from the timing map."""
    marks, t = [], 0.0
    for c in timing["chapters"]:
        start = t
        t += c["duration"]
        marks.append((int(round(start * 1000)), int(round(t * 1000)), c["title"]))
    return marks


def ffmetadata(meta: BookMeta, timing: dict) -> str:
    lines = [";FFMETADATA1"]
    tags = {
        "title": meta.title, "album": meta.title, "artist": meta.author, "album_artist": meta.author,
        "composer": meta.narrator, "date": meta.year, "genre": "Audiobook", "comment": meta.comment,
        "description": meta.comment, "copyright": meta.acknowledgement, "encoder": meta.generator,
        "publisher": meta.publisher,
    }
    for k, v in tags.items():
        if v:
            lines.append(f"{k}={escape_meta(v)}")
    for start, end, title in chapter_marks(timing):
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={escape_meta(title)}"]
    return "\n".join(lines) + "\n"


def write_m4b(audio_files: list[Path], timing: dict, meta: BookMeta, out: Path, bitrate_kbps: int = 64,
              cover: Optional[Path] = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        lst = td / "list.txt"
        lst.write_text("".join(f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                               for p in audio_files))
        md = td / "meta.txt"
        md.write_text(ffmetadata(meta, timing), encoding="utf-8")
        tmp = td / "book.m4b"
        args = ["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(md)]
        if cover is not None and cover.exists():
            args += ["-i", str(cover)]
        args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover is not None and cover.exists():
            args += ["-map", "2:v", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
        args += ["-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-ac", "1", "-movflags", "+faststart", "-f", "mp4",
                 str(tmp)]
        run_ffmpeg(args)
        part = out.with_suffix(".m4b.part")
        import shutil

        shutil.copyfile(tmp, part)
        part.replace(out)
    return out
