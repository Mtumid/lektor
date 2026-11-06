"""Helpers called by the Colab notebook cells.

Each public function is one notebook step. They all go through
:func:`step`, which prints a plain English message when something goes
wrong and tucks the full traceback away underneath, so the librarian sees
what to do next first.
"""

from __future__ import annotations

import functools
import html
import os
import platform
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .books import create_or_update_book, default_home, ensure_home, open_book
from .errors import LektorError

STATE: dict = {"home": None, "book_id": None}


# ---------------------------------------------------------------------------
# Display helpers (work in Colab, in Jupyter and in a plain terminal)
# ---------------------------------------------------------------------------
def _ipython_display(html_text: str) -> bool:
    try:
        from IPython import get_ipython
        from IPython.display import HTML, display

        if get_ipython() is None:
            return False
        display(HTML(html_text))
        return True
    except Exception:
        return False


def say(text: str, kind: str = "info") -> None:
    colours = {"info": "#0b57d0", "ok": "#137333", "warn": "#8a4b00", "error": "#b3261e"}
    icon = {"info": "", "ok": "✔ ", "warn": "⚠ ", "error": "✖ "}[kind]
    esc = html.escape(text).replace("\n", "<br>")
    if not _ipython_display(f"<div style='border-left:4px solid {colours[kind]};padding:.3em .8em;margin:.3em 0'>"
                            f"{icon}{esc}</div>"):
        print(icon + text)


def link(path: Path, label: Optional[str] = None) -> None:
    """Show where a file is, with a button that opens it in Colab's editor."""
    path = Path(path)
    label = label or path.name
    shown = str(path).replace("/content/drive/MyDrive", "My Drive")
    if not _ipython_display(f"<div>📄 <b>{html.escape(label)}</b>: <code>{html.escape(shown)}</code></div>"):
        print(f"{label}: {path}")


def open_in_editor(path: Path) -> None:
    try:
        from google.colab import files  # type: ignore

        files.view(str(path))
    except Exception:
        link(path)


def _show_error(e: BaseException) -> None:
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    if isinstance(e, LektorError):
        msg, hint = e.message, e.hint
    elif isinstance(e, FileNotFoundError):
        msg, hint = f"A file could not be found: {e.filename}", "Check the path, and that Google Drive is mounted (run section 0)."
    elif isinstance(e, MemoryError) or "CUDA out of memory" in str(e):
        msg, hint = "The computer ran out of memory.", "Use Runtime > Restart session, then run section 0 and this cell again."
    else:
        msg, hint = f"Something unexpected went wrong ({type(e).__name__}: {e}).", \
            "Run the cell again. If it happens again, copy the technical details below and send them to the developer."
    body = (f"<div style='border-left:4px solid #b3261e;padding:.4em .9em;margin:.4em 0'>"
            f"<b>✖ {html.escape(msg)}</b>" + (f"<br>What to do: {html.escape(hint)}" if hint else "") +
            f"<details style='margin-top:.5em'><summary>Technical details</summary><pre style='white-space:pre-wrap'>"
            f"{html.escape(tb)}</pre></details></div>")
    if not _ipython_display(body):
        print(f"Problem: {msg}")
        if hint:
            print(f"What to do: {hint}")
        print("\nTechnical details:\n" + tb)


def step(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except KeyboardInterrupt:
            say("Stopped. Run the cell again to carry on where it left off.", "warn")
        except Exception as e:  # noqa: BLE001 - shown to the user
            _show_error(e)
        return None
    return wrapper


def _home() -> Path:
    return Path(STATE["home"] or default_home())


def _current(book_id: str = "") -> tuple:
    bid = book_id or STATE.get("book_id")
    if not bid:
        raise LektorError("No book is open yet.", hint="Run section 1 (Start or open a book) first.")
    STATE["book_id"] = bid
    return open_book(_home(), bid)


# ---------------------------------------------------------------------------
# 0. Setup
# ---------------------------------------------------------------------------
def gpu_info() -> dict:
    info = {"gpu": None, "memory_gb": None, "driver": None, "cuda_driver": None}
    if not shutil.which("nvidia-smi"):
        return info
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        if out:
            name, mem, drv = [x.strip() for x in out[0].split(",")]
            info.update(gpu=name, memory_gb=round(int(mem) / 1024, 1), driver=drv)
        head = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=20).stdout
        import re

        m = re.search(r"CUDA Version:\s*([\d.]+)", head)
        info["cuda_driver"] = m.group(1) if m else None
    except Exception:
        pass
    return info


