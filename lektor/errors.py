"""Errors with plain English messages.

Every error that a librarian might see should be a :class:`LektorError`
with a ``message`` saying what went wrong and a ``hint`` saying what to do
next. The notebook shows these two lines prominently and tucks the full
traceback away underneath.
"""

from __future__ import annotations


class LektorError(Exception):
    """An error with a friendly explanation and a suggested next step."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.hint:
            return f"{self.message}\nWhat to do: {self.hint}"
        return self.message


class ConfigError(LektorError):
    """Something is wrong in book.yaml or voices.yaml."""


class ScriptError(LektorError):
    """The script could not be parsed (see the validator for details)."""


class IngestError(LektorError):
    """A source document could not be read."""


class EngineError(LektorError):
    """A speech engine failed to load or synthesise."""
