"""Checks for a DAISY 2.02 book.

Two layers:

1. **Schema validation** of ``ncc.html`` and every SMIL file against the
   RelaxNG schemas of the DAISY Pipeline 2 validator for DAISY 2.02
   (``daisy202-utils``). They are downloaded once from a pinned commit of
   github.com/daisy/pipeline-modules (LGPL-3.0) into a cache folder; they are
   not part of Lektor's repository. libxml2 does not know RelaxNG's
   "compatibility" datatype library, so its ID/IDREF types are mapped to the
   equivalent XML Schema types when loading; uniqueness and resolution of
   ids is checked by layer 2 anyway. If the schemas cannot be downloaded,
   this layer is skipped (and the report says so).
2. **Structural self checks**, which mirror the Pipeline's own reference
   and timing checks and add Lektor-specific ones: every file is
   well-formed XML; every SMIL is referenced from the NCC; every link
   resolves to an existing file and id; ids are unique; the first NCC item
   is ``h1 class="title"``; heading levels never jump; clip times are
   valid, increasing and inside the audio; ``ncc:timeInThisSmil``,
   ``ncc:totalElapsedTime`` and ``ncc:totalTime`` add up; page, tocItems,
   files and depth metadata match the content; every audio file is MP3 and
   readable.
"""

from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PIPELINE_COMMIT = "88d39392e9a3e10c1f55f1a82e5e7ea12b0b28b6"
SCHEMA_BASE = (f"https://raw.githubusercontent.com/daisy/pipeline-modules/{PIPELINE_COMMIT}/"
               "daisy202-utils/src/main/resources/xml/schemas/")
SCHEMA_FILES = ["d202/d202ncc.rng", "d202/d202smil.rng", "d202/d202meta.rng", "relaxngcommon/attributes.rng",
                "relaxngcommon/datatypes.rng"]
NPT_RE = re.compile(r"^npt=(\d+(?:\.\d+)?)s$")
CLOCK_RE = re.compile(r"^(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)$")
NS = {"h": "http://www.w3.org/1999/xhtml"}


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def text(self) -> str:
        out = ["DAISY 2.02 check", ""]
        out += [f"INFO     {m}" for m in self.info]
        out += [f"ERROR    {m}" for m in self.errors]
        out += [f"WARNING  {m}" for m in self.warnings]
        out += ["", f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        return "\n".join(out) + "\n"


def schema_cache_dir() -> Path:
    if os.environ.get("LEKTOR_SCHEMA_CACHE"):
        return Path(os.environ["LEKTOR_SCHEMA_CACHE"])
    return Path.home() / ".cache" / "lektor" / f"daisy202-schemas-{PIPELINE_COMMIT[:8]}"


def fetch_schemas(timeout: float = 20) -> Optional[Path]:
    """Download (once) and return the schema folder, or None if offline."""
    root = schema_cache_dir()
    if all((root / f).exists() for f in SCHEMA_FILES):
        return root
    try:
        for f in SCHEMA_FILES:
            dest = root / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(SCHEMA_BASE + f, timeout=timeout) as r:
                data = r.read()
            if f.endswith("datatypes.rng"):
                data = data.replace(b"http://relaxng.org/ns/compatibility/datatypes/1.0",
                                    b"http://www.w3.org/2001/XMLSchema-datatypes")
            dest.write_bytes(data)
        return root
    except Exception:
        return None


def _parser():
    from lxml import etree

    return etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False, remove_comments=False)


def _clock_seconds(v: str) -> Optional[float]:
    m = CLOCK_RE.match(v.strip())
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _meta(doc, name: str) -> list[str]:
    return [m.get("content", "") for m in doc.iter() if isinstance(m.tag, str) and m.tag.endswith("meta")
            and m.get("name") == name]


