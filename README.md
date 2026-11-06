# Lektor

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/OWNER/lektor/blob/main/notebooks/Lektor.ipynb)

Lektor turns a book (PDF, EPUB, Word, HTML, plain text or Markdown) into an
**accessible audiobook** for print disabled students, read by natural
sounding open source voices. It makes:

- a **DAISY 2.02** book with full text and full audio, for Dolphin
  EasyReader and Thorium Reader, so the listener can move by chapter, by
  heading and by print page number, with the text highlighted as it is read;
- an **M4B** audiobook with chapters, for Apple Books and most audiobook apps;
- a **ZIP of MP3 files**, one per chapter, for anything else.

Everything runs in your web browser, in Google Colab, with your files in
Google Drive. You do not need to install anything or write any code.

It was built for the alternative formats service at the University of West
London library.

## How it works, in seven steps

The notebook has one section for each step. Each section has a short
explanation and a single cell: fill in the boxes, then press ▶.

| Step | What happens | What you do |
|---|---|---|
| 0. Setup | Connects Drive, installs Lektor | Choose a T4 GPU (*Runtime > Change runtime type*), run the cell |
| 1. Start or open a book | Creates the book's folder and settings | Fill in the title, author, source file and recipient code |
| 2. Convert to script | Reads the book into an editable script and a review report | Run it |
| 3. Review | You check the script and fix conversion mistakes; you add pronunciations | Work through the review report |
| 4. Voice audition | The student listens to anonymous voice samples A to H and picks one | Send her the audition, record her choice |
| 5. Generate audio | Lektor reads the whole book aloud (several hours; resumable) | Run it, and run it again after any disconnect |
| 6. Quality review | Lektor transcribes its own audio and flags sentences that went wrong | Listen to the flagged ones, accept or regenerate |
| 7. Package | Builds the DAISY, M4B and MP3 files and records the copy | Download and send to the student |

> **Status**: all five prototype milestones are written and tested on CPU
> (217 automated tests). Real voices and Docling still need to be confirmed
> on Colab with the smoke test; see [CHANGELOG.md](CHANGELOG.md).
>
> **Checking a new version**: open `notebooks/Smoke_Test.ipynb` in Colab,
> run both cells, and send the table it prints to the developer.

## Getting started

1. Put the book file in your Google Drive, for example in
   `My Drive/Lektor/incoming/`.
2. Click the **Open in Colab** badge above.
3. *Runtime* menu > *Change runtime type* > **T4 GPU** > *Save*.
4. Run section **0. Setup**. Allow access to Google Drive when asked.
5. Fill in section **1** and run it. Then run section **2**.
6. Open the review report and the script (section 3).

If the Lektor repository is private, add your GitHub token once: click the
key icon in Colab's left sidebar, add a secret called `GITHUB_TOKEN`, paste
a token with read access to the repository, and switch on *Notebook access*.

## Where your files live

```
My Drive/Lektor/
  lexicons/                  pronunciation lists shared by all books (you can edit these)
  production_records.csv     the library's record of every accessible copy made
  books/<book id>/
    book.yaml                settings for this book
    source/                  the original file
    script/script.md         the text to be read: edit this to fix mistakes
    script/review.html       the review report
    audition/                voice samples for the student
    output/                  the finished DAISY, M4B and MP3 files
    production_log.txt       what was run, and when
```

## FAQ

**Colab disconnected in the middle. Have I lost everything?**
No. Everything Lektor finishes is saved to Drive as it goes. Run section 0
again, then the section you were on. It carries on where it stopped; at most
a few minutes of work are repeated.

**I have no GPU / Colab says no GPU is available.**
Steps 1 to 3 work without one. The Kokoro voice also works without a GPU,
just more slowly. The other voices need one: try again later, as free GPUs
come and go during the day.

**How do I fix a mispronounced word?**
Add a line to the book's lexicon (`script/lexicon.csv`) or to a shared
lexicon in `My Drive/Lektor/lexicons/`, with the word as written and how to
say it, for example `Ableton,AY-bul-ton`. Then run step 5 again: only the
sentences containing that word are regenerated. See
[docs/lexicon.md](docs/lexicon.md).

**How do I fix a heading, a stray page header or a split word?**
Edit `script/script.md` (section 3 opens it for you), save, and run
*Check the script again*. The format is explained in
[docs/script-format.md](docs/script-format.md).

**Which voices are there?**
Kokoro (eight British and several American voices; works without a GPU),
Chatterbox and Chatterbox Turbo, and Qwen3-TTS (two built-in English
speakers, plus British voices *designed* from a written description). Run
section 4c for the full list. No voice is a copy of a real person.

**What does the quality report mean?**
Lektor transcribes its own audio with Whisper and compares it with the
text. "Words" means the voice said something different (often a
mispronunciation, so consider a lexicon entry); "silence", "repetition" and
"duration" mean the voice stumbled. Listen to each one: accept it, make it
again, or fix the text.

**How do I start a second book?**
Run section 1 again with a new book id (a short name with no spaces, such as
`music-theory-basics`). Use the old id to go back to the first book.

**Can a student's name go in Lektor?**
No. Use a recipient code that you can look up in the library's own records.

## Documentation

- [Script format](docs/script-format.md): how to read and edit `script.md`
- [Lexicon](docs/lexicon.md): fixing pronunciation
- [Formats](docs/formats.md): which app plays which file
- [Testing the DAISY book](docs/testing-daisy.md): a ten minute check in Thorium and EasyReader
- [Accessibility and copyright](docs/accessibility-and-copyright.md): what Lektor may change, statements, records
- [Engines](docs/engines.md): the voices, their licences and requirements
- [Adding an engine](docs/adding-an-engine.md): for developers
- [CLAUDE.md](CLAUDE.md): constraints for developers (and Claude Code)
- [CHANGELOG.md](CHANGELOG.md): what changed, and the assumptions made

## For developers

```bash
pip install -e ".[test]"
pytest -q
python -m lektor --help
```

Notebooks are generated by `tools/build_notebooks.py`. Book content, audio
and page images must never be committed (see `.gitignore` and `CLAUDE.md`).
