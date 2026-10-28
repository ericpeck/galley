"""Escape as String and Unescape String for the main window.

Both commands read Input and write Output. They never change Input. The
work is in :mod:`galley.core.escape`. The words live here.
"""

from dataclasses import dataclass

from galley.app.problem_text import (
    DisplayProblem,
    display_input_error,
    display_problem,
    message_for,
)
from galley.core.errors import InputError
from galley.core.escape import (
    EscapeError,
    UnescapeError,
    escape_document,
    unescape_document,
)
from galley.core.format import NOT_JSON_KEY, SELF_REFERENCE
from galley.core.problems import Problem, collect_problems

ESCAPED_MESSAGE = "Escaped as a JSON string."
NOT_ESCAPED_STRING_MESSAGE = "The input isn't an escaped JSON string."
NOT_ESCAPED_JSON_MESSAGE = "The escaped string doesn't contain valid JSON."
NOT_JSON_VALUE_MESSAGE = (
    "This document has a value JSON can't hold, such as a date or binary data."
)
NOT_JSON_KEY_MESSAGE = (
    "This document has a key JSON can't hold, such as a date. "
    "JSON keys must be text or numbers."
)
SELF_REFERENCE_MESSAGE = (
    "This document refers to itself, so it can't be written as JSON."
)
_NOT_JSON_MESSAGES = {
    NOT_JSON_KEY: NOT_JSON_KEY_MESSAGE,
    SELF_REFERENCE: SELF_REFERENCE_MESSAGE,
}


def not_json_text(kind: str) -> str:
    """The sentence for a value, key or self-reference JSON can't hold.

    ``kind`` is the error's text: ``NOT_JSON_KEY`` or ``SELF_REFERENCE``
    from ``galley.core.format``. Any other text is a value JSON can't hold.
    """
    return _NOT_JSON_MESSAGES.get(kind, NOT_JSON_VALUE_MESSAGE)


@dataclass(frozen=True)
class StringResult:
    """What Escape or Unescape shows.

    ``output`` replaces Output, or is None to leave Output unchanged.
    ``problems`` are Problems pane rows for Input. When there are rows, the
    status shows their count instead of ``status``.
    """

    output: str | None
    status: str
    problems: tuple[DisplayProblem, ...] = ()


def unescaped_message(target: str) -> str:
    """Status after Unescape. ``target`` is the Output picker label."""
    return f"Unescaped to {target}."


def escape_too_large_message(size: str, limit: str) -> str:
    """Status when Input is too large to escape on the UI thread."""
    return f"This document is too large to escape ({size}). The limit is {limit}."


def unescape_too_large_message(size: str, limit: str) -> str:
    """Status when Input is too large to unescape on the UI thread."""
    return f"This document is too large to unescape ({size}). The limit is {limit}."


def format_too_large_message(size: str, limit: str) -> str:
    """Status when XML or HTML Input is larger than Format takes.

    The same sentence as Escape's, which #104 set for every size limit.
    """
    return f"This document is too large to format ({size}). The limit is {limit}."


def convert_python_too_large_message(size: str) -> str:
    """Status when Input is too large for Convert from Python on the UI thread.

    One sentence names both limits, whichever format Output is.
    """
    return (
        f"This document is too large to convert from Python ({size}). "
        "The limit is about 1 MB for JSON or about 192 KB for YAML."
    )


def escape_input(text: str, source: str) -> StringResult:
    """Escape Input read as ``source``. A parse failure lists Input problems."""
    early = collect_problems(text, source)
    if early:
        return StringResult(None, "", tuple(display_problem(item) for item in early))
    try:
        escaped = escape_document(text, source)
    except EscapeError as exc:
        # Its text names a key or self-reference kind, or is json's own text.
        return StringResult(None, not_json_text(str(exc)))
    except InputError as exc:
        return StringResult(None, "", (display_input_error(exc),))
    except Exception as exc:
        found = collect_problems(text, source, exc)
        if not found:
            return StringResult(None, str(exc).strip() or exc.__class__.__name__)
        return StringResult(None, "", tuple(display_problem(item) for item in found))
    return StringResult(escaped, ESCAPED_MESSAGE)


def unescape_input(text: str, target: str) -> StringResult:
    """Unescape Input and format it as ``target``, the Output picker label."""
    try:
        formatted = unescape_document(text, target)
    except UnescapeError as exc:
        if exc.kind == "not_string":
            return StringResult(None, NOT_ESCAPED_STRING_MESSAGE)
        if exc.kind == "duplicate_key":
            first = exc.duplicates[0]
            repeated = Problem("json_duplicate", first.lineno, first.key)
            return StringResult(None, message_for(repeated))
        return StringResult(None, NOT_ESCAPED_JSON_MESSAGE)
    return StringResult(formatted, unescaped_message(target))
