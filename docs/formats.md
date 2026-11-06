# Output formats and where they play

| Format | File | Best for | Navigation |
|---|---|---|---|
| **DAISY 2.02** full text and full audio | `<book>_daisy.zip` | Dolphin EasyReader (Mac, iOS, Android, Windows), Thorium Reader (Mac, Windows, Linux) | chapters, every heading level, print pages; text highlighted as it is read |
| **M4B** audiobook | `<book>.m4b` | Apple Books (Mac, iPhone), BookPlayer (iOS), Smart AudioBook Player (Android), VLC | chapters |
| **MP3** files | `<book>_mp3.zip` | any player, car stereos, Windows Media Player | one file per chapter, numbered |

All three are made by notebook section 7 (and `python -m lektor package`).

## Technical details

- Audio is mono, 24 kHz, normalised to -18 LUFS integrated with a true peak
  ceiling of -1.5 dBTP (both configurable in `book.yaml` under `audio`).
- M4B: AAC at 64 kbps, chapter markers from the timing map, cover art (the
  first page of a PDF, or `package.cover_image`), and tags: title, author
  (artist), publisher, year, narrator (composer: "Synthetic voice: engine,
  preset"), and the accessibility statement in the comment.
- MP3: 64 kbps, ID3v2.3 tags with track numbers (`3/12`), file names like
  `03 Problems of Beginning 1. Start With a Beat.mp3`.
- The first chapter is always the spoken front matter ("About this
  audiobook"): title, author, publisher, the accessibility statement, the
  acknowledgement and a line saying the narration is synthetic.
- DAISY 2.02: full text and full audio (`ncc:multimediaType audioFullText`).
  One XHTML content file, one SMIL file and one MP3 (mono, 64 kbps,
  44.1 kHz) per chapter; every sentence has an id and a SMIL `par` linking
  it to its audio clip (`clip-begin`/`clip-end` in `npt=` form). Clips are
  contiguous, so pauses are played. The NCC has the title, every heading
  (h1 to h6, as in the script) and every print page (`page-normal`,
  `page-front` for roman numerals, `page-special` otherwise), with the
  required Dublin Core and `ncc:` metadata. The accessibility statement is
  in `dc:description`, the acknowledgement in `dc:rights`, the synthetic
  narrator in `ncc:narrator`. All files sit at the top of the ZIP with short
  lower case names, so nothing ever needs renaming.
- Each DAISY book is checked before it is zipped: against the DAISY
  Pipeline 2 RelaxNG schemas for DAISY 2.02 (downloaded once from a pinned
  commit), plus structural checks for links, ids, timing and metadata. The
  report is `output/daisy_check.txt`.

## Testing on the student's devices

See [testing-daisy.md](testing-daisy.md) for a ten minute check in Thorium
Reader and Dolphin EasyReader.

## Later

EPUB 3 with Media Overlays will be another writer on top of the same
timing map (`audio/timing.json`), which already records every sentence,
heading and page with its time.
