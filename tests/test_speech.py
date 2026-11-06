"""Normaliser, lexicon and music rules."""

import csv
import io

import pytest

from lektor.speech.lexicon import BUILTIN_DIR, ConfigError, Lexicon, load_lexicon, read_lexicon_csv
from lektor.speech.normaliser import Normaliser, SpeechOptions, roman_to_int
from lektor.speech.suggest import classify, find_terms, write_suggestions
from lektor.speech.wordlist import english_words, is_english


@pytest.fixture(scope="module")
def norm():
    entries = read_lexicon_csv(BUILTIN_DIR / "_defaults.csv") + read_lexicon_csv(BUILTIN_DIR / "music_production.csv")
    return Normaliser(Lexicon(entries))


@pytest.fixture(scope="module")
def plain():
    return Normaliser(Lexicon())


CASES = [
    # tempo, gain, units
    ("Set the tempo to 120 BPM.", "Set the tempo to one hundred and twenty B P M."),
    ("Try 128bpm.", "Try one hundred and twenty-eight B P M."),
    ("Pull the kick down to -6 dB.", "Pull the kick down to minus six decibels."),
    ("Boost by +3 dB.", "Boost by plus three decibels."),
    ("A cut of −12 dB", "A cut of minus twelve decibels"),
    ("Set it to 1 dB.", "Set it to one decibel."),
    ("A 10 ms attack and 1 ms release.", "A ten milliseconds attack and one millisecond release."),
    ("Ms Jones arrived.", "Ms Jones arrived."),
    ("Roll off below 30 Hz and above 16 kHz.", "Roll off below thirty hertz and above sixteen kilohertz."),
    ("Cut 20 Hz–20 kHz.", "Cut twenty hertz to twenty kilohertz."),
    ("A 24 dB/oct slope.", "A twenty-four decibels per octave slope."),
    ("Transpose +7 st.", "Transpose plus seven semitones."),
    ("Detune -12 ct.", "Detune minus twelve cents."),
    # ratios
    ("Use a 4:1 ratio.", "Use a four to one ratio."),
    ("A 1.5:1 ratio.", "A one point five to one ratio."),
    ("Limit at ∞:1.", "Limit at infinity to one."),
    # times
    ("At 3:30 pm.", "At three thirty p m."),
    ("A 3:05 intro.", "A three oh five intro."),
    # time signatures vs fractions
    ("Write in 4/4.", "Write in four four."),
    ("Try 7/8 or 6/8.", "Try seven eight or six eight."),
    ("It is in 3/4 time.", "It is in three four time."),
    ("Leave 3/4 of the bar empty.", "Leave three quarters of the bar empty."),
    ("Write 5/4 bars.", "Write five four bars."),
    # note values
    ("Program 1/16 notes.", "Program sixteenth notes."),
    ("Use 1/8 notes and 1/4 notes.", "Use eighth notes and quarter notes."),
    ("A 1/8T hi hat.", "A eighth note triplet hi hat."),
    ("A 1/8D delay.", "A dotted eighth delay."),
    ("Fill 1/4 of the loop.", "Fill a quarter of the loop."),
    ("Quantise to 1/16.", "Quantise to sixteenth."),
    ("Then 1/32 rolls.", "Then thirty-second rolls."),
    # note names and chords
    ("Play C# then Db.", "Play C sharp then D flat."),
    ("In Bb major.", "In B flat major."),
    ("An F#m chord.", "An F sharp minor chord."),
    ("Then F#m7 and Bbmaj7.", "Then F sharp minor seven and B flat major seven."),
    ("A Cmaj7 to G7.", "A C major seven to G seven."),
    ("Try C/E.", "Try C over E."),
    ("Play Am then Em.", "Play ay minor then E minor."),
    ("Am I right?", "Am I right?"),
    ("Use C♯ and B♭.", "Use C sharp and B flat."),
    ("Middle C is C4.", "Middle C is C four."),
    ("Tune A4 to 440 Hz.", "Tune ay four to four hundred and forty hertz."),
    # the note A versus the article
    ("A good beat helps.", "A good beat helps."),
    ("Play in A.", "Play in ay."),
    ("The key of A minor.", "The key of ay minor."),
    ("Tune the A string.", "Tune the ay string."),
    ("Play an A, then a C.", "Play an ay, then a C."),
    ("A minor is sad.", "Ay minor is sad."),
    ("Figure 2 A drum loop.", "Figure two A drum loop."),
    ("Tip: A short loop works.", "Tip: A short loop works."),
    # Roman numerals
    ("The ii–V–I progression.", "The two five one progression."),
    ("Try I-IV-V.", "Try one four five."),
    ("A vi–IV–I–V loop.", "A six four one five loop."),
    ("Play ii7–V7–Imaj7.", "Play two seven five seven one major seven."),
    ("Resolve to the I chord.", "Resolve to the one chord."),
    ("The V chord leads home.", "The five chord leads home."),
    ("I think I like it.", "I think I like it."),
    # drum machines and acronyms
    ("The TR-808 and the TB-303.", "The T R eight oh eight and the T B three oh three."),
    ("Load the 808 kick.", "Load the eight oh eight kick."),
    ("Open your DAW and a MIDI clip.", "Open your D A W and a middy clip."),
    ("Add EQ, an LFO and a VST.", "Add E Q, an L F O and a V S T."),
    ("Use sidechain compression.", "Use side chain compression."),
    ("Record at 24/96.", "Record at twenty-four ninety-six."),
    # general
    ("It cost £5.99.", "It cost five pounds ninety-nine."),
    ("Only £1 or 50p.", "Only one pound or 50p."),
    ("It cost $10 and €3.", "It cost ten dollars and three euros."),
    ("A £2m budget.", "A two million pounds budget."),
    ("About 50% louder.", "About fifty per cent louder."),
    ("The 1st, 2nd, 3rd and 21st takes.", "The first, second, third and twenty-first takes."),
    ("In 1995 and 2015.", "In nineteen ninety-five and twenty fifteen."),
    ("In 2005, then 2000 Hz.", "In two thousand and five, then two thousand hertz."),
    ("The 1980s and '90s.", "The nineteen eighties and nineties."),
    ("Over 1,200 samples.", "Over one thousand two hundred samples."),
    ("At 0.5 dB.", "At zero point five decibels."),
    ("Pages 12-15.", "Pages twelve to fifteen."),
    ("See p. 47 and pp. 50–52.", "See page forty-seven and pages fifty to fifty-two."),
    ("See Fig. 3.", "See figure three."),
    ("Use a compressor, e.g. a VCA type.", "Use a compressor, for example a V C A type."),
    ("Reverb, i.e. space.", "Reverb, that is space."),
    ("Drums, bass, etc.", "Drums, bass, et cetera."),
    ("Rock vs. pop.", "Rock versus pop."),
    ("Bass & drums.", "Bass and drums."),
    ("Filter → reverb.", "Filter to reverb."),
    ("Make it 2x faster.", "Make it two times faster."),
    ("Use 1920×1080.", "Use one thousand nine hundred and twenty by one thousand and eighty."),
    ("Input/output.", "Input or output."),
    ("And/or both.", "And or both."),
    ("Track #3.", "Track number three."),
    ("Visit https://www.ableton.com/en/live/ today.", "Visit ableton dot com today."),
    ("Visit ableton.com today.", "Visit ableton dot com today."),
    ("Write to info@ableton.com.", "Write to an address at ableton dot com."),
    ("PROBLEMS OF BEGINNING", "Problems of beginning"),
    ("NOTE: this matters.", "Note: this matters."),
    ("Ask the BBC.", "Ask the BBC."),
    ("Track 007.", "Track oh oh seven."),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_normaliser(norm, text, expected):
    assert norm(text) == expected


def test_display_text_is_untouched(norm):
    n = norm.normalise("Set 120 BPM.")
    assert n.display == "Set 120 BPM."
    assert n.lexicon_hits and n.lexicon_hits[0][0] == "BPM"


def test_urls_link_mode():
    n = Normaliser(Lexicon(), SpeechOptions(urls="link"))
    assert n("Go to https://example.com/x now.") == "Go to web link now."


def test_heading_roman_numerals(plain):
    assert plain("Part II", heading=True) == "Part two"
    assert plain("Chapter XIV: The End", heading=True) == "Chapter fourteen: The End"
    assert plain("IV. Mixing", heading=True) == "Four. Mixing"
    assert plain("Part II") == "Part II"  # only in headings
    assert roman_to_int("XLII") == 42 and roman_to_int("IIII") is None


def test_numbered_heading_is_capitalised(plain):
    assert plain("12. Use a Reference Track", heading=True) == "Twelve. Use a Reference Track"


# ---------------------------------------------------------------------------
# Lexicon files
# ---------------------------------------------------------------------------
def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["written", "spoken", "case_sensitive", "whole_word", "is_regex", "notes"])
        w.writerows(rows)
    return path


