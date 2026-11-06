"""Create and open books in the Lektor home folder (on Google Drive)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from .config import BookConfig, BookPaths, append_log, load_book_config, save_book_config
from .errors import ConfigError

DRIVE_HOME = Path("/content/drive/MyDrive/Lektor")


def default_home() -> Path:
    if os.environ.get("LEKTOR_HOME"):
        return Path(os.environ["LEKTOR_HOME"])
    return DRIVE_HOME


def ensure_home(home: Path) -> Path:
    home = Path(home)
    (home / "books").mkdir(parents=True, exist_ok=True)
    (home / "lexicons").mkdir(parents=True, exist_ok=True)
    install_shared_lexicons(home)
    return home


def install_shared_lexicons(home: Path) -> list[Path]:
    """Copy the lexicons shipped with Lektor into the shared Drive folder,
    once, so the librarian can edit them. Existing files are never replaced."""
    src_dir = Path(__file__).parent / "data" / "lexicons"
    copied = []
    for src in sorted(src_dir.glob("*.csv")):
        if src.name.startswith("_"):
            continue  # built-in defaults stay inside the package
        dest = Path(home) / "lexicons" / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
            copied.append(dest)
    return copied


def create_or_update_book(
    home: Path,
    book_id: str,
    title: str,
    author: str = "",
    publisher: str = "",
    year: Optional[str] = None,
    source: str = "",
    permission_basis: str = "s31B",
    recipient_code: str = "",
    accessibility_statement: str = "",
    acknowledgement: str = "",
) -> tuple[BookPaths, BookConfig, list[str]]:
    """Create the book folder and book.yaml, or update the fields given."""
    home = ensure_home(home)
    paths = BookPaths.for_book(home, book_id)
    messages: list[str] = []
    if paths.book_yaml.exists():
        cfg = load_book_config(paths.book_yaml)
        messages.append(f"Opened the existing book '{book_id}'. Settings from the form were updated.")
    else:
        try:
            cfg = BookConfig(book_id=book_id, title=title or book_id)
        except Exception as e:
            raise ConfigError(f"The book id '{book_id}' cannot be used: {e}",
                              hint="Use lower case letters, digits and hyphens, for example 'making-music'.") from e
        messages.append(f"Created a new book '{book_id}'.")
    paths.ensure()
    updates = {"title": title, "author": author, "publisher": publisher, "year": year,
               "permission_basis": permission_basis, "recipient_code": recipient_code}
    for k, v in updates.items():
        if v not in (None, ""):
            setattr(cfg, k, v)
    if accessibility_statement:
        cfg.statements.accessibility = accessibility_statement
    if acknowledgement:
        cfg.statements.acknowledgement = acknowledgement

    if source:
        src = Path(source)
        if not src.is_absolute():
            for base in (home, home.parent, paths.root):
                if (base / src).exists():
                    src = base / src
                    break
        if not src.exists():
            raise ConfigError(f"The source file {source} was not found.",
                              hint="Check the path. In Colab, right-click the file in the Files panel and choose "
                                   "'Copy path'.")
        if paths.source_dir not in src.resolve().parents:
            dest = paths.source_dir / src.name
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dest)
                messages.append(f"Copied {src.name} into the book's source folder.")
            src = dest
        cfg.source = src.name
    save_book_config(cfg, paths.book_yaml)
    append_log(paths, "book", "; ".join(messages))
    return paths, cfg, messages


def open_book(home: Path, book_id: str) -> tuple[BookPaths, BookConfig]:
    paths = BookPaths.for_book(home, book_id)
    if not paths.book_yaml.exists():
        existing = sorted(p.name for p in (Path(home) / "books").glob("*") if (p / "book.yaml").exists())
        hint = "Run section 1 (Start or open a book) first."
        if existing:
            hint += " Existing books: " + ", ".join(existing)
        raise ConfigError(f"There is no book called '{book_id}' in {home}.", hint=hint)
    return paths, load_book_config(paths.book_yaml)