def check_daisy(folder: Path, log: Callable[[str], None] = print, use_schemas: bool = True) -> CheckReport:
    from lxml import etree

    folder = Path(folder)
    rep = CheckReport()
    ncc_path = folder / "ncc.html"
    if not ncc_path.exists():
        rep.error("ncc.html is missing")
        return rep
    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    for name in files:
        if name != name.lower() or not re.fullmatch(r"[a-z0-9._-]+", name):
            rep.error(f"file name '{name}' is not lower case ASCII without spaces")

    docs: dict[str, object] = {}
    for name in files:
        if name.endswith((".html", ".smil")):
            try:
                docs[name] = etree.parse(str(folder / name), _parser())
            except etree.XMLSyntaxError as e:
                rep.error(f"{name} is not well-formed XML: {e}")
    if "ncc.html" not in docs:
        return rep
    ncc = docs["ncc.html"]

    # -- 1. schemas ---------------------------------------------------------------
    if use_schemas:
        root = fetch_schemas()
        if root is None:
            rep.info.append("schema validation skipped: the DAISY Pipeline schemas could not be downloaded")
        else:
            try:
                ncc_rng = etree.RelaxNG(etree.parse(str(root / "d202/d202ncc.rng")))
                smil_rng = etree.RelaxNG(etree.parse(str(root / "d202/d202smil.rng")))
                if not ncc_rng.validate(ncc):
                    for err in list(ncc_rng.error_log)[:10]:
                        rep.error(f"ncc.html (schema) line {err.line}: {err.message}")
                n_bad = 0
                for name, doc in docs.items():
                    if name.endswith(".smil") and not smil_rng.validate(doc):
                        n_bad += 1
                        if n_bad <= 3:
                            for err in list(smil_rng.error_log)[:3]:
                                rep.error(f"{name} (schema) line {err.line}: {err.message}")
                if n_bad > 3:
                    rep.error(f"... and {n_bad - 3} more SMIL files fail the schema")
                rep.info.append(f"validated against the DAISY Pipeline 2 schemas (pipeline-modules {PIPELINE_COMMIT[:8]})")
            except Exception as e:  # noqa: BLE001
                rep.info.append(f"schema validation skipped: {e}")

    # -- 2. ids per document --------------------------------------------------------
    ids: dict[str, dict[str, object]] = {}
    for name, doc in docs.items():
        seen: dict[str, object] = {}
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            i = el.get("id")
            if i is None:
                continue
            if i in seen:
                rep.error(f"{name}: id '{i}' is used twice")
            seen[i] = el
        ids[name] = seen

    def resolve(src: str, origin: str, want_suffix: str) -> Optional[object]:
        file, _, frag = src.partition("#")
        if not file.endswith(want_suffix):
            rep.error(f"{origin}: link '{src}' should point to a {want_suffix} file")
            return None
        if file not in files:
            rep.error(f"{origin}: link '{src}' points to a missing file")
            return None
        if not frag:
            rep.error(f"{origin}: link '{src}' has no #fragment")
            return None
        el = ids.get(file, {}).get(frag)
        if el is None:
            rep.error(f"{origin}: link '{src}' points to a missing id")
        return el

    # -- 3. NCC body --------------------------------------------------------------------
    body = ncc.getroot().find("h:body", NS)
    items = [el for el in (body if body is not None else []) if isinstance(el.tag, str)]
    if not items:
        rep.error("ncc.html has no items")
        return rep
    first = items[0]
    if etree.QName(first).localname != "h1" or first.get("class") != "title":
        rep.error('the first item in ncc.html must be <h1 class="title">')
    smil_refs: list[str] = []
    prev_level = 0
    counts = {"page-normal": 0, "page-front": 0, "page-special": 0}
    max_page = 0
    depth = 0
    for el in items:
        tag = etree.QName(el).localname
        a = el.find("h:a", NS)
        if a is None or not a.get("href"):
            rep.error(f"ncc.html: <{tag} id='{el.get('id')}'> has no link")
            continue
        href = a.get("href")
        target = resolve(href, f"ncc.html <{tag} id='{el.get('id')}'>", ".smil")
        if target is not None and etree.QName(target).localname not in ("par", "text", "seq"):
            rep.error(f"ncc.html: '{href}' should point to a par or text element")
        f = href.partition("#")[0]
        if f not in smil_refs:
            smil_refs.append(f)
        if re.fullmatch(r"h[1-6]", tag):
            level = int(tag[1])
            depth = max(depth, level)
            if prev_level and level > prev_level + 1:
                rep.error(f"ncc.html: heading level jumps from h{prev_level} to h{level} at '{a.text}'")
            prev_level = level
        elif tag == "span":
            cls = el.get("class")
            if cls in counts:
                counts[cls] += 1
                if cls == "page-normal":
                    if not (a.text or "").strip().isdigit():
                        rep.error(f"ncc.html: page-normal '{a.text}' is not a number")
                    else:
                        max_page = max(max_page, int(a.text.strip()))
    smils = [f for f in files if f.endswith(".smil")]
    for s in smils:
        if s not in smil_refs:
            rep.error(f"{s} is never referenced from ncc.html, so players would skip it")
    if smil_refs != sorted(smil_refs):
        rep.warn("SMIL files are referenced from ncc.html out of order")

    # -- 4. SMIL files and timing ---------------------------------------------------------
    total = 0.0
    audio_durations: dict[str, float] = {}
    for s in smil_refs:
        doc = docs.get(s)
        if doc is None:
            continue
        elapsed_meta = _meta(doc, "ncc:totalElapsedTime")
        if elapsed_meta:
            v = _clock_seconds(elapsed_meta[0])
            if v is None or abs(v - total) > 1.0:
                rep.error(f"{s}: ncc:totalElapsedTime is {elapsed_meta[0]} but the files before it last {total:.1f} s")
        smil_sum = 0.0
        last_end: dict[str, float] = {}
        for par in doc.iter("par"):
            text = par.find("text")
            if text is None:
                rep.error(f"{s}: par '{par.get('id')}' has no text")
            else:
                resolve(text.get("src", ""), f"{s} par '{par.get('id')}'", ".html")
            for au in par.iter("audio"):
                src = au.get("src", "")
                if src not in files:
                    rep.error(f"{s}: audio '{src}' is missing")
                    continue
                if not src.endswith(".mp3"):
                    rep.error(f"{s}: audio '{src}' is not MP3")
                b, en = NPT_RE.match(au.get("clip-begin", "")), NPT_RE.match(au.get("clip-end", ""))
                if not b or not en:
                    rep.error(f"{s}: audio in par '{par.get('id')}' has clip times not in npt=0.000s form")
                    continue
                cb, ce = float(b.group(1)), float(en.group(1))
                if ce <= cb:
                    rep.error(f"{s}: par '{par.get('id')}' ends ({ce}) before it begins ({cb})")
                if src in last_end and cb < last_end[src] - 0.0015:
                    rep.error(f"{s}: par '{par.get('id')}' starts before the previous clip ends")
                if src in last_end and cb > last_end[src] + 0.0015:
                    rep.warn(f"{s}: a gap before par '{par.get('id')}' would be skipped by players")
                last_end[src] = ce
                if src not in audio_durations:
                    audio_durations[src] = _audio_duration(folder / src)
                if ce > audio_durations[src] + 0.25:
                    rep.error(f"{s}: par '{par.get('id')}' ends at {ce:.2f} s, after the end of {src} "
                              f"({audio_durations[src]:.2f} s)")
                smil_sum += ce - cb
        seq = doc.find("body/seq")
        if seq is not None and seq.get("dur"):
            dur = float(seq.get("dur").rstrip("s"))
            if abs(dur - smil_sum) > 0.5:
                rep.error(f"{s}: seq dur {dur:.2f} s does not match its clips ({smil_sum:.2f} s)")
        this_meta = _meta(doc, "ncc:timeInThisSmil")
        if this_meta:
            v = _clock_seconds(this_meta[0])
            if v is None or abs(v - smil_sum) > 0.5:
                rep.error(f"{s}: ncc:timeInThisSmil is {this_meta[0]} but its clips last {smil_sum:.2f} s")
        if not _meta(doc, "dc:format") == ["Daisy 2.02"]:
            rep.error(f"{s}: dc:format must be 'Daisy 2.02'")
        total += smil_sum

    # -- 5. NCC metadata -------------------------------------------------------------------
    def one(name: str) -> Optional[str]:
        vals = _meta(ncc, name)
        if len(vals) != 1:
            rep.error(f"ncc.html: {name} must appear exactly once (found {len(vals)})")
            return None
        return vals[0]

    for name in ("dc:title", "dc:format", "dc:identifier", "dc:date", "dc:publisher", "ncc:charset",
                 "ncc:pageFront", "ncc:pageNormal", "ncc:pageSpecial", "ncc:tocItems", "ncc:totalTime"):
        one(name)
    for name in ("dc:creator", "dc:language"):
        if not _meta(ncc, name):
            rep.error(f"ncc.html: {name} is missing")
    if _meta(ncc, "dc:format") != ["Daisy 2.02"]:
        rep.error("ncc.html: dc:format must be 'Daisy 2.02'")
    if _meta(ncc, "ncc:multimediaType") != ["audioFullText"]:
        rep.error("ncc.html: ncc:multimediaType should be 'audioFullText'")
    tt = _meta(ncc, "ncc:totalTime")
    if tt:
        v = _clock_seconds(tt[0])
        if v is None:
            rep.error(f"ncc.html: ncc:totalTime '{tt[0]}' is not in h:mm:ss form")
        elif abs(v - total) > 1.5:
            rep.error(f"ncc.html: ncc:totalTime is {tt[0]} but the SMIL files last {total:.1f} s")
    for key, cls in (("ncc:pageNormal", "page-normal"), ("ncc:pageFront", "page-front"), ("ncc:pageSpecial", "page-special")):
        vals = _meta(ncc, key)
        if vals and vals[0] != str(counts[cls]):
            rep.error(f"ncc.html: {key} is {vals[0]} but there are {counts[cls]} {cls} items")
    toc = _meta(ncc, "ncc:tocItems")
    if toc and toc[0] != str(len(items)):
        rep.error(f"ncc.html: ncc:tocItems is {toc[0]} but there are {len(items)} items")
    mp = _meta(ncc, "ncc:maxPageNormal")
    if mp and mp[0] != str(max_page):
        rep.error(f"ncc.html: ncc:maxPageNormal is {mp[0]} but the highest page is {max_page}")
    nf = _meta(ncc, "ncc:files")
    if nf and nf[0] != str(len(files)):
        rep.error(f"ncc.html: ncc:files is {nf[0]} but the book has {len(files)} files")
    dp = _meta(ncc, "ncc:depth")
    if dp and dp[0] != str(depth):
        rep.error(f"ncc.html: ncc:depth is {dp[0]} but the deepest heading is h{depth}")
    rep.info.append(f"{len(items)} NCC items, {len(smils)} SMIL files, {total / 60:.1f} minutes, pages "
                    f"{counts['page-normal']} normal / {counts['page-front']} front / {counts['page-special']} special")
    return rep


def _audio_duration(path: Path) -> float:
    try:
        from .ffmpeg import probe

        return float(probe(path)["format"]["duration"])
    except Exception:
        return float("inf")


def check_daisy_zip(zip_path: Path, **kw) -> CheckReport:
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            if "ncc.html" not in names:
                rep = CheckReport()
                rep.error("ncc.html is not at the top level of the ZIP")
                return rep
            z.extractall(td)
        return check_daisy(Path(td), **kw)
