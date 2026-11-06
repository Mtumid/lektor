# Testing the DAISY book before sending it

Lektor checks every DAISY book automatically (`output/daisy_check.txt`):
against the DAISY Pipeline 2 schemas for DAISY 2.02, and for broken links,
missing files, timing errors and wrong metadata. That proves the files are
well formed. It cannot prove that a player behaves well, so please try each
new book on a real player before it goes to the student. It takes about ten
minutes.

## 1. Get the file onto a device

Download `<book>_daisy.zip` from the book's `output` folder in Google Drive.
**Do not unzip it**: both players open the ZIP directly.

## 2. Thorium Reader (Mac or Windows, free)

1. Install Thorium from <https://thorium.edrlab.org> (or ask IT, if your
   laptop is locked; the student can also do this check on her Mac).
2. Drag the ZIP into Thorium's window (or *Import a publication*).
3. Open the book and check:
   - **It plays**: press play. The text should highlight sentence by
     sentence, in step with the voice.
   - **Headings**: open the table of contents. You should see the parts and
     the numbered strategies, nested.
   - **Pages**: use *Go to page* and type a page number from the print
     book, for example 47. It should jump to that page.
   - **Speed**: try 1.5x and 2x.

## 3. Dolphin EasyReader (Mac, iOS, Android or Windows)

This is the student's app, so this check matters most.

1. Put the ZIP somewhere the device can reach it (for example Google Drive
   or email to yourself), then open it with EasyReader (*Share* or *Open
   in* EasyReader). On a Mac you can add it from EasyReader's file menu.
2. Check:
   - **Playback with highlighting**, as above.
   - **Navigation by heading level**: set the navigation level to 1 and
     use next/previous (it should move between parts), then level 2 (it
     should move between strategies).
   - **Navigation by page**: set the navigation unit to *Page*, step
     forward and back, and use *Go to page*.
   - **Bookmarks**: add one, close the book, open it again: it should
     resume where you left it.
   - **The two voices**: figure captions and library notes should be in
     the second voice.
   - **The start**: the book opens with the title, the accessibility
     statement and the line saying the narration is synthetic.

## 4. If something is wrong

Write down what you did, what you expected and what happened, the book id,
and the player and device (for example "EasyReader 11 on iPhone"). Attach
`daisy_check.txt` and `production_log.txt` and send them to the developer.

## What the DAISY book contains

| File | What it is |
|---|---|
| `ncc.html` | the table of contents: title, every heading, every print page |
| `c0001.html` ... | the text, one file per chapter |
| `s0001.smil` ... | timing: which sentence is spoken when |
| `a0001.mp3` ... | the audio, one file per chapter |
| `lektor.css` | a small stylesheet for the text |