@step
def setup(home: str = "") -> None:
    STATE["home"] = home or str(default_home())
    h = Path(STATE["home"])
    if str(h).startswith("/content/drive") and not Path("/content/drive/MyDrive").exists():
        raise LektorError("Google Drive is not connected.", hint="Run the setup cell again and allow access to Drive.")
    ensure_home(h)
    g = gpu_info()
    say(f"Lektor {__version__} is ready. Your Lektor folder is {str(h).replace('/content/drive/MyDrive', 'My Drive')}.", "ok")
    if g["gpu"]:
        say(f"GPU: {g['gpu']} with {g['memory_gb']} GB of memory.", "ok")
    else:
        say("There is no GPU in this session. Converting books and the Kokoro voice still work, but more "
            "slowly, and the other voices need a GPU.\nTo get one: Runtime menu > Change runtime type > "
            "T4 GPU > Save, then run this cell again.", "warn")


# ---------------------------------------------------------------------------
# 1. Start or open a book
# ---------------------------------------------------------------------------
@step
def start_book(book_id: str, title: str = "", author: str = "", publisher: str = "", year: str = "",
               source: str = "", permission_basis: str = "s31B", recipient_code: str = "",
               accessibility_statement: str = "", acknowledgement: str = "") -> None:
    book_id = book_id.strip()
    paths, cfg, msgs = create_or_update_book(
        _home(), book_id, title.strip(), author.strip(), publisher.strip(), year.strip() or None,
        source.strip(), permission_basis, recipient_code.strip(), accessibility_statement.strip(),
        acknowledgement.strip(),
    )
    STATE["book_id"] = book_id
    for m in msgs:
        say(m, "ok")
    link(paths.book_yaml, "Book settings")
    if not cfg.source:
        say(f"No source file yet. Upload the book to {paths.source_dir} or fill in the 'source' field.", "warn")
    if not cfg.statements.confirmed:
        say("The accessibility statement still uses Lektor's placeholder wording. Please confirm it with the "
            "university's copyright lead, then set 'confirmed: true' under 'statements' in book.yaml.", "warn")


# ---------------------------------------------------------------------------
# 2. Convert to script
# ---------------------------------------------------------------------------
@step
def convert(replace_edited_script: bool = False, reader: str = "auto", book_id: str = "") -> None:
    from .ingest import run_ingest

    paths, cfg = _current(book_id)
    if reader and reader != "auto":
        cfg.ingest.reader = reader
    res = run_ingest(paths, cfg, force=replace_edited_script, log=lambda m: print(m))
    s = res.stats
    say(f"Converted with the {res.reader} reader: {s.get('headings', 0)} headings, {s.get('chapters', 0)} chapters, "
        f"{s.get('figures', 0)} figures and {s.get('tables', 0)} tables.", "ok")
    for n in res.notes:
        say(n, "warn" if ("failed" in n or "NOT" in n or "not be available" in n) else "info")
    link(res.script_path, "Script")
    link(res.review_path, "Review report (download it, or open it from Drive, to view it in a browser)")
    _show_review_inline(res.review_path)


def _show_review_inline(path: Path) -> None:
    try:
        text = Path(path).read_text(encoding="utf-8")
        _ipython_display(f"<details><summary><b>Show the review report here</b></summary>"
                         f"<iframe srcdoc=\"{html.escape(text)}\" style='width:100%;height:600px;border:1px solid #ccc'>"
                         f"</iframe></details>")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3. Review and lexicon
