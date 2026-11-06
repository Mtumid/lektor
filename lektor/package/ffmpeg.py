"""Small wrappers around ffmpeg and ffprobe (preinstalled in Colab).

ffmpeg runs as a separate program, so its licence does not affect Lektor's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..errors import LektorError


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise LektorError("ffmpeg is not installed.", hint="It is normally part of Colab. Run section 0 again.")
    return exe


def run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise LektorError(f"ffmpeg failed: {proc.stderr.strip()[-800:]}",
                          hint="Run the cell again. If it fails again, send this message to the developer.")


def probe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        raise LektorError("ffprobe is not installed.")
    proc = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                           "-show_chapters", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise LektorError(f"ffprobe could not read {Path(path).name}: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def has_encoder(name: str) -> bool:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True)
    return f" {name} " in proc.stdout


def escape_meta(value: str) -> str:
    """Escape a value for an ffmetadata file."""
    out = str(value)
    for ch in ("\\", "=", ";", "#", "\n"):
        out = out.replace(ch, "\\" + ch)
    return out
