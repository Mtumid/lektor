"""EPUB 2 and 3 reader.

Lektor reads EPUB itself (zip + OPF + XHTML) instead of using Docling,
because it must keep the publisher's page break markers for print page
navigation, and it avoids the AGPL licence of ``ebooklib``.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..errors import IngestError
from .common import RawItem
from .html_walk import HtmlWalker, walk_html


def _opf_path(z: zipfile.ZipFile) -> str:
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", "replace")
    except KeyError:
        opfs = [n for n in z.namelist() if n.endswith(".opf")]
        if opfs:
            return opfs[0]
        raise IngestError("This EPUB has no META-INF/container.xml and no package file.",
                          hint="The file may be damaged. Ask the publisher for a fresh copy.")
    soup = BeautifulSoup(container, "xml")
    rf = soup.find("rootfile")
    if not rf or not rf.get("full-path"):
        raise IngestError("This EPUB's container.xml does not say where the package file is.")
    return rf["full-path"]


def read_epub(path: Path) -> tuple[list[RawItem], dict[str, str]]:
    """Return (items, metadata) for an EPUB file."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise IngestError(f"{path.name} is not a valid EPUB (it is not a zip file).",
                          hint="Check the file opens in an e-book reader.") from e
    with z:
        opf_name = _opf_path(z)
        base = posixpath.dirname(opf_name)
        opf = BeautifulSoup(z.read(opf_name).decode("utf-8", "replace"), "xml")
        meta: dict[str, str] = {}
        md = opf.find("metadata")
        if md:
            for key, tag in (("title", "title"), ("author", "creator"), ("publisher", "publisher"),
                             ("year", "date"), ("language", "language")):
                el = md.find(tag)
                if el and el.get_text(strip=True):
                    meta[key] = el.get_text(strip=True)
            if "year" in meta:
                meta["year"] = meta["year"][:4]
        manifest = {it.get("id"): it for it in opf.find_all("item")}
        nav_ids = {i for i, it in manifest.items() if "nav" in (it.get("properties") or "").split()}
        spine = opf.find("spine")
        if not spine:
            raise IngestError("This EPUB has no reading order (spine).")
        walker = HtmlWalker()
        for ref in spine.find_all("itemref"):
            idref = ref.get("idref")
            if idref in nav_ids:
                continue
            if ref.get("linear") == "no":
                continue
            item = manifest.get(idref)
            if item is None:
                continue
            mt = item.get("media-type", "")
            if "html" not in mt and "xml" not in mt:
                continue
            href = posixpath.normpath(posixpath.join(base, unquote(item.get("href", ""))))
            try:
                html = z.read(href).decode("utf-8", "replace")
            except KeyError:
                continue
            walk_html(html, walker)
    return walker.items, meta
