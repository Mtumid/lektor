# Pronunciation: lexicons and rules

Synthetic voices guess how to say unusual words, and often guess wrong:
`BPM`, `MIDI`, `4:1`, `F#m7`, `TR-808`, `DeSantis`. Lektor fixes this
before the text reaches the voice, so the fix works with every voice.

Only the **spoken** text changes. The text shown in the DAISY book stays
exactly as the author wrote it.

## Lexicon files

A lexicon is a CSV file (open it in Google Sheets, Excel or Colab's editor).
The first line must be:

```
written,spoken,case_sensitive,whole_word,is_regex,notes
```

| Column | Meaning |
|---|---|
| written | the text as it appears in the book |
| spoken | what the voice should say. `=` means "say it as written" (to switch off an earlier entry) |
| case_sensitive | `yes` or `no`. Leave blank for "yes if *written* has a capital letter" |
| whole_word | `yes` (default) or `no` (also match inside longer words) |
| is_regex | `no` (default) or `yes` for a Python regular expression (advanced) |
| notes | anything you like |

Examples:

```
written,spoken,case_sensitive,whole_word,is_regex,notes
DeSantis,dee SAN tiss,,,,author's name
Ableton,AY-bul-tun,,,,
Push2,Push two,,,,
```

Respell words the way you would explain them to a person ("AY-bul-tun").
Listen to the result in the voice audition and adjust.

## Where lexicons live, and which wins

Lektor reads lexicons in this order. When two entries have the same
*written* text, **the later one wins**:

1. built in defaults inside Lektor (a handful of general entries)
2. shared lexicons for every book: `My Drive/Lektor/lexicons/*.csv`.
   Lektor puts its draft **music_production.csv** here the first time it
   runs. Edit it freely; Lektor never overwrites it.
3. the book's `script/lexicon_suggestions.csv` (rows with a *spoken* form)
4. the book's own `script/lexicon.csv` (create it if you need one)

## Suggestions for each book

Notebook section 3c (*Find words that may be mispronounced*) writes
`script/lexicon_suggestions.csv`, listing, most frequent first:

- words in capitals the lexicon does not know (`LUFS`, `TR-8S`)
- words mixing letters with digits or symbols (`Push2`)
- words not in an English dictionary (`DeSantis`, `glitchy`); British
  spellings are recognised

Each row has an example sentence and print page. Type a *spoken* form for
the ones that matter and save. You do not need to copy them anywhere: rows
with a spoken form are used straight away, and running the cell again keeps
what you typed.

Section 3d shows how any sentence will be spoken, so you can check a fix
without making audio.

## Rules Lektor applies by itself

Some cases depend on context, so they are handled by rules rather than the
lexicon (`lektor/speech/rules.py` and `lektor/speech/normaliser.py`):

| Written | Spoken | Note |
|---|---|---|
| `ii–V–I`, `I-IV-V` | two five one, one four five | Roman numeral progressions |
| `the V chord` | the five chord | a Roman numeral before "chord" |
| `I think` | I think | the pronoun is left alone |
| `in A`, `the A string` | in ay, the ay string | a capital A mid-sentence is a note |
| `A good idea` | A good idea | the article at the start of a sentence |
| `4/4`, `7/8` | four four, seven eight | time signatures |
| `3/4 of the bar` | three quarters of the bar | a fraction when "of" follows |
| `1/16 notes`, `1/8T` | sixteenth notes, eighth note triplet | note values |
| `4:1` | four to one | ratios (but `3:30` is a time) |
| `F#m7`, `Bbmaj7`, `C/E` | F sharp minor seven, B flat major seven, C over E | chord symbols |
| `-6 dB`, `+3 dB` | minus six decibels, plus three decibels | signed values |
| `20 Hz–20 kHz` | twenty hertz to twenty kilohertz | ranges |
| `£5.99`, `50%`, `1990s`, `21st` | five pounds ninety-nine, fifty per cent, nineteen nineties, twenty-first | |
| `e.g.`, `i.e.`, `etc.`, `p. 47` | for example, that is, et cetera, page forty-seven | |
| URLs and email addresses | ableton dot com | or "web link" (`speech.urls: link` in book.yaml) |
| `PROBLEMS OF BEGINNING` | Problems of beginning | ordinary words in capitals are not spelled out |
| `Part II` (in a heading) | Part two | |

Your lexicon entries are applied **before** these rules, so you can always
override a rule for a particular book.

> **The seed lexicon is a draft.** `music_production.csv` was written
> without hearing the voices. Please review it, and listen for these words
> in the audition.
