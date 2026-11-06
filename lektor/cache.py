"""Cache of synthesised units, fast locally and safe on Drive.

Writing thousands of small files through Google Drive's FUSE mount is slow
and flaky. So units are written to a **local** folder on the Colab machine
first (``/content/lektor_cache/<book_id>``) and copied to Drive in
**batches**: every ``batch_size`` units or every ``interval`` seconds, as one
zip file per batch in ``books/<id>/cache/batches/``. After a disconnect,
:meth:`UnitCache.restore` unpacks the batches from Drive back into the local
folder, so at most one batch (a few minutes of work) is lost.

When a book is complete, :meth:`compact` merges the batches into one archive.

Audio is stored as FLAC (lossless, about half the size of WAV) at the
engine's own sample rate.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf


def default_local_root() -> Path:
    if os.environ.get("LEKTOR_LOCAL_CACHE"):
        return Path(os.environ["LEKTOR_LOCAL_CACHE"])
    if Path("/content").is_dir():
        return Path("/content/lektor_cache")
    return Path(tempfile.gettempdir()) / "lektor_cache"


class UnitCache:
    def __init__(self, drive_dir: Path, local_dir: Path, batch_size: int = 50, interval: float = 120.0) -> None:
        self.drive_dir = Path(drive_dir)
        self.local_dir = Path(local_dir)
        self.batch_dir = self.drive_dir / "batches"
        self.batch_size = batch_size
        self.interval = interval
        self.unsynced: list[str] = []
        self.last_sync = time.monotonic()
        self.local_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_book(cls, paths, local_root: Optional[Path] = None, **kw) -> "UnitCache":
        root = Path(local_root) if local_root else default_local_root()
        return cls(paths.cache_dir, root / paths.root.name, **kw)

    # -- paths ---------------------------------------------------------------
    def path(self, key: str) -> Path:
        return self.local_dir / key[:2] / f"{key}.flac"

    def has(self, key: str) -> bool:
        return self.path(key).exists()

    def keys(self) -> set[str]:
        return {p.stem for p in self.local_dir.glob("*/*.flac")}

    # -- read / write ----------------------------------------------------------
    def put(self, key: str, audio: np.ndarray, sample_rate: int) -> Path:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp.flac")
        a = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        sf.write(str(tmp), a, sample_rate, format="FLAC", subtype="PCM_24")
        tmp.replace(p)
        self.unsynced.append(key)
        return p

    def get(self, key: str) -> tuple[np.ndarray, int]:
        a, sr = sf.read(str(self.path(key)), dtype="float32", always_2d=False)
        if a.ndim > 1:
            a = a.mean(axis=1)
        return a, sr

    def delete(self, key: str) -> None:
        p = self.path(key)
        if p.exists():
            p.unlink()

    # -- Drive ---------------------------------------------------------------
    def due(self) -> bool:
        return len(self.unsynced) >= self.batch_size or (
            self.unsynced and time.monotonic() - self.last_sync >= self.interval)

    def sync(self, force: bool = False) -> int:
        """Copy unsynced units to Drive as one zip. Returns how many were copied."""
        if not self.unsynced or not (force or self.due()):
            return 0
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"batch-{stamp}-{os.getpid()}-{len(self.unsynced)}.zip"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / name
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
                for key in self.unsynced:
                    p = self.path(key)
                    if p.exists():
                        z.write(p, f"{key[:2]}/{key}.flac")
            dest_tmp = self.batch_dir / (name + ".part")
            shutil.copyfile(tmp, dest_tmp)
            dest_tmp.replace(self.batch_dir / name)
        n = len(self.unsynced)
        self.unsynced = []
        self.last_sync = time.monotonic()
        return n

    def restore(self) -> int:
        """Unpack batches from Drive into the local cache. Returns units restored."""
        if not self.batch_dir.is_dir():
            return 0
        done_file = self.local_dir / ".restored"
        done = set(done_file.read_text().split()) if done_file.exists() else set()
        restored = 0
        for z in sorted(self.batch_dir.glob("*.zip")):
            if z.name in done:
                continue
            try:
                with zipfile.ZipFile(z) as zf:
                    for member in zf.namelist():
                        target = self.local_dir / member
                        if not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src, open(target.with_suffix(".tmp"), "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            target.with_suffix(".tmp").replace(target)
                            restored += 1
            except zipfile.BadZipFile:
                continue  # a batch cut off by a disconnect; its units will be made again
            done.add(z.name)
        done_file.write_text("\n".join(sorted(done)))
        return restored

    def compact(self) -> Optional[Path]:
        """Merge all batches on Drive into a single archive."""
        if not self.batch_dir.is_dir():
            return None
        batches = sorted(self.batch_dir.glob("*.zip"))
        if len(batches) <= 1:
            return batches[0] if batches else None
        self.sync(force=True)
        batches = sorted(self.batch_dir.glob("*.zip"))
        name = f"all-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / name
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
                for key in sorted(self.keys()):
                    z.write(self.path(key), f"{key[:2]}/{key}.flac")
            shutil.copyfile(tmp, self.batch_dir / (name + ".part"))
            (self.batch_dir / (name + ".part")).replace(self.batch_dir / name)
        for b in batches:
            if b.name != name:
                b.unlink()
        done_file = self.local_dir / ".restored"
        done_file.write_text(name)
        return self.batch_dir / name
