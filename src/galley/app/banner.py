"""Banner sentences for located input errors and a dead worker.

Core reports a ``kind`` and structured ``details``. The words a person reads
are chosen here, from the copy already approved for these three cases.
A worker that exits on its own has no line or column.
"""

from typing import assert_never

from galley.core import MAX_DEPTH
from galley.core.errors import InputError

FORMATTER_STOPPED = (
    "The formatter stopped unexpectedly. "
    "Your text and file weren't changed. "
    "Press Format to try again."
)


def banner_text(error: InputError) -> str:
    """Return the banner sentence for ``error``."""
    kind = error.kind
    if kind == "invalid_utf8":
        return _invalid_utf8_banner(error)
    if kind == "too_deep":
        return _too_deep_banner(error)
    if kind == "bad_escape":
        return _bad_escape_banner(error)
    assert_never(kind)


def _invalid_utf8_banner(error: InputError) -> str:
    if error.lineno is None or error.colno is None:
        offset = error.details["offset"]
        where = f"at byte {offset:,}"
    else:
        where = f"at line {error.lineno}, column {error.colno}"
    return (
        "This file isn't valid UTF-8, so Galley can't read it. "
        f"The first bad byte is {where}. "
        "Save the file as UTF-8 and open it again."
    )


def _too_deep_banner(error: InputError) -> str:
    return (
        "This document is nested more than "
        f"{MAX_DEPTH} levels deep, which is more than Galley can "
        f"format safely. The limit was reached at line {error.lineno}."
    )


def _bad_escape_banner(error: InputError) -> str:
    escape = error.details["escape"]
    return (
        f"Line {error.lineno}, column {error.colno} has an invalid "
        f"character escape ({escape}) that can't be saved as UTF-8."
    )