# ---------------------------------------------------------------------------
@step
def validate(book_id: str = "") -> bool:
    from .script.review import write_review
    from .script.validator import has_errors, summarise, validate_file

    paths, cfg = _current(book_id)
    level = cfg.structure.chapter_level if isinstance(cfg.structure.chapter_level, int) else None
    _, issues = validate_file(paths.script, level)
    write_review(paths.script, paths.review, cfg)
    ok = not has_errors(issues)
    say(summarise(issues), "ok" if not issues else ("warn" if ok else "error"))
    link(paths.review, "Review report (updated)")
    return ok


@step
def open_file(which: str = "script", book_id: str = "") -> None:
    paths, _ = _current(book_id)
    target = {"script": paths.script, "settings": paths.book_yaml, "lexicon": paths.book_lexicon,
              "suggestions": paths.lexicon_suggestions, "review": paths.review}[which]
    if not target.exists():
        raise LektorError(f"{target.name} does not exist yet.",
                          hint="Run section 2 (Convert to script) first." if which != "lexicon" else
                          "Run section 3's lexicon step first.")
    open_in_editor(target)
    link(target)


# ---------------------------------------------------------------------------
# 3c. Pronunciation (lexicon)
# ---------------------------------------------------------------------------
@step
def prepare_speech(book_id: str = "") -> None:
    from .prepare import run_prepare

    paths, cfg = _current(book_id)
    r = run_prepare(paths, cfg, log=lambda m: None)
    hours = (r.main_chars + r.secondary_chars) / 15 / 3600
    say(f"Prepared {len(r.units)} pieces of speech in {r.n_chapters} chapters (roughly {hours:.1f} hours of audio). "
        f"The lexicon has {r.lexicon_size} entries.", "ok")
    if r.n_suggestions:
        say(f"{r.n_suggestions} words may be mispronounced. Open lexicon_suggestions.csv (below), type how each "
            "should be said in the 'spoken' column for the ones that matter, save, and run this cell again. "
            "Rows you leave empty are ignored.", "info")
    link(paths.lexicon_suggestions, "Lexicon suggestions")


@step
def try_pronunciation(text: str, book_id: str = "") -> None:
    """Show how a sentence will be spoken, after the lexicon and rules."""
    from .prepare import build_normaliser

    paths, cfg = _current(book_id)
    norm = build_normaliser(paths, cfg)
    n = norm.normalise(text)
    say(f"Written: {n.display}\nSpoken:  {n.spoken}", "info")


# ---------------------------------------------------------------------------
# 4. Voice audition
# ---------------------------------------------------------------------------
@step
def audition(chapter: str = "", voices: str = "", seconds: int = 120, book_id: str = "") -> None:
    from .audition import DEFAULT_PRESETS, run_audition

    paths, cfg = _current(book_id)
    presets = [v.strip() for v in voices.split(",") if v.strip()] or DEFAULT_PRESETS
    r = run_audition(paths, cfg, presets, chapter, int(seconds), log=print)
    if r.failed:
        say("Some voices could not be made: " + "; ".join(f"{p}: {m}" for p, m in r.failed.items()), "warn")
    if not r.files:
        raise LektorError("No voice could be rendered.", hint="Check the GPU (section 0) and try again.")
    say(f"The audition is ready: {len(r.files)} voices, labelled {', '.join(r.files)}.", "ok")
    for label, path in r.combined.items():
        link(path, f"Audition, all voices, {label}")
    link(r.folder / "feedback.txt", "Feedback form for the student")
    link(r.folder / "key.json", "Key (which letter is which voice): keep this to yourself")
    say("Send the student the audition files and the feedback form from the audition folder in Drive "
        "(not key.json). When she has chosen, use the next cell.", "info")
    try:
        from IPython.display import Audio, display

        if r.combined.get("1x"):
            display(Audio(str(r.combined["1x"])))
    except Exception:
        pass


@step
def choose_voice(letter: str, secondary_voice: str = "", book_id: str = "") -> None:
    from .audition import choose_voice as _choose

    paths, cfg = _current(book_id)
    preset = _choose(paths, cfg, letter, secondary_voice.strip())
    say(f"Voice {letter.upper()} is {preset}. It is now the main voice in book.yaml. The secondary voice (for "
        f"library additions) is {cfg.voices.secondary}.", "ok")


