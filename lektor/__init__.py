"""Lektor: turn long documents into accessible audiobooks.

The package is organised by pipeline stage:

- ``lektor.ingest``   readers for PDF, EPUB, DOCX, HTML, TXT and Markdown
- ``lektor.script``   the editable script format (parser, writer, validator, review)
- ``lektor.speech``   text normalisation, lexicons and context rules
- ``lektor.chunk``    sentence segmentation and synthesis units
- ``lektor.engines``  speech engine interface, registry and workers
- ``lektor.qa``       transcription checks and the QA report
- ``lektor.assemble`` joining units, pauses, loudness, timing map
- ``lektor.package``  DAISY 2.02, M4B and MP3 writers

Every stage is reachable from ``python -m lektor <stage>`` (see ``lektor.cli``).
"""

__version__ = "0.1.0"