def test_load_order_later_wins(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    write_csv(shared / "a.csv", [["Ableton", "Able ton", "", "", "", ""], ["DAW", "door", "", "", "", ""]])
    book = write_csv(tmp_path / "book.csv", [["Ableton", "AY-bull-tun", "", "", "", ""]])
    lex = load_lexicon(shared, [book])
    n = Normaliser(lex)
    assert n("Ableton makes a DAW.") == "AY-bull-tun makes a door."


def test_case_and_whole_word(tmp_path):
    p = write_csv(tmp_path / "l.csv", [
        ["Live", "Ableton Live", "yes", "yes", "no", ""],
        ["synth", "sinth", "no", "no", "no", "partial words too"],
        ["colour", "color", "", "", "", "blank case_sensitive -> insensitive"],
    ])
    n = Normaliser(Lexicon(read_lexicon_csv(p)))
    assert n("Open Live, a live set.") == "Open Ableton Live, a live set."
    assert n("Synths and synthesis.") == "Sinths and sinthesis."
    assert n("Colour and COLOUR.") == "Color and color."


def test_regex_entries_and_equals(tmp_path):
    p = write_csv(tmp_path / "l.csv", [
        [r"Push\s?(\d)", r"Push \1", "yes", "yes", "yes", ""],
        ["BPM", "=", "", "", "", "say it as written"],
    ])
    lex = Lexicon(read_lexicon_csv(BUILTIN_DIR / "music_production.csv") + read_lexicon_csv(p))
    n = Normaliser(lex)
    assert n("Use Push2 at 120 BPM.") == "Use Push 2 at one hundred and twenty BPM."


def test_bad_lexicon_rows_are_reported(tmp_path):
    p = write_csv(tmp_path / "l.csv", [["(unclosed", "x", "", "", "yes", ""], ["a", "b", "maybe", "", "", ""]])
    with pytest.raises(ConfigError) as e:
        read_lexicon_csv(p)
    assert "line 2" in e.value.message and "line 3" in e.value.message


def test_rows_without_spoken_are_ignored(tmp_path):
    p = write_csv(tmp_path / "l.csv", [["Glitch", "", "", "", "", ""]])
    assert read_lexicon_csv(p) == []


def test_seed_lexicon_size():
    entries = read_lexicon_csv(BUILTIN_DIR / "music_production.csv")
    assert len(entries) >= 150


# ---------------------------------------------------------------------------
# Unknown terms
# ---------------------------------------------------------------------------
def test_british_spellings():
    wl = english_words()
    for w in ["centre", "colour", "organise", "analyse", "catalogue", "travelled", "programme"]:
        assert is_english(w, wl), w


def test_classify():
    wl = english_words()
    assert classify("LUFS", wl) == "capitals"
    assert classify("TR-8S", wl) == "letters with digits or symbols"
    assert classify("Push2", wl) == "letters with digits or symbols"
    assert classify("DeSantis", wl) == "not in dictionary"
    assert classify("music", wl) is None
    assert classify("IV", wl) is None


def test_find_terms_and_merge(tmp_path, norm):
    sents = [("DeSantis uses the TR-8S and the TR-8S.", "4"), ("Set 120 BPM in your DAW.", "5"),
             ("Glitchy sounds by DeSantis.", "6")]
    rows = find_terms(sents, norm)
    words = [r["written"] for r in rows]
    assert words[0] in ("DeSantis", "TR-8S")
    assert "BPM" not in words and "DAW" not in words  # covered by the lexicon
    assert {"DeSantis", "TR-8S"} <= set(words)
    ds = next(r for r in rows if r["written"] == "DeSantis")
    assert ds["count"] == "2" and ds["page"] == "4"
    out = tmp_path / "s.csv"
    write_suggestions(rows, out)
    # the librarian fills in a spoken form; regenerating keeps it
    text = out.read_text().replace("DeSantis,,", "DeSantis,dee SAN tiss,")
    out.write_text(text)
    write_suggestions(find_terms(sents, norm), out)
    again = list(csv.DictReader(io.StringIO(out.read_text())))
    assert next(r for r in again if r["written"] == "DeSantis")["spoken"] == "dee SAN tiss"
    # and the filled-in row works as a lexicon entry
    lex = Lexicon(read_lexicon_csv(out))
    assert Normaliser(lex)("DeSantis wrote it.") == "Dee SAN tiss wrote it."
