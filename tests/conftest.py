import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import fixtures  # noqa: E402


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "Lektor"
    h.mkdir()
    return h


@pytest.fixture
def pdf_file(tmp_path):
    return fixtures.make_pdf(tmp_path / "sample.pdf")


@pytest.fixture
def pdf_labelled(tmp_path):
    return fixtures.make_pdf(tmp_path / "labelled.pdf", with_page_labels=True)


@pytest.fixture
def epub_file(tmp_path):
    return fixtures.make_epub(tmp_path / "sample.epub")


@pytest.fixture
def docx_file(tmp_path):
    return fixtures.make_docx(tmp_path / "sample.docx")
