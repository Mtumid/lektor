# Changelog

## M2 Speech preparation to audio, with the mock engine

### Added
- **Normaliser** (`lektor/speech/normaliser.py`): display text and spoken text are kept separately for every
  sentence. Handles numbers, ordinals, years and decades, currencies, percentages, ranges, times, signed values,
  URLs and email addresses (domain only, or "web link"), abbreviations, symbols, Roman numerals in headings,
  and ordinary words written in capitals. Protected placeholders stop later passes changing finished text.
- **Lexicons** (`lektor/speech/lexicon.py`): CSV with `written, spoken, case_sensitive, whole_word, is_regex,
  notes`; load order built in, shared Drive lexicons, the book's suggestions, the book's lexicon (later wins).
  A draft **music production lexicon** of about 170 entries is copied to `MyDrive/Lektor/lexicons/` on first run.
- **Music rules** (`lektor/speech/rules.py`): Roman numeral progressions and chords versus the pronoun "I", the
  note A versus the article, time signatures versus fractions, note values and triplets, ratios versus clock
  times, chord symbols with accidentals and qualities, slash chords, notes with octaves.
- **Unknown term detector** writing `lexicon_suggestions.csv` (capitals, letters with digits or symbols, words
  not in the dictionary, British spellings accepted), ranked by frequency with an example and page. The
  librarian's entries are kept when it is regenerated, and rows with a spoken form are used straight away.
- **Chunking** (`lektor/chunk/`): sentence splitting that never breaks abbreviations; units of whole sentences
  up to the engine's limit, never crossing a paragraph; long sentences split at clauses; stable ids; boundary
  types; voice roles; seeded per-paragraph parameter variation; cache keys; `units.jsonl`.
- Figures ("Figure." plus caption, optional description, or "Figure on page N"), tables (row by row with
  column headers, or a spoken notice for large tables), footnotes and endnotes (end of paragraph, end of
  chapter or skip), library notes and the spoken front matter, all library-added text in the secondary voice.
- **Engine interface, registry, voice presets** (`lektor/engines/`, `lektor/data/voices.yaml`) and the
  deterministic **MockEngine**.
- **Synthesis** (`lektor/synth.py`) with a character-weighted progress bar and time remaining, and a
  **unit cache** (`lektor/cache.py`) written locally and synced to Drive in zip batches (every 50 units or
  2 minutes), restored on start.
- **Assembly** (`lektor/assemble/`): resampling, silence trimming, pauses by boundary with seeded ±15 % jitter,
  10 ms fades, loudness normalisation to -18 LUFS with a -1.5 dBTP true-peak limiter, and the **timing map**
  (`audio/timing.json`). Unchanged chapters are not rebuilt.
- **M4B** (AAC mono 64 kbps, chapters, cover from PDF page 1, tags) and **MP3 ZIP** (one file per chapter,
  ID3 tags with track numbers) packaging through ffmpeg.
- CLI: `prepare`, `synth`, `assemble`, `package`. Notebook: 3c/3d pronunciation, 5 generate audio, 7 package.
- Docs: `docs/lexicon.md`, `docs/formats.md`. Tests: about 130 more (183 in total).

### Assumptions and decisions
- **Seeds come from the text, not the unit id.** The brief suggested deriving seeds from unit ids. Ids shift
  when a paragraph is inserted, which would change the cache key of every later unit and force the whole rest of
  the book to be made again. Seeds are therefore derived from the unit's spoken text (and paragraph variation
  from the paragraph's text) plus the preset: still exactly reproducible, and an edit only regenerates the
  units it touches. Pause jitter is keyed on the unit id, which only affects assembly (cheap).
