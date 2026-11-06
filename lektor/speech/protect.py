"""Placeholders that protect finished spoken text from later passes.

When the lexicon or a rule has decided how something is said, its spoken
form is swapped for a single private-use character. Later passes (numbers,
symbols, ...) cannot see inside it, and sentence splitting cannot break it.
:meth:`Protector.restore` puts the spoken forms back at the end.
"""

from __future__ import annotations

import re

_BASE = 0xF0000
PLACEHOLDER_RE = re.compile("[\U000F0000-\U000FFFFD]")


class Protector:
    def __init__(self) -> None:
        self.items: list[str] = []

    def put(self, spoken: str) -> str:
        self.items.append(spoken)
        return chr(_BASE + len(self.items) - 1)

    def restore(self, text: str) -> str:
        # Restored text may itself contain placeholders (nested); loop.
        for _ in range(5):
            new = PLACEHOLDER_RE.sub(lambda m: self.items[ord(m.group(0)) - _BASE], text)
            if new == text:
                break
            text = new
        return text

    def sub(self, pattern: re.Pattern, repl, text: str) -> str:
        """Like ``pattern.sub`` but the replacement is protected."""
        def _r(m: re.Match) -> str:
            out = repl(m) if callable(repl) else m.expand(repl)
            if out is None:
                return m.group(0)
            return self.put(out)
        return pattern.sub(_r, text)


def has_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER_RE.search(text))