def list_voices() -> None:
    from .engines.registry import engine_class, load_presets

    rows = []
    for name, p in sorted(load_presets(_home()).items()):
        if p.engine == "mock":
            continue
        cls = engine_class(p.engine)
        rows.append(f"{name:34} {'GPU' if cls.needs_gpu else 'CPU/GPU':8} {p.description}")
    print("\n".join(rows))


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
@step
def smoke_test(voices: str = "", include_docling: bool = True) -> None:
    from .smoke import run_smoke

    presets = [v.strip() for v in voices.split(",") if v.strip()] or None
    report = run_smoke(_home(), presets, log=print, include_docling=include_docling)
    table = report.table()
    out = _home() / "smoke" / "smoke_report.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(table, encoding="utf-8")
    print("\n" + "=" * 30 + " COPY EVERYTHING BELOW THIS LINE " + "=" * 30 + "\n")
    print(table)
    print("\n" + "=" * 30 + " COPY EVERYTHING ABOVE THIS LINE " + "=" * 30)
    say(f"The report is also saved in {str(out).replace('/content/drive/MyDrive', 'My Drive')}, and the audio "
        "samples are next to it so you can listen to them.", "info")


# ---------------------------------------------------------------------------
# 5. Generate audio
# ---------------------------------------------------------------------------
@step
def generate_audio(main_voice: str = "", secondary_voice: str = "", book_id: str = "") -> None:
    from .assemble import run_assemble
    from .config import save_book_config
    from .prepare import run_prepare
    from .synth import run_synth

    paths, cfg = _current(book_id)
    changed = False
    if main_voice and main_voice != cfg.voices.main:
        cfg.voices.main, changed = main_voice, True
    if secondary_voice and secondary_voice != cfg.voices.secondary:
        cfg.voices.secondary, changed = secondary_voice, True
    if changed:
        save_book_config(cfg, paths.book_yaml)
    r0 = run_prepare(paths, cfg, log=lambda m: None)
    say(f"Voices: {cfg.voices.main} (main) and {cfg.voices.secondary} (library additions). "
        f"{len(r0.units)} pieces of speech.", "info")
    r = run_synth(paths, cfg, log=print)
    if r.failed:
        say(f"{len(r.failed)} pieces could not be made. They are listed in qa/synth_failures.json. Run this cell "
            "again to retry them.", "warn")
        return
    say(f"All {r.total} pieces of speech are made. Joining them into chapters...", "ok")
    t = run_assemble(paths, cfg, log=print)
    say(f"Done: {len(t['chapters'])} chapters, {t['total_duration'] / 3600:.2f} hours.", "ok")


# ---------------------------------------------------------------------------
# 7. Package and download
# ---------------------------------------------------------------------------
@step
def package(formats: str = "", book_id: str = "") -> None:
    from .package import run_package

    paths, cfg = _current(book_id)
    wanted = [f.strip() for f in formats.split(",") if f.strip()] or None
    r = run_package(paths, cfg, wanted, log=print)
    for kind, path in r.files.items():
        link(path, kind.upper())
    for n in r.notes:
        say(n, "warn")
    say("The files are in the book's output folder in Drive. Download them from there.", "ok")


def environment_report() -> str:
    """Compact environment details (used by the smoke test)."""
    lines = [f"lektor {__version__}", f"python {platform.python_version()} on {platform.platform()}"]
    g = gpu_info()
    lines.append(f"gpu {g['gpu'] or 'none'} mem={g['memory_gb']}GB driver={g['driver']} cuda_driver={g['cuda_driver']}")
    try:
        import torch  # Colab's own torch, for information only

        lines.append(f"colab torch {torch.__version__} cuda={torch.version.cuda} available={torch.cuda.is_available()}")
    except Exception:
        lines.append("colab torch: not importable")
    for tool in ("uv", "ffmpeg", "java"):
        lines.append(f"{tool}: {'yes' if shutil.which(tool) else 'no'}")
    lines.append(f"colab release: {os.environ.get('COLAB_RELEASE_TAG', 'n/a')}")
    return "\n".join(lines)