- **Loudness is normalised per chapter**, each to the same target, rather than measuring the whole book at
  once (a ten hour book does not fit in Colab's memory as one array). The effect for the listener is the same:
  every chapter at -18 LUFS.
- The note A is spoken "ay". A capital A in the middle of a sentence is treated as a letter or note; at the
  start of a sentence as the article unless a musical word follows. Captions such as "Figure 2 A drum loop"
  keep the article. Please listen for this in the audition.
- Chapters: a heading at or above the chapter level starts a new chapter; a part heading directly followed by
  a chapter heading shares its chapter ("Problems of Beginning: 1. Start With a Beat"). Text before the first
  chapter heading becomes "Opening pages". Chapter 0 is the spoken front matter.
- Table rows are read in the main voice (they are the author's words); the "Table. N rows, N columns."
  introduction and the omitted-table notice are in the secondary voice.
- Sentence times inside a multi-sentence unit are estimated from text length for now; milestone 5 refines
  them for DAISY highlighting.
- The English wordlist comes from `pyspellchecker` (MIT).

## M1 Foundations (0.1.0)

### Added
- Package skeleton with a `python -m lektor <stage>` command line (`new-book`, `ingest`, `validate`).
- `book.yaml` settings with defaults for every later stage, friendly validation errors and warnings
  for unknown keys; Drive folder layout (`BookPaths`) and a per-book production log.
- The script format (`script.md`): parser, writer and validator with plain English, line-numbered
  messages. The writer and parser round-trip without loss (tested, including 200 random cases).
- Ingest for PDF (Docling, with a pdfplumber fallback), EPUB (Lektor's own reader), DOCX (Docling or
  python-docx), HTML (Docling or BeautifulSoup), TXT and Markdown.
- Shared clean-up for all formats: running headers, footers and printed page numbers removed;
  line end hyphenation repaired; paragraphs joined across page breaks with an inline page marker;
  heading levels decided by heuristics plus `structure.heading_rules`; contents and index put in
  skip blocks; stable figure and table ids.
- Print page numbers from PDF page labels, from page numbers printed in the margins, or from
  `structure.page_offset`; EPUB page break markers.
- Review report (`review.html`): statistics, heading outline, validator problems, and things worth
  a look (leftover headers/footers, broken hyphenation, odd characters, very short or long
  paragraphs, figures without captions, tables, pages with no text, numbering gaps).
- Re-running ingest never overwrites an edited `script.md`; it writes `script.new.md` instead.
- `lektor/envs.py`: isolated `uv` environments for Docling and (later) each speech engine.
- Notebook sections 0 to 3, generated by `tools/build_notebooks.py`.
- Test fixtures generated at test time (PDF with reportlab, EPUB, DOCX, HTML, TXT); 50+ tests;
  GitHub Actions workflow.
- Documentation: README, script format, accessibility and copyright, engines (licences), CLAUDE.md.

### Assumptions and decisions
- **EPUB is read by Lektor's own reader**, not Docling. Docling 2.102+ can read EPUB, but its HTML
  path drops EPUB page break markers, which we need for print page navigation. `ebooklib` was
  avoided because it is AGPL.
- **A fallback PDF reader** (pdfplumber, MIT) exists because Docling's layout models cannot be
  downloaded in the development sandbox or CI, and as a safety net if Docling fails in Colab. In
  Colab, `auto` uses Docling.
- **Engine environments are self-contained** (no `--system-site-packages`). Tested: `uv` does not
  treat system packages as satisfying requirements, and Chatterbox pins torch 2.6.0 anyway. uv's
  cache means torch is downloaded once per Colab session and hard-linked into each environment.
  `--torch-backend auto` picks the CUDA build that matches the driver.
- Colab runtime checked at the time of writing: Python 3.12, torch 2.10 to 2.11. Kokoro requires
  Python below 3.13, so engine environments use Python 3.12.
- Heading heuristic: numbered headings ("12. Title", "Chapter 3") are chapters; "Part" headings
  and plain headings that introduce a run of numbered headings sit one level above; other plain
  headings sit below. The librarian can override with `structure.heading_rules` or by editing `#`s.
- Pages are marked only when a print page number is known. A PDF with neither page labels nor
  printed numbers gets no page navigation (the review says so) unless `page_offset` is set.
- The accessibility statement wording is a placeholder until confirmed by the copyright lead.
