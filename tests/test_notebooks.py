"""The notebooks must be valid and only call functions that exist."""

import json
import re
from pathlib import Path

import pytest

from lektor import notebook as nb

NB_DIR = Path(__file__).parent.parent / "notebooks"


@pytest.mark.parametrize("name", ["Lektor.ipynb"])
def test_notebook_calls_exist(name):
    data = json.loads((NB_DIR / name).read_text())
    assert data["nbformat"] == 4
    calls = set()
    for cell in data["cells"]:
        if cell["cell_type"] != "code":
            continue
        src = "".join(cell["source"])
        assert src.lstrip().startswith("#@title"), "every code cell is a form with a title"
        calls |= set(re.findall(r"\bnb\.(\w+)\(", src))
        compile(src.replace("from google.colab", "from os"), name, "exec")
    assert calls, "the notebook should call lektor.notebook"
    for c in calls:
        assert callable(getattr(nb, c, None)), f"lektor.notebook.{c} is missing"
