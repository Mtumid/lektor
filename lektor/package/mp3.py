"""A ZIP of MP3 files, one per chapter, with ID3 tags and track numbers."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from .common import BookMeta, safe_filename
from .ffmpeg import run_ffmpeg


def mp3_names(timing: dict) -> list[str]:
    n = len(timing["chapters"])
    width = max(2, len(str(n)))
    return [f"{i:0{width}d} {safe_filename(c['title'], 70)}.mp3" for i, c in enumerate(timing["chapters"], 1)]


def encode_mp3(src: Path, out: Path, meta: BookMeta, title: str, track: int, total: int, bitrate_kbps: int,
               cover: Optional[Path] = None) -> None:
    args = ["-i", str(src)]
    if cover is not None and cover.exists():
        args += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic",
                 "-metadata:s:v", "title=Cover", "-metadata:s:v", "comment=Cover (front)"]
    args += ["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k", "-ac", "1", "-id3v2_version", "3",
             "-metadata", f"title={title}", "-metadata", f"album={meta.title}", "-metadata", f"artist={meta.author}",
             "-metadata", f"album_artist={meta.author}", "-metadata", f"track={track}/{total}",
             "-metadata", f"date={meta.year}", "-metadata", "genre=Audiobook",
             "-metadata", f"composer={meta.narrator}", "-metadata", f"comment={meta.comment}",
             "-metadata", f"publisher={meta.publisher}", str(out)]
    run_ffmpeg(args)


def write_mp3_zip(audio_dir: Path, timing: dict, meta: BookMeta, out: Path, folder: str, bitrate_kbps: int = 64,
                  cover: Optional[Path] = None) -> Path:
    names = mp3_names(timing)
    total = len(names)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i, (c, name) in enumerate(zip(timing["chapters"], names), 1):
            encode_mp3(audio_dir / c["file"], td / name, meta, c["title"], i, total, bitrate_kbps, cover)
        tmp = td / "out.zip"
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
            for name in names:
                z.write(td / name, f"{folder}/{name}")
        part = out.with_suffix(".zip.part")
        shutil.copyfile(tmp, part)
        part.replace(out)
    return out
