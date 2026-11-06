# Changelog

## M5 DAISY 2.02 and production records (0.5.0)

### Added
- **DAISY 2.02 full text and full audio** (`lektor/package/daisy202.py`), one book per ZIP, generated from the
  timing map: `ncc.html` (title, every heading h1 to h6, every print page as `page-normal`, `page-front` or
  `page-special`, and the required Dublin Core and `ncc:` metadata including `ncc:totalTime`, page counts,
  `ncc:tocItems`, `ncc:depth`, `ncc:files`, `ncc:multimediaType audioFullText`, `ncc:charset`); one XHTML content
  file with an id on every sentence, one SMIL 1.0 file and one MP3 per chapter; `par endsync="last"` linking each
  sentence to its clip in `npt=` form. Clips are contiguous so the pauses between sentences are played.
  Accessibility statement in `dc:description`, acknowledgement in `dc:rights`, narrator in `ncc:narrator`.
- **DAISY checks** (`lektor/package/daisy_check.py`): validation against the RelaxNG schemas of the DAISY
  Pipeline 2 validator for DAISY 2.02 (downloaded once from a pinned commit; not bundled, as they are LGPL-3.0),
  plus structural self checks mirroring the Pipeline's reference and SMIL timing checks: every SMIL referenced,
  every link and id resolves, unique ids, title first, no heading jumps, valid increasing clips inside the audio,
  `timeInThisSmil`, `totalElapsedTime` and `totalTime` consistent, metadata counts match. Report in
  `output/daisy_check.txt`; packaging stops if there are errors. Negative tests break books in five ways and
  check each is caught.
- Sentence boundaries inside multi-sentence units are snapped to the quietest point nearby during assembly, so
  highlighting changes when the voice pauses.
- **Production records** (`lektor/records.py`): one row per packaging run in `MyDrive/Lektor/production_records.csv`
  with date, book id, title, author, publisher, source file, permission basis, formats, engine and voice
  presets, Lektor version and recipient code. No student names.
- Packaging warns when the statement wording is unconfirmed, there is no recipient code, or quality flags remain.
- Notebook section 7 covers DAISY; the smoke test reports the DAISY check. Docs: `docs/testing-daisy.md`
  (Thorium and EasyReader test steps), `docs/formats.md` updated. 217 tests.

### Assumptions and decisions
- **The DAISY Pipeline 2 validator itself (Java) is not run in Colab.** Its RelaxNG schemas are used directly
  with lxml instead, which needs no Java and no 300 MB download. libxml2 lacks RelaxNG's compatibility datatype
  library, so its ID/IDREF types are mapped to XML Schema's when the schemas are loaded; the Schematron rules
  embedded in the schemas are covered by Lektor's own checks. The reference book from the Pipeline's test suite
  validates with this set-up.
- daisy.org could not be reached from the development sandbox; the specification's rules were taken from the
  Pipeline's schemas and test book (github.com/daisy/pipeline-modules, commit 88d39392), which encode it.
- One SMIL and one content file per chapter, with the files at the top of the ZIP and short names
  (`ncc.html`, `c0001.html`, `s0001.smil`, `a0001.mp3`).
- DAISY MP3s are 44.1 kHz mono (MPEG-1 Layer III, the most widely supported), 64 kbps.
- A chapter with no heading of its own (for example "Opening pages") gets an NCC heading from its title,
  because DAISY 2.02 players only reach SMIL files that the NCC links to.
- Footnotes and library notes are read in line and are not marked as skippable DAISY notes yet
  (`ncc:footnotes 0`); marking them as skippable is a possible improvement.

## M4 Quality checks

### Added
- **faster-whisper worker** (`lektor/qa/whisper.py`) in its own `whisper` environment (`faster-whisper==1.2.1` with
  `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*`, put on `LD_LIBRARY_PATH` automatically). Word timestamps are
  kept for milestone 5. Whisper is never primed with the expected text (that would hide errors).
- **Checks** (`lektor/qa/checks.py`): word and character error rates with jiwer after normalising transcript and
  spoken text the same way (the transcript also goes through Lektor's normaliser, so "120 BPM" matches "one hundred
  and twenty B P M"); speaking rate outside the band learned for each voice from its first 300 units (median ±
  a robust spread); internal silences over 1.2 s; clipping; empty or invalid audio; repetition loops.
- **Retries** (`lektor/qa/runner.py`): failing units are made again with new seeds up to `qa.retries` (3) times;
  multi-sentence units that still fail are made sentence by sentence and joined; the best attempt is kept.
  Results are stored in `qa/qa_results.json` with a signature of the audio, so reruns only check what changed.
- **QA report** (`qa/qa_report.html`): summary, then each flagged unit with its id, the text, what the voice was
  given, what Whisper heard, a word diff, the problems and an embedded audio player.
- Notebook section 6: 6a check, 6b accept remaining flags (with a confirmation tick), 6c make particular units
  again, 6d spot check (a random sample to listen to). CLI: `qa` (`--accept-all`, `--regenerate`).
- Assembly now notices when a unit's audio was replaced (chapter fingerprints include an audio signature), so
  retried units reach the chapter files.
- Smoke test: Whisper transcribes every engine's sample and reports a word error rate per voice.
- Tests: checks, retries, splitting, acceptance, regeneration, re-assembly and the report, with a fake
  transcriber (207 tests in total).

### Assumptions and decisions
- **QA runs as its own stage after generation** (section 6), not inside section 5. Whisper loads once; the speech
  engine is loaded only for retries. Both fit on the T4 together (distil-large-v3 in int8_float16 needs about
  1 GB), so retries do not need models to be swapped in and out.
