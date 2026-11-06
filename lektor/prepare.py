"""Speech preparation stage: script -> units.jsonl (+ lexicon suggestions).

Rerunning is cheap and safe. Units whose spoken text, voice and settings
are unchanged keep the same cache key, so after an edit to the script or the
lexicon only the affected units are synthesised again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .chunk.plan import PlanOptions, Planner, Unit, VoiceChoice, write_units
from .config import BookConfig, BookPaths, append_log
from .engines.registry import engine_class, get_preset
from .errors import ScriptError
from .script.parser import read_script
from .script.validator import check_script, has_errors
from .speech.lexicon import load_for_book
from .speech.normaliser import Normaliser, SpeechOptions
from .speech.suggest import find_terms, write_suggestions


@dataclass
class PrepareResult:
    units: list[Unit]
    n_chapters: int
    n_suggestions: int
    lexicon_size: int
    main_chars: int
    secondary_chars: int


def voice_choice(preset_name: str, home: Path) -> VoiceChoice:
    preset = get_preset(preset_name, home)
    cls = engine_class(preset.engine)
    params = {**cls.default_params, **preset.params}
    limit = int(params.pop("max_chars", 0) or cls.unit_limit())
    return VoiceChoice(name=preset.name, engine=preset.engine, limit=limit,
                       fingerprint=preset.fingerprint(home), params=params, vary=dict(preset.vary))


def front_matter_sentences(cfg: BookConfig) -> list[str]:
    lines = [f"{cfg.title}."]
    if cfg.author:
        lines.append(f"By {cfg.author}.")
    if cfg.publisher:
        lines.append(f"Published by {cfg.publisher}" + (f", {cfg.year}." if cfg.year else "."))
    lines.append(cfg.statement("accessibility"))
    lines.append(cfg.statement("acknowledgement"))
    lines.append(cfg.statement("synthetic_voice"))
    return lines


def chapter_level_for(cfg: BookConfig, script) -> int:
    if isinstance(cfg.structure.chapter_level, int):
        return cfg.structure.chapter_level
    try:
        return int(script.meta.get("chapter-level") or 1)
    except ValueError:
        return 1


def build_normaliser(paths: BookPaths, cfg: BookConfig) -> Normaliser:
    return Normaliser(load_for_book(paths), SpeechOptions(urls=cfg.speech.urls))


def plan_units(paths: BookPaths, cfg: BookConfig, normaliser: Optional[Normaliser] = None,
               main: Optional[str] = None, secondary: Optional[str] = None) -> tuple[list[Unit], Normaliser, object]:
    if not paths.script.exists():
        raise ScriptError("There is no script yet.", hint="Run section 2 (Convert to script) first.")
    script, issues = read_script(paths.script)
    issues += check_script(script)
    if has_errors(issues):
        first = next(i for i in issues if i.severity == "error")
        n = sum(1 for i in issues if i.severity == "error")
        raise ScriptError(f"script.md has {n} error(s), for example: {first}",
                          hint="Run 'Check the script again' (section 3) to see them all, fix them, then run this again.")
    norm = normaliser or build_normaliser(paths, cfg)
    home = paths.lektor_home
    planner = Planner(
        norm,
        voice_choice(main or cfg.voices.main, home),
        voice_choice(secondary or cfg.voices.secondary, home),
        PlanOptions(
            chapter_level=chapter_level_for(cfg, script),
            footnotes=cfg.speech.footnotes,
            endnotes=cfg.speech.endnotes,
            uncaptioned_figures=cfg.speech.uncaptioned_figures,
            max_table_cells=cfg.speech.max_table_cells,
            front_matter=front_matter_sentences(cfg),
        ),
    )
    return planner.plan(script), norm, script


def run_prepare(paths: BookPaths, cfg: BookConfig, log=print) -> PrepareResult:
    units, norm, script = plan_units(paths, cfg)
    write_units(units, paths.units)
    sentences = ((s.display, u.page) for u in units if u.role == "main" for s in u.sentences)
    rows = find_terms(sentences, norm)
    n_sugg = write_suggestions(rows, paths.lexicon_suggestions)
    chapters = len({u.chapter for u in units})
    main_chars = sum(len(u.spoken) for u in units if u.role == "main")
    sec_chars = sum(len(u.spoken) for u in units if u.role == "secondary")
    append_log(paths, "prepare", f"{len(units)} units in {chapters} chapters; lexicon {len(norm.lexicon)} entries "
                                 f"({norm.lexicon.digest()}); {len(rows)} suggestions; voices {cfg.voices.main} / "
                                 f"{cfg.voices.secondary}")
    return PrepareResult(units, chapters, len(rows), len(norm.lexicon), main_chars, sec_chars)
