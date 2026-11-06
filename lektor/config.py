"""Book settings (``book.yaml``) and the book folder layout.

``book.yaml`` lives in each book's Drive folder. The librarian creates it
with the notebook form and may edit it by hand. Everything has a sensible
default, so a minimal file needs only ``book_id`` and ``title``.

Unknown keys are reported as warnings rather than errors, so a typo never
stops the pipeline but is still visible.
"""

from __future__ import annotations

import datetime as _dt
import re
import warnings
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ConfigError

# ---------------------------------------------------------------------------
# Placeholder statement wording. The librarian must confirm this with the
# university's copyright lead before any copy is given to a student.
# ---------------------------------------------------------------------------
DEFAULT_ACCESSIBILITY_STATEMENT = (
    "This accessible copy has been made by the University of West London "
    "Library for the personal use of a print disabled student, under "
    "{basis_phrase}. It must not be shared or sold."
)
DEFAULT_ACKNOWLEDGEMENT = (
    "Original work: {title}, by {author}, published by {publisher}{year_phrase}. "
    "All rights in the original work remain with the copyright holder."
)
DEFAULT_SYNTHETIC_VOICE_STATEMENT = (
    "This audiobook is narrated by a synthetic voice. Words added by the "
    "library, such as figure captions and notes, are read in a second voice."
)
BASIS_PHRASES = {
    "publisher permission": "permission from the publisher",
    "s31B": "section 31B of the Copyright, Designs and Patents Act 1988",
}

BOOK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)


class HeadingRule(_Section):
    """Force headings whose text matches ``pattern`` to ``level``."""

    pattern: str
    level: int = Field(ge=1, le=6)


class StructureSettings(_Section):
    # Which heading level becomes a chapter (navigation point and M4B
    # chapter). "auto" picks the level of numbered headings if there are
    # any, otherwise level 1.
    chapter_level: Union[Literal["auto"], int] = "auto"
    heading_rules: list[HeadingRule] = Field(default_factory=list)
    # Printed page = physical PDF page - page_offset. Only used when the PDF
    # has no page labels and no printed page numbers could be detected.
    page_offset: Optional[int] = None
    # Headings whose section is kept in the script but marked as a skip block
    # (not read aloud). Matched case-insensitively against the whole heading.
    skip_sections: list[str] = Field(
        default_factory=lambda: ["Contents", "Table of Contents", "Index"]
    )


class IngestSettings(_Section):
    # auto: Docling for PDF, DOCX, PPTX and HTML when available, otherwise the
    # built in fallback readers. EPUB always uses Lektor's own reader, which
    # keeps EPUB page break markers.
    reader: Literal["auto", "docling", "fallback"] = "auto"
    # OCR for scanned PDFs: auto turns it on when a page has no text layer.
    ocr: Literal["auto", "on", "off"] = "auto"


class VoiceSettings(_Section):
    main: str = "kokoro-bf-emma"
    secondary: str = "kokoro-bm-george"
    # Neutral voice used to announce "Voice A" etc. in the audition.
    announcer: str = "kokoro-bm-george"


class SpeechSettings(_Section):
    # How URLs and email addresses are read: "domain" reads the domain only
    # (for example "ableton dot com"), "link" says "web link".
    urls: Literal["domain", "link"] = "domain"
    # Figures with no caption: "silent" says nothing, "page" says
    # "Figure on page 48".
    uncaptioned_figures: Literal["silent", "page"] = "page"
    # Tables with more cells than this are replaced by a spoken notice.
    max_table_cells: int = 40
    # Where footnotes are read.
    footnotes: Literal["end_of_paragraph", "end_of_chapter", "skip"] = "end_of_paragraph"
    endnotes: Literal["end_of_chapter", "skip"] = "end_of_chapter"


class PauseSettings(_Section):
    sentence: float = 0.35
    paragraph: float = 0.8
    before_heading: float = 1.2
    after_heading: float = 0.6
    chapter: float = 2.0
    jitter: float = 0.15
    fade_ms: float = 10.0


class AudioSettings(_Section):
    sample_rate: int = 24000
    loudness_lufs: float = -18.0
    true_peak_dbtp: float = -1.5
    mp3_bitrate_kbps: int = 64
    aac_bitrate_kbps: int = 64


class QASettings(_Section):
    whisper_model: str = "small.en"
    max_wer: float = 0.15
    max_silence_s: float = 1.2
    retries: int = 3
    split_on_failure: bool = True


class PackageSettings(_Section):
    formats: list[Literal["daisy", "m4b", "mp3"]] = Field(
        default_factory=lambda: ["daisy", "m4b", "mp3"]
    )
    cover_image: Optional[str] = None


class Statements(_Section):
    accessibility: str = DEFAULT_ACCESSIBILITY_STATEMENT
    acknowledgement: str = DEFAULT_ACKNOWLEDGEMENT
    synthetic_voice: str = DEFAULT_SYNTHETIC_VOICE_STATEMENT
    # Set to true once the wording has been agreed with the copyright lead.
    confirmed: bool = False


