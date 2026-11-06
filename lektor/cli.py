"""Command line entry point: ``python -m lektor <stage> [options]``.

Every notebook cell calls one of these commands (through
:mod:`lektor.notebook`), and the tests use them too, so the notebook never
contains pipeline logic of its own.

Common options: ``--home`` (the Lektor folder, default
``/content/drive/MyDrive/Lektor`` or ``$LEKTOR_HOME``) and ``--book``.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .errors import LektorError

COMMANDS: dict[str, tuple[Callable, str]] = {}


def command(name: str, help: str):
    def deco(fn):
        COMMANDS[name] = (fn, help)
        return fn
    return deco


def _book(args):
    from .books import open_book

    return open_book(Path(args.home), args.book)


# ---------------------------------------------------------------------------
@command("new-book", "create a book folder and book.yaml (or update one)")
def cmd_new_book(args) -> int:
    from .books import create_or_update_book

    paths, cfg, msgs = create_or_update_book(
        Path(args.home), args.book, args.title or "", args.author or "", args.publisher or "",
        args.year, args.source or "", args.permission_basis, args.recipient_code or "",
    )
    for m in msgs:
        print(m)
    print(f"Book folder: {paths.root}")
    return 0


@command("ingest", "convert the source document to script/script.md and review.html")
def cmd_ingest(args) -> int:
    from .ingest import run_ingest

    paths, cfg = _book(args)
    if args.reader:
        cfg.ingest.reader = args.reader
    res = run_ingest(paths, cfg, force=args.force)
    print(f"Reader: {res.reader}")
    s = res.stats
    print(f"Found {s.get('headings', 0)} headings ({s.get('chapters', 0)} chapters at level "
          f"{s.get('chapter_level')}), {s.get('figures', 0)} figures, {s.get('tables', 0)} tables.")
    for n in res.notes:
        print("Note:", n)
    print(f"Script: {res.script_path}")
    print(f"Review: {res.review_path}")
    return 0


@command("validate", "check script.md and rebuild review.html")
def cmd_validate(args) -> int:
    from .script.review import write_review
    from .script.validator import has_errors, summarise, validate_file

    paths, cfg = _book(args)
    target = Path(args.script) if args.script else paths.script
    level = cfg.structure.chapter_level if isinstance(cfg.structure.chapter_level, int) else None
    _, issues = validate_file(target, level)
    print(summarise(issues))
    write_review(target, paths.review, cfg)
    print(f"Review updated: {paths.review}")
    return 1 if has_errors(issues) else 0


@command("prepare", "prepare speech: units.jsonl and lexicon_suggestions.csv")
def cmd_prepare(args) -> int:
    from .prepare import run_prepare

    paths, cfg = _book(args)
    _apply_voice_overrides(args, cfg)
    r = run_prepare(paths, cfg)
    print(f"{len(r.units)} units in {r.n_chapters} chapters; {r.n_suggestions} words suggested for the lexicon.")
    print(f"Units: {paths.units}")
    print(f"Suggestions: {paths.lexicon_suggestions}")
    return 0


@command("synth", "make audio for every unit not yet cached (resumable)")
def cmd_synth(args) -> int:
    from .prepare import run_prepare
    from .synth import run_synth

    paths, cfg = _book(args)
    _apply_voice_overrides(args, cfg)
    run_prepare(paths, cfg, log=lambda m: None)
    only = args.only.split(",") if args.only else None
    r = run_synth(paths, cfg, only=only, progress=not args.no_progress)
    print(f"Made {r.made} units; {r.already} were already done; {len(r.failed)} failed.")
    for f in r.failed[:20]:
        print(f"  {f['id']}: {f['problem']}")
    return 1 if r.failed else 0


@command("assemble", "join units into chapter audio and write the timing map")
def cmd_assemble(args) -> int:
    from .assemble import run_assemble
    from .chunk.plan import read_units

    paths, cfg = _book(args)
    t = run_assemble(paths, cfg, read_units(paths.units))
    print(f"{len(t['chapters'])} chapters, {t['total_duration'] / 60:.1f} minutes.")
    return 0


@command("qa", "check every unit with Whisper, retry failures, write qa_report.html")
def cmd_qa(args) -> int:
    from .qa.runner import accept_all, regenerate, run_qa

    paths, cfg = _book(args)
    if args.accept_all:
        print(f"Accepted {accept_all(paths)} flagged units.")
        return 0
    if args.regenerate:
        for r in regenerate(paths, cfg, args.regenerate.split(",")):
            print(f"{r.unit_id}: {'ok' if r.passed else '; '.join(r.flags)}")
        return 0
    s = run_qa(paths, cfg, retry=not args.no_retry, progress=not args.no_progress)
    print(f"Checked {s.checked} of {s.total_units}: {s.passed_first_time} fine first time, {s.fixed_by_retry} fixed "
          f"by retrying, {s.accepted} accepted, {s.still_flagged} still flagged.")
    print(f"Report: {paths.qa_report}")
    return 0


@command("package", "build the output packages (M4B, MP3, DAISY)")
def cmd_package(args) -> int:
    from .package import run_package

    paths, cfg = _book(args)
    formats = args.formats.split(",") if args.formats else None
    r = run_package(paths, cfg, formats)
    for k, v in r.files.items():
        print(f"{k}: {v}")
    for n in r.notes:
        print("Note:", n)
    return 0


def _apply_voice_overrides(args, cfg) -> None:
    if getattr(args, "main", None):
        cfg.voices.main = args.main
    if getattr(args, "secondary", None):
        cfg.voices.secondary = args.secondary


@command("version", "print the Lektor version")
def cmd_version(args) -> int:
    print(f"lektor {__version__}")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    from .books import default_home

    ap = argparse.ArgumentParser(prog="lektor", description="Lektor accessible audiobook pipeline")
    ap.add_argument("--debug", action="store_true", help="show full tracebacks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, (fn, help) in COMMANDS.items():
        p = sub.add_parser(name, help=help)
        p.add_argument("--home", default=str(default_home()))
        p.add_argument("--book", default="")
        if name == "new-book":
            p.add_argument("--title")
            p.add_argument("--author")
            p.add_argument("--publisher")
            p.add_argument("--year")
            p.add_argument("--source")
            p.add_argument("--permission-basis", default="s31B", choices=["s31B", "publisher permission"])
            p.add_argument("--recipient-code")
        if name == "ingest":
            p.add_argument("--force", action="store_true", help="replace an edited script.md (a backup is kept)")
            p.add_argument("--reader", choices=["auto", "docling", "fallback"])
        if name == "validate":
            p.add_argument("--script", help="validate this file instead of script.md")
        if name in ("prepare", "synth"):
            p.add_argument("--main", help="voice preset for the main voice (overrides book.yaml)")
            p.add_argument("--secondary", help="voice preset for the secondary voice")
        if name == "synth":
            p.add_argument("--only", help="comma separated unit ids to make again")
            p.add_argument("--no-progress", action="store_true")
        if name == "package":
            p.add_argument("--formats", help="comma separated: daisy,m4b,mp3")
        if name == "qa":
            p.add_argument("--no-retry", action="store_true")
            p.add_argument("--no-progress", action="store_true")
            p.add_argument("--accept-all", action="store_true")
            p.add_argument("--regenerate", help="comma separated unit ids to make again")
        _extend = EXTRA_ARGS.get(name)
        if _extend:
            _extend(p)
    return ap


EXTRA_ARGS: dict[str, Callable] = {}


def main(argv: Optional[list[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    fn = COMMANDS[args.cmd][0]
    try:
        return fn(args)
    except LektorError as e:
        print(f"Problem: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"What to do: {e.hint}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
