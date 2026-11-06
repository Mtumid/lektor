# Output formats and where they play

| Format | File | Best for | Navigation |
|---|---|---|---|
| **DAISY 2.02** full text and full audio | `<book>_daisy.zip` | Dolphin EasyReader (Mac, iOS, Android, Windows), Thorium Reader (Mac, Windows, Linux) | chapters, every heading level, print pages; text highlighted as it is read |
| **M4B** audiobook | `<book>.m4b` | Apple Books (Mac, iPhone), BookPlayer (iOS), Smart AudioBook Player (Android), VLC | chapters |
| **MP3** files | `<book>_mp3.zip` | any player, car stereos, Windows Media Player | one file per chapter, numbered |

Milestone status: M4B and MP3 are made from milestone 2; DAISY arrives in
milestone 5.

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

## Testing on the student's devices

Instructions for checking the DAISY book in EasyReader and Thorium come
with milestone 5.
