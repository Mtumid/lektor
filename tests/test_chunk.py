"""Sentence splitting, units, boundaries, voices and cache keys."""

import pytest

from lektor.chunk.plan import PlanOptions, Planner, VoiceChoice, chapters_of, read_units, write_units
from lektor.chunk.sentences import split_long, split_sentences
from lektor.script.parser import parse_script
from lektor.speech.lexicon import BUILTIN_DIR, Lexicon, read_lexicon_csv
from lektor.speech.normaliser import Normaliser


@pytest.fixture(scope="module")
def norm():
    return Normaliser(Lexicon(read_lexicon_csv(BUILTIN_DIR / "music_production.csv")))


def planner(norm, limit=120, **opts):
    main = VoiceChoice("mock-main", "mock", limit, "fp-main", {"x": 0.5}, {"x": 0.1})
    sec = VoiceChoice("mock-secondary", "mock", limit, "fp-sec")
    o = PlanOptions(chapter_level=2, front_matter=["Title.", "A statement."], **opts)
    return Planner(norm, main, sec, o)


SCRIPT = """---
chapter-level: 2
---

# Problems of Beginning

## 1. Start With a Beat

[[page 1]]

First sentence, e.g. this one. Second sentence at 120 BPM. Third sentence ends here.[^1]

Another paragraph that continues [[page 2]] on the next page. And more.

[^1]: The footnote.

[[figure id=fig-1 page=2]]
Caption: Figure 1.1 A drum pattern.
Description:
[[/figure]]

[[figure id=fig-2 page=2]]
Caption:
Description:
[[/figure]]

[[table id=t1 page=2]]
| Element | Level |
|---|---|
| Kick | -6 dB |
Caption:
[[/table]]

[[note]]Added by the library.[[/note]]

### A subheading

- item one
- item two

## 2. Next Strategy

Last words.

[[skip]]
Contents ..... 1
[[page 3]]
[[/skip]]

## 3. Third

Final.
"""


def plan(norm, text=SCRIPT, **kw):
    s, issues = parse_script(text)
    assert not [i for i in issues if i.severity == "error"]
    return planner(norm, **kw).plan(s)


def test_sentences_do_not_split_abbreviations():
    s = split_sentences("Use a compressor, e.g. a VCA. Dr. Smith said so. Fig. 3 shows it.", ["e.g.", "Dr.", "Fig."])
    assert s == ["Use a compressor, e.g. a VCA.", "Dr. Smith said so.", "Fig. 3 shows it."]


def test_sentences_keep_decimals_and_lexicon_terms():
    s = split_sentences("Set 2.5 dB at 44.1 kHz. Then stop.", [])
    assert s == ["Set 2.5 dB at 44.1 kHz.", "Then stop."]


def test_split_long_at_clauses():
    text = "one two three, four five six; seven eight nine ten eleven twelve"
    parts = split_long(text, 20)
    assert all(len(p) <= 20 or " " not in p for p in parts)
    assert " ".join(parts) == text


def test_units_structure(norm):
    units = plan(norm)
    kinds = [u.kind for u in units]
    assert kinds[:2] == ["frontmatter", "frontmatter"]
    assert all(u.role == "secondary" for u in units if u.kind in ("frontmatter", "figure", "note", "footnote", "table"))
    assert all(u.role == "main" for u in units if u.kind in ("heading", "paragraph", "list_item", "table_row"))
    # units never cross paragraphs: each unit's sentences come from one block
    ids = [u.id for u in units]
    assert len(ids) == len(set(ids))
    for u in units:
        assert len(u.spoken) <= 120 or len(u.sentences) == 1
    # chapters: front matter, then "Problems of Beginning: 1. Start With a Beat", then 2 and 3
    chs = chapters_of(units)
    assert [t for _, t, _ in chs] == ["About this audiobook", "Problems of Beginning: 1. Start With a Beat",
                                      "2. Next Strategy", "3. Third"]
    # boundaries
    by_text = {u.display: u for u in units}
    assert by_text["Problems of Beginning"].boundary == "heading"
    assert by_text["Last words."].boundary == "chapter"
    assert units[-1].boundary == "chapter"
    sub = next(u for u in units if u.display == "A subheading")
    prev = units[units.index(sub) - 1]
    assert prev.boundary == "section"
    assert sub.heading_level == 3


