"""Generate notebooks/Lektor.ipynb and notebooks/Smoke_Test.ipynb.

The notebooks are generated so that every cell follows the same pattern
(a short explanation, then one form-driven cell that calls
``lektor.notebook``) and so that changes are easy to review.

Run:  python tools/build_notebooks.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "Mtumid/lektor"  # GitHub owner/name; also used by the README badge


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str, title: str) -> dict:
    src = text.strip("\n")
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {"cellView": "form", "id": title.lower().replace(" ", "_")[:40]},
        "outputs": [],
        "source": src.splitlines(keepends=True),
    }


def notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": [], "gpuType": "T4", "toc_visible": True},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


SETUP_CODE = f'''
#@title 0. Setup (run this first, every time) {{ display-mode: "form" }}
#@markdown Leave these as they are unless the developer asks you to test a different version.
branch = "main" #@param {{type:"string"}}
repository = "{REPO}" #@param {{type:"string"}}
lektor_folder = "/content/drive/MyDrive/Lektor" #@param {{type:"string"}}

import os, subprocess, sys, importlib
from google.colab import drive
drive.mount("/content/drive")

token = ""
try:
    from google.colab import userdata
    token = userdata.get("GITHUB_TOKEN") or ""
except Exception:
    token = ""  # no secret: the repository is assumed to be public

url = f"https://x-access-token:{{token}}@github.com/{{repository}}.git" if token else f"https://github.com/{{repository}}.git"
subprocess.run(["rm", "-rf", "/content/lektor"])
r = subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", branch, url, "/content/lektor"],
                   capture_output=True, text=True)
if r.returncode != 0:
    msg = (r.stderr or r.stdout).replace(token, "***") if token else (r.stderr or r.stdout)
    print("Could not download Lektor from GitHub.")
    print("If the repository is private, add a GITHUB_TOKEN secret (key icon in the left sidebar) and allow")
    print("this notebook to use it. Also check the branch name.")
    print("Details:", msg.strip()[-500:])
elif not os.path.exists("/content/lektor/pyproject.toml"):
    print(f"The branch '{{branch}}' of {{repository}} does not contain Lektor yet (it is empty).")
    print("Type the name of a branch that does, for example m5-daisy-records, in the 'branch' box and run")
    print("this cell again. Once the pull requests are merged, 'main' will work.")
else:
    p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", "/content/lektor"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print("Installing Lektor failed:", p.stderr[-1500:])
    else:
        if "/content/lektor" not in sys.path:
            sys.path.insert(0, "/content/lektor")
        importlib.invalidate_caches()
        for m in [m for m in sys.modules if m == "lektor" or m.startswith("lektor.")]:
            del sys.modules[m]
        os.environ["LEKTOR_HOME"] = lektor_folder
        from lektor import notebook as nb
        nb.setup(lektor_folder)
'''

START_CODE = '''
#@title 1. Start or open a book { display-mode: "form" }
#@markdown **Book id**: a short name with no spaces, for example `making-music`. Use the same id to reopen a book.
book_id = "making-music" #@param {type:"string"}
title = "Making Music: 74 Creative Strategies for Electronic Music Producers" #@param {type:"string"}
author = "Dennis DeSantis" #@param {type:"string"}
publisher = "Ableton" #@param {type:"string"}
year = "2015" #@param {type:"string"}
#@markdown **Source file**: the path of the PDF (or EPUB, DOCX, ...) in Drive. In the Files panel, right-click the file and choose *Copy path*.
source_file = "/content/drive/MyDrive/Lektor/incoming/Making_Music.pdf" #@param {type:"string"}
permission_basis = "publisher permission" #@param ["publisher permission", "s31B"]
#@markdown **Recipient code**: your own reference for the student. Never type the student's name.
recipient_code = "" #@param {type:"string"}
#@markdown Leave the statements empty to keep the current wording (see book.yaml).
accessibility_statement = "" #@param {type:"string"}
acknowledgement = "" #@param {type:"string"}
nb.start_book(book_id, title, author, publisher, year, source_file, permission_basis, recipient_code,
              accessibility_statement, acknowledgement)
'''

CONVERT_CODE = '''
#@title 2. Convert to script { display-mode: "form" }
#@markdown Tick this only if you want to throw away your edits to script.md and start again (a backup is kept).
replace_edited_script = False #@param {type:"boolean"}
#@markdown `auto` uses Docling for PDFs (the first run installs it, which takes a few minutes).
reader = "auto" #@param ["auto", "docling", "fallback"]
nb.convert(replace_edited_script, reader)
'''

REVIEW_CODE = '''
#@title 3a. Open a file in the editor { display-mode: "form" }
which = "script" #@param ["script", "settings", "review"]
nb.open_file(which)
'''

VALIDATE_CODE = '''
#@title 3b. Check the script again after editing { display-mode: "form" }
nb.validate()
'''


LEXICON_CODE = '''
#@title 3c. Find words that may be mispronounced { display-mode: "form" }
nb.prepare_speech()
'''

TRY_CODE = '''
#@title 3d. Hear how a sentence will be read (as text) { display-mode: "form" }
sentence = "Set the tempo to 120 BPM and sidechain the pad at -6 dB with a 4:1 ratio." #@param {type:"string"}
nb.try_pronunciation(sentence)
'''

AUDITION_CODE = '''
#@title 4a. Make the voice audition { display-mode: "form" }
#@markdown **Chapter**: a number, or a few words of a strategy's title. Empty: the first chapter with enough text.
chapter = "" #@param {type:"string"}
#@markdown **Voices**: leave empty for the default eight, or list presets separated by commas (see 4c).
voices = "" #@param {type:"string"}
seconds = 120 #@param {type:"integer"}
nb.audition(chapter, voices, seconds)
'''

CHOOSE_CODE = '''
#@title 4b. Record the student's choice { display-mode: "form" }
letter = "A" #@param ["A", "B", "C", "D", "E", "F", "G", "H"]
#@markdown Optional: a different voice for library additions (captions, notes). Empty keeps the current one.
secondary_voice = "" #@param {type:"string"}
nb.choose_voice(letter, secondary_voice)
'''

VOICES_CODE = '''
#@title 4c. List all voice presets { display-mode: "form" }
nb.list_voices()
'''

GENERATE_CODE = '''
#@title 5. Generate audio { display-mode: "form" }
#@markdown Leave the voices empty to use the ones in book.yaml (the voice chosen in the audition).
#@markdown `mock-main` and `mock-secondary` are test tones for checking the pipeline without a GPU.
main_voice = "" #@param {type:"string"}
secondary_voice = "" #@param {type:"string"}
nb.generate_audio(main_voice, secondary_voice)
'''

QA_CODE = '''
#@title 6a. Check the audio { display-mode: "form" }
nb.quality_check()
'''

ACCEPT_CODE = '''
#@title 6b. Accept all remaining flags { display-mode: "form" }
#@markdown Only after listening to the flagged pieces in the report.
i_have_listened_and_they_are_fine = False #@param {type:"boolean"}
nb.accept_flags(i_have_listened_and_they_are_fine)
'''

REGEN_CODE = '''
#@title 6c. Make particular pieces again { display-mode: "form" }
#@markdown Copy the ids from the report, separated by commas, for example `c003-p0012-u01, c004-p0002-u02`.
unit_ids = "" #@param {type:"string"}
nb.regenerate_units(unit_ids)
'''

SPOT_CODE = '''
#@title 6d. Spot check: listen to a random sample { display-mode: "form" }
how_many = 10 #@param {type:"integer"}
nb.spot_check(how_many)
'''

PACKAGE_CODE = '''
#@title 7. Package and download { display-mode: "form" }
#@markdown Leave empty for all formats in book.yaml, or type for example `m4b,mp3`.
formats = "" #@param {type:"string"}
nb.package(formats)
'''


def lektor_notebook() -> dict:
    cells = [
        md(f"""