- **Model**: `distil-large-v3` (Systran, MIT, English) on a GPU, `small.en` on CPU (`qa.whisper_model: auto`).
  `large-v3-turbo` was considered, but its Hugging Face repository has been renamed since faster-whisper 1.2.1 was
  released, so the download could break.
- A unit is flagged for words when it has at least two word errors and a WER over `qa.max_wer` (15 %), or, for
  very short units, a character error rate over 50 %. Duration flags need at least 20 units to learn from.
- Retried audio replaces the unit's audio under the same cache key, so reruns of synthesis keep the better take.

## M3 Real voices in Colab

### Added
- **Engine isolation** that is tested here, not only designed: each engine gets its own `uv` virtual
  environment under `/content/lektor_envs/` (`lektor/envs.py`), built the first time it is needed, with
  `--torch-backend auto` choosing the PyTorch build for the GPU driver. A test builds a real uv environment with
  only numpy, confirms it cannot see the main environment's packages, and runs the worker inside it.
- **Worker protocol** (`lektor/engines/worker.py`, `client.py`): a persistent subprocess per engine speaking
  JSON lines over stdin/stdout; audio passed as temporary `.npy` files; library output on stdout cannot corrupt
  the protocol; crashes, timeouts and GPU out-of-memory are reported in plain English.
- **Kokoro** (`kokoro`): all eight British voices and four American ones; works on CPU when there is no GPU.
- **Chatterbox** (`chatterbox`) and **Chatterbox Turbo** (`chatterbox-turbo`), with exaggeration, cfg_weight and
  temperature, seeded ±0.1 exaggeration per paragraph, conditioning prepared once per reference clip.
- **Qwen3-TTS** CustomVoice (`qwen3-custom`: Ryan, Aiden, with a narration instruction), VoiceDesign
  (`qwen3-design`) and Base (`qwen3-base`), loaded with SDPA attention in float16 on the T4.
- **Designed voices, "design once, then clone"** (`lektor/voice_design.py`): British female and male voices
  designed from a written description, saved once to `MyDrive/Lektor/voices/designed/` with a provenance record
  (`synthetic: true`, `real_person: false`), then used by Qwen3-TTS Base and by Chatterbox for every unit. Cache
  keys depend on the design's definition, so they are the same before and after the clip is made.
- **Voice presets** for every engine in `lektor/data/voices.yaml` (extendable from `MyDrive/Lektor/voices.yaml`).
- **Voice audition** (`lektor/audition.py`): a passage of about two minutes from a chosen chapter, rendered with
  up to eight presets, labelled A to H in a shuffled order (key kept separately), one MP3 per voice, a combined
  announced `audition.mp3`, 1.5x and 2x versions with pitch preserving `atempo`, and a feedback form. The
  chosen letter is saved to book.yaml.
- Plain English error when a GPU voice is chosen in a session without a GPU.
- **Notebook** sections 0 to 5 complete (4a audition, 4b record the choice, 4c list voices), and
  **`notebooks/Smoke_Test.ipynb`**: Docling on a generated PDF, voice design, every engine, voice consistency of
  the designed voices, and the whole pipeline with all packagers; prints a compact PASS/FAIL table to paste back.
- Docs: `docs/engines.md` (licences, strengths, memory, speed, how environments work) and
  `docs/adding-an-engine.md`.

### Checked before pinning (November 2026)
- Colab: Python 3.12, torch 2.10 to 2.11. Pinned versions: `kokoro==0.9.4`, `misaki[en]==0.9.4`,
  `chatterbox-tts==0.1.7`, `qwen-tts==0.1.1`, `faster-whisper==1.2.1` (for M4), `docling>=2.102,<3`.
- Every environment's dependencies were resolved against PyPI with `uv pip compile`. This found a real problem:
  the Qwen environment would have got torch 2.15 with torchaudio 2.11 (torchaudio stops at 2.11 and must match
  torch). Kokoro, Qwen and Docling environments therefore pin `torch==2.11.*` (and `torchaudio==2.11.*`), the
  version Colab itself runs on the T4. Chatterbox keeps its own pin of torch 2.6.0 on Python 3.11.
- Model cards: Kokoro British voices use `lang_code='b'`; Kokoro is best on 100 to 200 tokens. Both Chatterbox
  models ship a default voice (`conds.pt`); the brief expected only Turbo to. Chatterbox references must be over
  5 seconds; Turbo ignores exaggeration and cfg_weight. Qwen3-TTS has no British built-in speaker, so British
  voices are designed. The T4 cannot use FlashAttention 2, so SDPA is used everywhere.

### Assumptions and decisions
- Engine environments do not reuse Colab's torch: uv does not count system packages as installed (tested), and
  Chatterbox needs torch 2.6.0. uv's cache means each wheel downloads once per Colab session.
- Models are downloaded into the Colab machine's Hugging Face cache each session (not Drive, which is too slow).
- The default audition set is Kokoro bf_emma, bm_george and bf_isabella, Chatterbox Turbo (designed British
  female), Chatterbox (designed British male), Qwen3-TTS Ryan, and Qwen3-TTS designed British female and male.
- Memory and speed figures in `docs/engines.md` are estimates until the smoke test reports real ones.

### Not yet verified (needs the smoke test in Colab)
- That every engine installs and runs on the current Colab image, and the real-time factors.
- Voice consistency of the designed voices over a long book (the smoke test reports speaker similarity).

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