class BookConfig(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)

    book_id: str
    title: str
    author: str = ""
    publisher: str = ""
    year: Optional[str] = None
    isbn: str = ""
    language: str = "en-GB"
    source: str = ""
    permission_basis: Literal["publisher permission", "s31B"] = "s31B"
    recipient_code: str = ""
    statements: Statements = Field(default_factory=Statements)
    structure: StructureSettings = Field(default_factory=StructureSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    voices: VoiceSettings = Field(default_factory=VoiceSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    pauses: PauseSettings = Field(default_factory=PauseSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    qa: QASettings = Field(default_factory=QASettings)
    package: PackageSettings = Field(default_factory=PackageSettings)

    @field_validator("book_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not BOOK_ID_RE.match(v):
            raise ValueError(
                "book_id must use only lower case letters, digits, hyphens and "
                "underscores (for example 'making-music')"
            )
        return v

    @field_validator("year", mode="before")
    @classmethod
    def _year_to_str(cls, v: Any) -> Any:
        return None if v in (None, "") else str(v)

    # -- statements with the placeholders filled in ------------------------
    def statement(self, which: str) -> str:
        template = getattr(self.statements, which)
        return template.format(
            title=self.title,
            author=self.author or "an unknown author",
            publisher=self.publisher or "an unknown publisher",
            year_phrase=f" in {self.year}" if self.year else "",
            basis_phrase=BASIS_PHRASES.get(self.permission_basis, self.permission_basis),
        )


def _unknown_keys(model: BaseModel, prefix: str = "") -> list[str]:
    found = []
    for key in (model.model_extra or {}):
        found.append(prefix + key)
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            found.extend(_unknown_keys(value, prefix + name + "."))
    return found


def _friendly_validation(e: ValidationError, path: Path) -> ConfigError:
    lines = []
    for err in e.errors():
        where = ".".join(str(p) for p in err["loc"]) or "(top level)"
        lines.append(f"  - {where}: {err['msg']}")
    return ConfigError(
        f"{path.name} has {len(lines)} problem(s):\n" + "\n".join(lines),
        hint=f"Open {path} in the Colab file editor, fix the setting(s) above and run the cell again.",
    )


def load_book_config(path: Path | str) -> BookConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"No book settings file was found at {path}.",
            hint="Run section 1 of the notebook (Start or open a book) first.",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        raise ConfigError(
            f"{path.name} is not valid YAML{where}.",
            hint="Check for a missing colon, a stray tab character or unbalanced quotes.",
        ) from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} should contain settings as 'name: value' lines.")
    try:
        cfg = BookConfig.model_validate(data)
    except ValidationError as e:
        raise _friendly_validation(e, path) from e
    for key in _unknown_keys(cfg):
        warnings.warn(f"{path.name}: unknown setting '{key}' was ignored (is it a typo?)")
    return cfg


def save_book_config(cfg: BookConfig, path: Path | str) -> None:
    path = Path(path)
    data = cfg.model_dump(mode="json", exclude_none=False)
    header = (
        "# Lektor book settings. You may edit this file in the Colab file editor.\n"
        "# See docs/configuration in the Lektor repository for every option.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Folder layout
# ---------------------------------------------------------------------------
class BookPaths:
    """Every file location for one book, relative to its Drive folder."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @classmethod
    def for_book(cls, lektor_home: Path | str, book_id: str) -> "BookPaths":
        return cls(Path(lektor_home) / "books" / book_id)

    @property
    def lektor_home(self) -> Path:
        return self.root.parent.parent

    book_yaml = property(lambda s: s.root / "book.yaml")
    source_dir = property(lambda s: s.root / "source")
    script_dir = property(lambda s: s.root / "script")
    script = property(lambda s: s.root / "script" / "script.md")
    review = property(lambda s: s.root / "script" / "review.html")
    lexicon_suggestions = property(lambda s: s.root / "script" / "lexicon_suggestions.csv")
    book_lexicon = property(lambda s: s.root / "script" / "lexicon.csv")
    units = property(lambda s: s.root / "units.jsonl")
    audition_dir = property(lambda s: s.root / "audition")
    cache_dir = property(lambda s: s.root / "cache")
    qa_dir = property(lambda s: s.root / "qa")
    qa_report = property(lambda s: s.root / "qa" / "qa_report.html")
    output_dir = property(lambda s: s.root / "output")
    log = property(lambda s: s.root / "production_log.txt")
    shared_lexicons = property(lambda s: s.lektor_home / "lexicons")
    production_records = property(lambda s: s.lektor_home / "production_records.csv")

    def ensure(self) -> "BookPaths":
        for d in (self.source_dir, self.script_dir, self.audition_dir, self.cache_dir,
                  self.qa_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def resolve_source(self, cfg: BookConfig) -> Path:
        """Find the source document named in book.yaml."""
        if cfg.source:
            p = Path(cfg.source)
            candidates = [p] if p.is_absolute() else [self.root / p, self.source_dir / p]
            for c in candidates:
                if c.exists():
                    return c
            raise ConfigError(
                f"The source file '{cfg.source}' named in book.yaml could not be found.",
                hint=f"Put the file in {self.source_dir} or correct the 'source' setting.",
            )
        files = sorted(f for f in self.source_dir.glob("*") if f.is_file() and not f.name.startswith("."))
        if len(files) == 1:
            return files[0]
        if not files:
            raise ConfigError(
                f"There is no source file in {self.source_dir}.",
                hint="Upload the book (PDF, EPUB, DOCX, HTML, TXT or Markdown) to that folder.",
            )
        raise ConfigError(
            f"There are {len(files)} files in {self.source_dir} and book.yaml does not say which to use.",
            hint="Set 'source' in book.yaml to the file name.",
        )


def append_log(paths: BookPaths, stage: str, message: str) -> None:
    """Append a line to the book's production log."""
    from . import __version__

    paths.root.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(paths.log, "a", encoding="utf-8") as f:
        f.write(f"{stamp}  lektor {__version__}  [{stage}]  {message}\n")