# Lektor: accessible audiobooks

Run the sections in order. Each section has a short explanation and one cell. You never need to change any
code: fill in the boxes on the right of a cell, then press the ▶ button (or Ctrl+Enter).

**If Colab disconnects** (it does, especially on long jobs): nothing is lost. Run section 0 again, then the
section you were on. Lektor picks up where it stopped.

All files live in your Google Drive under `My Drive/Lektor`.
"""),
        md("""
## 0. Setup

Connects Google Drive and installs Lektor. Run it at the start of every Colab session.

You need a **GPU** for most voices: open the *Runtime* menu, choose *Change runtime type*, pick *T4 GPU* and
press *Save* before running this cell. Converting a book works without one.
"""),
        code(SETUP_CODE, "setup"),
        md("""
## 1. Start or open a book

Creates the book's folder in Drive and its settings file (`book.yaml`). If the book already exists, this
reopens it and updates any boxes you have filled in.

The **permission basis** records why the library may make this copy: either the publisher gave permission,
or section 31B of the Copyright, Designs and Patents Act. It goes into the production record.
"""),
        code(START_CODE, "start_book"),
        md("""
## 2. Convert to script

Reads the book and writes **script.md**, the text that will be read aloud, plus a **review report** listing
things worth checking. Lektor removes running headers, footers and page numbers, rejoins words split at
line ends, and keeps headings, print page numbers, figures, tables and footnotes.

