"""Read one file as UTF-8 text for the Input editor.

This module has no Qt and no message strings (ADR 0001 §4). It reads
bytes, refuses what Galley can't open, and names the reason with a
:class:`LoadErrorKind`. The app chooses the words.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from galley.core.formats import format_for_extension, sniff_format

# Hard cap on the file size Open accepts, checked with stat() before any
# bytes are read. 1 MB is 1_000_000 bytes, the SI unit the status bar
# shows. Format keeps its own size check.
MAX_OPEN_MB = 100
MAX_OPEN_BYTES = MAX_OPEN_MB * 1_000_000

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class LoadErrorKind(StrEnum):
    """Why a file was not opened. Input stays as it was."""

    NOT_FOUND = "not_found"
    IS_DIRECTORY = "is_directory"
    PERMISSION = "permission"
    NOT_UTF8 = "not_utf8"
    UTF16 = "utf16"
    TOO_LARGE = "too_large"
    OS_ERROR = "os_error"


class LoadError(Exception):
    """The file was not read.

    ``offset`` and ``line`` locate the earliest invalid byte for ``not_utf8``.
    The offset counts from the start of the file, byte order mark included.
    """

    def __init__(
        self,
        kind: LoadErrorKind,
        *,
        offset: int | None = None,
        line: int | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.offset = offset
        self.line = line


@dataclass(frozen=True)
class LoadedDocument:
    """A file ready for Input.

    ``text`` has LF newlines and no byte order mark. ``fmt`` is a format
    label, such as ``JSON``, or None when neither the extension nor the text
    settles it.
    ``size`` is the file's size in bytes. ``had_bom`` and ``newline``
    record the original encoding details for a later Save.
    """

    text: str
    fmt: str | None
    size: int
    had_bom: bool
    newline: str


def read_document(path: Path, max_bytes: int = MAX_OPEN_BYTES) -> LoadedDocument:
    """Return ``path`` decoded as strict UTF-8.

    Files over ``max_bytes`` are refused before they are read. A UTF-8 byte
    order mark is dropped, and CRLF and CR newlines become LF. Everything
    else Galley can't open raises :class:`LoadError`.
    """
    try:
        if path.is_dir():
            raise LoadError(LoadErrorKind.IS_DIRECTORY)
        if path.stat().st_size > max_bytes:
            raise LoadError(LoadErrorKind.TOO_LARGE)
        data = path.read_bytes()
    except OSError as exc:
        raise LoadError(_os_error_kind(exc)) from exc
    if data.startswith(_UTF16_BOMS):
        raise LoadError(LoadErrorKind.UTF16)
    had_bom = data.startswith(_UTF8_BOM)
    start = len(_UTF8_BOM) if had_bom else 0
    try:
        # The same as decoding with utf-8-sig, but the error offset is
        # still counted from the start of the file.
        text = data[start:].decode("utf-8")
    except UnicodeDecodeError as exc:
        offset = start + exc.start
        line = data.count(b"\n", 0, offset) + 1
        raise LoadError(LoadErrorKind.NOT_UTF8, offset=offset, line=line) from exc
    newline = _newline_style(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return LoadedDocument(text, detect_format(path, text), len(data), had_bom, newline)


def detect_format(path: Path, text: str) -> str | None:
    """Return the format label for ``path``, or None when unsettled.

    An extension in the format table wins over the text. Otherwise the
    first meaningful line decides: an object or array opener is JSON, a
    document marker, a list item, or a ``key:`` line is YAML, and ``<`` is
    XML.
    """
    fmt = format_for_extension(path.suffix) or sniff_format(text)
    return None if fmt is None else fmt.label


def _newline_style(text: str) -> str:
    """The first line ending in ``text``, or LF when it has none."""
    for index, char in enumerate(text):
        if char == "\n":
            return "\n"
        if char == "\r":
            return "\r\n" if text.startswith("\n", index + 1) else "\r"
    return "\n"


def _os_error_kind(exc: OSError) -> LoadErrorKind:
    if isinstance(exc, FileNotFoundError):
        return LoadErrorKind.NOT_FOUND
    if isinstance(exc, IsADirectoryError):
        return LoadErrorKind.IS_DIRECTORY
    if isinstance(exc, PermissionError):
        return LoadErrorKind.PERMISSION
    return LoadErrorKind.OS_ERROR
