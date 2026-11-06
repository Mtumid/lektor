"""``qa/qa_report.html``: what is still flagged, with audio to listen to.

A single self-contained page: a summary, then one entry per flagged unit
with its id (to copy into the notebook's regenerate box), the text as
written, what Whisper heard, a word diff, the problems found and an audio
player (the audio is embedded, so the page works when downloaded).
"""

from __future__ import annotations

import base64
import html
import subprocess
import tempfile
from pathlib import Path

from ..cache import UnitCache
from ..config import BookConfig, BookPaths
from .checks import word_diff_html

MAX_EMBEDDED = 300

CSS = """
:root { color-scheme: light dark; --fg:#1d1d1f; --bg:#fff; --muted:#5f6368; --line:#d9d9de; --chip:#f1f3f4;
        --del:#b3261e; --ins:#0b57d0; --ok:#137333; --warn:#8a4b00; }
@media (prefers-color-scheme: dark) { :root { --fg:#e8e8ea; --bg:#151517; --muted:#a0a3a8; --line:#36363b;
        --chip:#232327; --del:#ff8a80; --ins:#8ab4f8; --ok:#81c995; --warn:#f0b35a; } }
body { font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg);
       max-width: 62rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid var(--line) } .muted { color: var(--muted) }
.stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: .6rem }
.stat { background: var(--chip); border-radius: .5rem; padding: .6rem .8rem } .stat b { display:block; font-size:1.4rem }
.unit { border: 1px solid var(--line); border-radius: .6rem; padding: .8rem 1rem; margin: 1rem 0 }
.unit h3 { margin: 0 0 .3rem; font-size: 1rem } code { background: var(--chip); padding: 0 .3rem; border-radius: .25rem }
.flag { color: var(--warn) } del { color: var(--del) } ins { color: var(--ins); text-decoration: underline }
.label { color: var(--muted); font-size: .85rem; text-transform: uppercase; letter-spacing: .03em; margin-top: .5rem }
audio { width: 100%; margin-top: .5rem }
"""


def _mp3_data_uri(path: Path) -> str:
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "a.mp3"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1",
                            "-b:a", "48k", str(out)], check=True, capture_output=True)
            return "data:audio/mpeg;base64," + base64.b64encode(out.read_bytes()).decode()
    except Exception:
        return ""


def render(cfg: BookConfig, units, results: dict, cache: UnitCache, summary) -> str:
    e = html.escape
    h = [f"<!doctype html><html lang='en-GB'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>QA: {e(cfg.title)}</title>"
         f"<style>{CSS}</style></head><body>"]
    h.append(f"<h1>Quality report: {e(cfg.title)}</h1>")
    h.append("<p class='muted'>Every piece of speech was transcribed by Whisper and compared with the text. "
             "Pieces that did not match were made again automatically. What is listed below still needs a "
             "human ear: listen, then either accept it or ask for it to be made again (notebook section 6).</p>")
    h.append("<div class='stats'>")
    for label, value in [("Pieces checked", f"{summary.checked} of {summary.total_units}"),
                         ("Fine first time", summary.passed_first_time), ("Fixed by retrying", summary.fixed_by_retry),
                         ("Accepted by you", summary.accepted), ("Still flagged", summary.still_flagged)]:
        h.append(f"<div class='stat'><b>{e(str(value))}</b>{e(label)}</div>")
    h.append("</div>")
    flagged = [u for u in units if u.cache_key in results and not results[u.cache_key]["passed"]
               and not results[u.cache_key].get("accepted")]
    h.append(f"<h2>Still flagged ({len(flagged)})</h2>")
    if not flagged:
        h.append("<p>Nothing is flagged.</p>")
    for n, u in enumerate(flagged):
        r = results[u.cache_key]
        audio = _mp3_data_uri(cache.path(u.cache_key)) if n < MAX_EMBEDDED and cache.has(u.cache_key) else ""
        h.append("<div class='unit'>")
        h.append(f"<h3><code>{e(u.id)}</code> &middot; chapter {u.chapter}: {e(u.chapter_title)}"
                 f"{' &middot; page ' + e(u.page) if u.page else ''}</h3>")
        h.append("<div class='label'>Problems</div>" + "".join(f"<div class='flag'>{e(f)}</div>" for f in r["flags"]))
        h.append(f"<div class='label'>Text (as written, script.md line {u.line})</div><div>{e(u.display)}</div>")
        h.append(f"<div class='label'>What the voice was given</div><div>{e(u.spoken)}</div>")
        h.append(f"<div class='label'>What Whisper heard</div><div>{e(r.get('transcript', ''))}</div>")
        if r.get("transcript"):
            h.append(f"<div class='label'>Difference</div><div>{word_diff_html(u.spoken, r['transcript'])}</div>")
        h.append(f"<div class='muted'>Tries: {r.get('attempts', 1)}{', made sentence by sentence' if r.get('split') else ''}; "
                 f"voice {e(u.preset)}</div>")
        if audio:
            h.append(f"<audio controls preload='none' src='{audio}'></audio>")
        h.append("</div>")
    h.append("</body></html>")
    return "\n".join(h)


def write_report(paths: BookPaths, cfg: BookConfig, units, results: dict, cache: UnitCache, summary) -> Path:
    paths.qa_dir.mkdir(parents=True, exist_ok=True)
    paths.qa_report.write_text(render(cfg, units, results, cache, summary), encoding="utf-8")
    return paths.qa_report