It never changes the author's words. If you edit script.md and run this again, your edits are kept (the new
conversion is saved as script.new.md instead) unless you tick the box.
"""),
        code(CONVERT_CODE, "convert"),
        md("""
## 3. Review the script

1. Open the review report (it is in the book's `script` folder in Drive; double-click to download and open
   it in your browser), and work through *Worth a human look*. Each item gives a line number in script.md.
2. Open **script.md** with the cell below. It opens in the editor pane on the right. Save with Ctrl+S.
3. Run *Check the script again* to make sure your edits are valid.

The format is explained in `docs/script-format.md` in the Lektor repository. The essentials:
`#` marks headings (more `#` signs means a lower level), `[[page 47]]` marks a print page,
`[[note]]...[[/note]]` adds a library note read in the second voice, and `[[skip]]...[[/skip]]` keeps text
in the file without reading it aloud.
"""),
        code(REVIEW_CODE, "open_file"),
        code(VALIDATE_CODE, "validate"),
        md("""
### Pronunciation

Lexicons tell the voice how to say words it would get wrong: `BPM` is read "B P M", `MIDI` "middy", `4:1`
"four to one". Lektor ships a draft music production lexicon (in `My Drive/Lektor/lexicons/`), and the cell
below lists every other word in this book that might be a problem, most frequent first, in
`lexicon_suggestions.csv`. Open it (File panel, double-click), fill in the **spoken** column for the words
that matter, save, and run the cell again. See `docs/lexicon.md` for details.
"""),
        code(LEXICON_CODE, "prepare_speech"),
        code(TRY_CODE, "try_pronunciation"),
        md("""
## 4. Voice audition

Before the whole book is made, the student chooses the voice. This cell reads the same passage (about two
minutes) with several voices and labels them **A to H in a random order**, so she is not influenced by names.
It makes one file per voice, a combined `audition.mp3` with each voice announced, faster versions (1.5x and
2x, because some voices fall apart when sped up), and a feedback form. Everything is in the book's
`audition` folder in Drive. Send her the MP3 files and `feedback.txt`; keep `key.json` to yourself.

