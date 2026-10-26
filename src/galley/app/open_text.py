"""Status-bar sentences for Open.

Core reports a :class:`LoadErrorKind`. The words live here. Each sentence
names the file, never its full path; the path goes in the tooltip and
accessible description.
"""

from typing import assert_never

from galley.core.load import MAX_OPEN_MB, LoadError, LoadErrorKind


def open_error_text(file_name: str, error: LoadError) -> str:
    """Return the sentence for a file that was not opened."""
    kind = error.kind
    if kind is LoadErrorKind.NOT_FOUND:
        return f"Couldn't find {file_name}. It may have been moved or deleted."
    if kind is LoadErrorKind.IS_DIRECTORY:
        return f"{file_name} is a folder. Choose a file to open."
    if kind is LoadErrorKind.PERMISSION:
        return f"Couldn't open {file_name}. You don't have permission to read it."
    if kind is LoadErrorKind.NOT_UTF8:
        return (
            f"{file_name} isn't UTF-8 text (line {error.line}), "
            "so Galley can't open it."
        )
    if kind is LoadErrorKind.UTF16:
        return f"{file_name} is UTF-16 text. Save it as UTF-8 and try again."
    if kind is LoadErrorKind.TOO_LARGE:
        return f"{file_name} is too large to open (over {MAX_OPEN_MB} MB)."
    if kind is LoadErrorKind.OS_ERROR:
        return f"Couldn't read {file_name}. Try again, or check the disk."
    assert_never(kind)


def replace_input_text(file_name: str) -> str:
    """The question asked before unsaved Input is replaced."""
    return f"Replace the current input with {file_name}?"


def opening_text(file_name: str) -> str:
    """Busy text shown once Open has taken longer than the reveal delay."""
    return f"Opening {file_name}…"


def opened_text(file_name: str, size: str) -> str:
    """Status after a file loads. ``size`` is already in SI units."""
    return f"Opened {file_name} ({size})"


def opened_first_text(count: int) -> str:
    """Status after a drop of several files, when only the first opens."""
    return f"Opened the first of {count} files."


DROP_WHILE_FORMATTING_TEXT = "Can't open a file while formatting."


def input_window_title(file_name: str) -> str:
    """Window title for a file loaded into Input."""
    return f"{file_name} — Galley"
