"""Located input errors.

``kind`` says which problem this is. ``lineno`` and ``colno`` are the 1-based
caret when the problem has one. ``details`` holds the structured facts the UI
needs (a byte offset, a depth, or an escape). Banner sentences live in
``galley.app``.

``JSONDuplicateKeyError`` is a syntax-level problem, not an ``InputError``:
it can list several places at once, so it reaches the Problems pane as one
row per repeated key.
"""

from dataclasses import dataclass
from typing import Literal

MAX_DEPTH = 512

InputKind = Literal["invalid_utf8", "too_deep", "bad_escape"]


class InputError(Exception):
    """A problem in the user's document.

    The exception text is the ``kind`` name. The words shown in the banner
    are chosen in the UI from ``kind`` and ``details``.
    """

    def __init__(
        self,
        kind: InputKind,
        *,
        lineno: int | None,
        colno: int | None,
        details: dict[str, object],
    ) -> None:
        super().__init__(kind)
        self.kind = kind
        self.lineno = lineno
        self.colno = colno
        self.details = details

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Pickle ``kind``, line, column, and ``details`` for a spawn child.

        ``Exception`` rebuilds from ``args`` only. That calls ``InputError``
        with the kind and drops the keyword-only fields, so spawn would not
        deliver a located error.
        """
        return (
            _restore_input_error,
            (self.kind, self.lineno, self.colno, dict(self.details)),
        )


def _restore_input_error(
    kind: InputKind,
    lineno: int | None,
    colno: int | None,
    details: dict[str, object],
) -> InputError:
    """Rebuild an ``InputError`` after it crosses a process boundary."""
    return InputError(kind, lineno=lineno, colno=colno, details=details)


@dataclass(frozen=True)
class DuplicateKey:
    """One repeated key, at the occurrence after the first. 1-based."""

    key: str
    lineno: int
    colno: int


class JSONDuplicateKeyError(ValueError):
    """A JSON object names the same key more than once.

    ``duplicates`` lists every repeat in text order. Each one sits where the
    key appears again, because that is the line to fix. ``lineno`` and
    ``colno`` are the first repeat, like ``json.JSONDecodeError``.
    """

    def __init__(self, duplicates: tuple[DuplicateKey, ...]) -> None:
        if not duplicates:
            raise ValueError("JSONDuplicateKeyError needs at least one duplicate.")
        super().__init__("duplicate_key")
        self.duplicates = duplicates
        self.lineno = duplicates[0].lineno
        self.colno = duplicates[0].colno

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Pickle the duplicates, not the ``args`` text, for a spawn child."""
        return (JSONDuplicateKeyError, (self.duplicates,))


class NotJSONValueError(ValueError):
    """A YAML value JSON can't hold, such as a date, binary data, or a set.

    Format to JSON raises it from the ``json.dumps`` hook, so it has no line.
    The words shown to the user are chosen in ``galley.app``.
    """


def utf8_input_error(data: bytes, offset: int) -> InputError:
    """Build an ``invalid_utf8`` error for the byte at ``offset``.

    The line and column come from decoding the bytes before ``offset`` and
    counting newlines. If that prefix is not valid UTF-8, the line and column
    are left unset and ``details["offset"]`` is the only location.
    """
    located = _line_column_at_byte(data, offset)
    if located is None:
        line = None
        column = None
    else:
        line, column = located
    return InputError(
        "invalid_utf8",
        lineno=line,
        colno=column,
        details={"offset": offset},
    )


def nesting_error(line: int, column: int, *, depth: int) -> InputError:
    """Build a ``too_deep`` error at the container that crossed the limit."""
    return InputError(
        "too_deep",
        lineno=line,
        colno=column,
        details={"depth": depth},
    )


def escape_error(line: int, column: int, escape: str) -> InputError:
    """Build a ``bad_escape`` error for an escape that is not a Unicode scalar."""
    return InputError(
        "bad_escape",
        lineno=line,
        colno=column,
        details={"escape": escape},
    )


def _line_column_at_byte(data: bytes, offset: int) -> tuple[int, int] | None:
    """Return a 1-based line and column for the byte at ``offset``.

    The column is the character position of that byte on its line. ``None``
    means the bytes before ``offset`` are not valid UTF-8.
    """
    if offset < 0:
        return None
    try:
        prefix = data[:offset].decode("utf-8")
    except UnicodeDecodeError:
        return None
    line = prefix.count("\n") + 1
    last_newline = prefix.rfind("\n")
    column = len(prefix) - (last_newline + 1) + 1
    return line, column