The first run installs each voice engine (a few minutes each) and needs a **GPU** for most of them.
British voices for Chatterbox and Qwen3-TTS are *designed* from a written description the first time (no
real person's voice is used); the designed clips are saved in `My Drive/Lektor/voices/designed/`.
"""),
        code(AUDITION_CODE, "audition"),
        code(CHOOSE_CODE, "choose_voice"),
        code(VOICES_CODE, "list_voices"),
        md("""
## 5. Generate audio

Reads the whole book aloud with the voices in book.yaml (the one chosen in the audition). This takes from
under an hour (Kokoro) to many hours (Chatterbox, Qwen3-TTS) for a long book, so it may need several Colab
sessions. Progress is saved to Drive as it goes: **if Colab disconnects, run section 0 again and then this
cell again**, and it carries on where it stopped. After an edit to the script or the lexicon, only the
affected sentences are made again.

Most voices need a **GPU** (Kokoro also works without one, more slowly). The first run in each Colab
session installs the voice engine, which takes a few minutes.
"""),
        code(GENERATE_CODE, "generate_audio"),
        md("""
## 6. Quality review

Lektor listens to its own audio: every piece of speech is transcribed by Whisper and compared with the text.
It also looks for long silences, clipping, repeated phrases and speech that is much too fast or slow. Pieces
that fail are made again automatically (up to three times). The report lists what is still flagged, with
an audio player for each: listen, then accept them (6b) or make particular ones again (6c). If a flag is a
real mistake in the text, fix it in script.md and run sections 5 and 6 again.

The spot check (6d) plays a random sample, because automatic checks cannot judge everything.
"""),
        code(QA_CODE, "quality_check"),
        code(ACCEPT_CODE, "accept_flags"),
        code(REGEN_CODE, "regenerate_units"),
        code(SPOT_CODE, "spot_check"),
        md("""
## 7. Package and download

Builds the finished files in the book's `output` folder:

- **DAISY 2.02** (`..._daisy.zip`): full text and full audio for Dolphin EasyReader and Thorium Reader, with
  navigation by heading and by print page, and the text highlighted as it is read. Lektor checks it against
  the DAISY 2.02 rules before zipping it (`daisy_check.txt`).
- **M4B** audiobook with chapters, for Apple Books and audiobook apps.
- **MP3** files, one per chapter, in a ZIP.

A row is added to `My Drive/Lektor/production_records.csv` (the library's record of accessible copies).
Before sending the files, check them on a device as described in `docs/testing-daisy.md`.
"""),
        code(PACKAGE_CODE, "package"),
    ]
    return notebook(cells)


SMOKE_CODE = '''
#@title 1. Run the smoke test { display-mode: "form" }
#@markdown Leave empty to test every engine. This takes about 30 to 60 minutes the first time
#@markdown (each engine is installed and its model downloaded).
voices = "" #@param {type:"string"}
include_docling = True #@param {type:"boolean"}
nb.smoke_test(voices, include_docling)
'''


def smoke_notebook() -> dict:
    cells = [
        md("""
# Lektor smoke test

This checks that every part of Lektor works on Colab: Docling, each voice engine, the designed voices, and
the packaging. It does not touch your books.

1. *Runtime* menu > *Change runtime type* > **T4 GPU** > *Save*.
2. Run cell 0, then cell 1.
3. When it finishes, copy the table between the two marker lines and paste it back to the developer.
   (It is also saved in `My Drive/Lektor/smoke/smoke_report.txt`, with audio samples you can listen to.)
"""),
        code(SETUP_CODE.replace("0. Setup (run this first, every time)", "0. Setup"), "setup"),
        code(SMOKE_CODE, "smoke_test"),
    ]
    return notebook(cells)


def main() -> None:
    out = ROOT / "notebooks"
    out.mkdir(exist_ok=True)
    (out / "Lektor.ipynb").write_text(json.dumps(lektor_notebook(), indent=1, ensure_ascii=False) + "\n")
    (out / "Smoke_Test.ipynb").write_text(json.dumps(smoke_notebook(), indent=1, ensure_ascii=False) + "\n")
    print("wrote", out / "Lektor.ipynb", "and", out / "Smoke_Test.ipynb")


if __name__ == "__main__":
    main()
