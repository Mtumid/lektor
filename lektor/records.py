"""The library's record of accessible copies made (``production_records.csv``).

One row is appended each time a book is packaged. The file lives in the
Lektor home folder (``MyDrive/Lektor/production_records.csv``) so it covers
every book. Students' names are never stored: the recipient code is
whatever reference the librarian typed.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
from pathlib import Path

from . import __version__
from .config import BookConfig, BookPaths

COLUMNS = ["date", "book_id", "title", "author", "publisher", "source_file", "permission_basis", "formats_produced",
           "engine_and_voice_preset", "lektor_version", "recipient_code"]


def voice_summary(cfg: BookConfig, home: Path) -> str:
    from .engines.registry import get_preset

    parts = []
    for role, name in (("main", cfg.voices.main), ("secondary", cfg.voices.secondary)):
        try:
            p = get_preset(name, home)
            parts.append(f"{role}: {p.engine} / {p.name}")
        except Exception:
            parts.append(f"{role}: {name}")
    return "; ".join(parts)


def append_record(paths: BookPaths, cfg: BookConfig, formats: list[str]) -> dict:
    try:
        source = paths.resolve_source(cfg).name
    except Exception:
        source = cfg.source
    row = {
        "date": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "book_id": cfg.book_id,
        "title": cfg.title,
        "author": cfg.author,
        "publisher": cfg.publisher,
        "source_file": source,
        "permission_basis": cfg.permission_basis,
        "formats_produced": ", ".join(formats),
        "engine_and_voice_preset": voice_summary(cfg, paths.lektor_home),
        "lektor_version": __version__,
        "recipient_code": cfg.recipient_code,
    }
    path = paths.production_records
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    if new:
        w.writeheader()
    w.writerow(row)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    return row


def read_records(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8"))))
