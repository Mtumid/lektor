"""Things every package shares: metadata, file names, the cover image."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import __version__
from ..config import BookConfig, BookPaths


@dataclass
class BookMeta:
    title: str
    author: str
    publisher: str
    year: str
    language: str
    narrator: str
    accessibility: str
    acknowledgement: str
    synthetic_voice: str
    identifier: str
    generator: str

    @property
    def comment(self) -> str:
        return f"{self.accessibility} {self.acknowledgement} {self.synthetic_voice}"


def book_meta(cfg: BookConfig, timing: Optional[dict] = None) -> BookMeta:
    from ..engines.registry import get_preset

    try:
        main = get_preset(cfg.voices.main)
        narrator = f"Synthetic voice: {main.engine}, preset {main.name}"
    except Exception:
        narrator = f"Synthetic voice: {cfg.voices.main}"
    return BookMeta(
        title=cfg.title, author=cfg.author, publisher=cfg.publisher, year=cfg.year or "",
        language=cfg.language, narrator=narrator,
        accessibility=cfg.statement("accessibility"), acknowledgement=cfg.statement("acknowledgement"),
        synthetic_voice=cfg.statement("synthetic_voice"),
        identifier=f"uwl-lektor-{cfg.book_id}", generator=f"Lektor {__version__}",
    )


def safe_filename(text: str, max_len: int = 80) -> str:
    """A file name that is safe on Windows, macOS, Linux and in zip files."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[\\/:*?\"<>|]+", " ", t)
    t = re.sub(r"[^\w\s.,'()&-]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return (t[:max_len].rstrip(" .") or "untitled")


def make_cover(paths: BookPaths, cfg: BookConfig, out: Path) -> Optional[Path]:
    """The cover image: book.yaml's ``package.cover_image``, else the first
    page of a PDF source. Returns None if neither is possible."""
    try:
        from PIL import Image
    except ImportError:
        return None
    src: Optional[Path] = None
    if cfg.package.cover_image:
        p = Path(cfg.package.cover_image)
        src = p if p.is_absolute() else paths.root / p
        if not src.exists():
            src = None
    if src is not None:
        img = Image.open(src).convert("RGB")
    else:
        try:
            source = paths.resolve_source(cfg)
        except Exception:
            return None
        if source.suffix.lower() != ".pdf":
            return None
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(source))
            page = pdf[0]
            scale = 1400 / max(page.get_height(), 1)
            img = page.render(scale=scale).to_pil().convert("RGB")
            pdf.close()
        except Exception:
            return None
    img.thumbnail((1400, 1400))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=88)
    return out