def test_pages_attach_to_sentences(norm):
    units = plan(norm)
    first_para = next(u for u in units if u.kind == "paragraph")
    assert first_para.sentences[0].pages == ["1"]
    cont = next(u for u in units if "continues" in u.display)
    assert cont.sentences[0].pages == ["2"]
    third = next(u for u in units if u.display == "Final.")
    # page 3 sat inside a skip block before chapter 3's heading
    heading3 = next(u for u in units if u.display == "3. Third")
    assert heading3.sentences[0].pages == ["3"]
    assert third.page == "3"


def test_footnote_end_of_paragraph(norm):
    units = plan(norm)
    i = next(k for k, u in enumerate(units) if "Third sentence" in u.display)
    assert units[i + 1].kind == "footnote" and units[i + 1].display == "Note: The footnote."
    assert sum(1 for u in units if u.kind == "footnote") == 1


def test_footnote_end_of_chapter_and_skip(norm):
    units = plan(norm, footnotes="end_of_chapter")
    ch1 = [u for u in units if u.chapter == 1]
    assert ch1[-1].kind == "footnote" and ch1[-1].boundary == "chapter"
    units = plan(norm, footnotes="skip")
    assert not any(u.kind == "footnote" for u in units)


def test_figures_and_tables(norm):
    units = plan(norm)
    figs = [u for u in units if u.kind == "figure"]
    assert figs[0].display == "Figure 1.1 A drum pattern."
    assert figs[1].display == "Figure on page 2."
    units = plan(norm, uncaptioned_figures="silent")
    assert len([u for u in units if u.kind == "figure"]) == 1
    rows = [u for u in plan(norm) if u.kind == "table_row"]
    assert rows[0].display == "Element: Kick; Level: -6 dB."
    assert rows[0].spoken == "Element: Kick; Level: minus six decibels."
    big = plan(norm, max_table_cells=2)
    notice = [u for u in big if u.kind == "table"]
    assert notice[0].display == "Table. A table is omitted here; see page 2 of the print book."
    assert not any(u.kind == "table_row" for u in big)


def test_display_and_spoken(norm):
    units = plan(norm)
    u = next(u for u in units if "120 BPM" in u.display)
    s = next(s for s in u.sentences if "120 BPM" in s.display)
    assert s.display == "Second sentence at 120 BPM."
    assert s.spoken == "Second sentence at one hundred and twenty B P M."
    assert "[^1]" not in u.display


def test_seeded_variation_is_reproducible(norm):
    a = plan(norm)
    b = plan(norm)
    assert [u.params for u in a] == [u.params for u in b]
    mains = [u for u in a if u.role == "main"]
    assert all(0.4 - 1e-9 <= u.params["x"] <= 0.6 + 1e-9 for u in mains)
    assert len({u.params["x"] for u in mains}) > 1


def test_cache_keys_only_change_for_edited_units(norm):
    a = plan(norm)
    edited = SCRIPT.replace("Another paragraph that continues", "Another paragraph, edited, that continues")
    b = plan(norm, text=edited)
    ka = {u.display: u.cache_key for u in a}
    changed = [u.display for u in b if ka.get(u.display) != u.cache_key]
    assert len(changed) == 1 and "edited" in changed[0]


def test_cache_key_depends_on_voice(norm):
    a = plan(norm)
    p = planner(norm)
    p.voices["main"] = VoiceChoice("other", "mock", 120, "fp-other", {"x": 0.5}, {"x": 0.1})
    s, _ = parse_script(SCRIPT)
    b = p.plan(s)
    assert {u.cache_key for u in a if u.role == "main"}.isdisjoint({u.cache_key for u in b if u.role == "main"})
    assert {u.cache_key for u in a if u.role == "secondary"} == {u.cache_key for u in b if u.role == "secondary"}


def test_units_jsonl_round_trip(norm, tmp_path):
    units = plan(norm)
    p = tmp_path / "units.jsonl"
    write_units(units, p)
    again = read_units(p)
    assert [u.to_json() for u in again] == [u.to_json() for u in units]


def test_long_sentence_is_split_into_parts(norm):
    long = "This is a long sentence, " + ", ".join(f"with clause number {i}" for i in range(12)) + "."
    units = plan(norm, text=f"## 1. One\n\n{long}\n", limit=80)
    parts = [s for u in units if u.kind == "paragraph" for s in u.sentences]
    assert len(parts) > 1 and all(s.part >= 1 for s in parts)
    assert len({s.sid for s in parts}) == 1
    assert " ".join(s.display for s in parts) == long
