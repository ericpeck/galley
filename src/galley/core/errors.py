"""Located input errors.

``str(error)`` is the banner copy. ``lineno`` and ``colno`` are the 1-based
caret when the problem has a line and column. A UTF-8 failure that cannot be
placed on a line keeps ``position``, the byte offset, and says so in the copy.
"""

MAX_NESTING_DEPTH = 64

_UTF8_BANNER = (
    "This file isn't valid UTF-8, so Galley can't read it. "
    "The first bad byte is {where}. "
    "Save the file as UTF-8 and open it again."
)


class InputError(Exception):
    """A problem in the user's document, with the text the error banner shows."""

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
        position: int | None = None,
    ) -> None:
        super().__init__(message)
        self.lineno = line
        self.colno = column
        self.position = position
        # JSON's UnicodeDecodeError calls this offset ``start``. Callers that
        # only know a byte offset can read either name.
        self.start = position


def utf8_input_error(data: bytes, offset: int) -> InputError:
    """Build the banner for a UTF-8 byte that cannot be decoded.

    The line and column come from decoding the bytes before ``offset`` and
    counting newlines. If that prefix is not valid UTF-8, the banner names
    the byte offset instead.
    """
    located = _line_column_at_byte(data, offset)
    if located is None:
        where = f"at byte {offset:,}"
        line = None
        column = None
    else:
        line, column = located
        where = f"at line {line}, column {column}"
    return InputError(
        _UTF8_BANNER.format(where=where),
        line=line,
        column=column,
        position=offset,
    )


def nesting_error(line: int, column: int) -> InputError:
    """Build the banner for a document nested past the safe limit."""
    message = (
        "This document is nested more than "
        f"{MAX_NESTING_DEPTH} levels deep, which is more than Galley can "
        f"format safely. The limit was reached at line {line}."
    )
    return InputError(message, line=line, column=column)


def surrogate_error(line: int, column: int, escape: str) -> InputError:
    """Build the banner for a JSON escape that is not a Unicode scalar."""
    message = (
        f"Line {line}, column {column} has an invalid character escape "
        f"({escape}) that can't be saved as UTF-8."
    )
    return InputError(message, line=line, column=column)


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
