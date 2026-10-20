"""Read one file as UTF-8 text for the Input editor.

This module has no Qt. It reads bytes, refuses what Galley can't open,
and names the reason with an :class:`OpenErrorKind`. The app chooses the
words, and never shows the full path in them.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Hard cap on the file size Open accepts. 1 MB is 1_000_000 bytes, the
# same SI unit the status bar shows. Format keeps its own size check.
MAX_OPEN_MB = 100
MAX_OPEN_BYTES = MAX_OPEN_MB * 1_000_000

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
# Bytes sampled when looking for UTF-16 without a byte order mark.
_UTF16_SAMPLE = 64


class OpenErrorKind(StrEnum):
    """Why a file was not opened. Input stays as it was."""

    NOT_FOUND = "not_found"
    IS_DIRECTORY = "is_directory"
    PERMISSION = "permission"
    NOT_UTF8 = "not_utf8"
    UTF16 = "utf16"
    TOO_LARGE = "too_large"
    OS_ERROR = "os_error"


class OpenTextError(Exception):
    """The file was not read. ``line`` is set for ``not_utf8`` only."""

    def __init__(self, kind: OpenErrorKind, line: int | None = None) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.line = line


@dataclass(frozen=True)
class OpenedText:
    """A file's text with newlines normalized, and its size on disk."""

    text: str
    size: int


def read_text(path: Path, limit: int = MAX_OPEN_BYTES) -> OpenedText:
    """Return ``path`` decoded as strict UTF-8.

    A UTF-8 byte order mark is dropped and newlines become line feeds.
    Files over ``limit`` bytes are refused before they are read.
    Everything else Galley can't open raises :class:`OpenTextError`.
    """
    try:
        if path.is_dir():
            raise OpenTextError(OpenErrorKind.IS_DIRECTORY)
        size = path.stat().st_size
        if size > limit:
            raise OpenTextError(OpenErrorKind.TOO_LARGE)
        data = path.read_bytes()
    except OSError as exc:
        raise OpenTextError(_os_error_kind(exc)) from exc
    if _looks_like_utf16(data):
        raise OpenTextError(OpenErrorKind.UTF16)
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM) :]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = data.count(b"\n", 0, exc.start) + 1
        raise OpenTextError(OpenErrorKind.NOT_UTF8, line) from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return OpenedText(text, len(data))


def format_for_suffix(suffix: str) -> str | None:
    """Return ``JSON`` or ``YAML`` for a known extension, or None."""
    match suffix.casefold():
        case ".json":
            return "JSON"
        case ".yaml" | ".yml":
            return "YAML"
        case _:
            return None


def sniff_format(text: str) -> str | None:
    """Guess ``JSON`` or ``YAML`` from the first meaningful line, or None.

    An object or array opener is JSON. A document marker, a list item, or
    a ``key:`` line is YAML. Anything else does not settle it.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("{", "[")):
            return "JSON"
        if line.startswith("---") or line.startswith("- "):
            return "YAML"
        key, colon, rest = line.partition(":")
        if colon and key and (rest == "" or rest.startswith(" ")):
            return "YAML"
        return None
    return None


def _os_error_kind(exc: OSError) -> OpenErrorKind:
    if isinstance(exc, FileNotFoundError):
        return OpenErrorKind.NOT_FOUND
    if isinstance(exc, IsADirectoryError):
        return OpenErrorKind.IS_DIRECTORY
    if isinstance(exc, PermissionError):
        return OpenErrorKind.PERMISSION
    return OpenErrorKind.OS_ERROR


def _looks_like_utf16(data: bytes) -> bool:
    """True for a UTF-16 byte order mark, or ASCII text padded with NULs."""
    if data.startswith(_UTF16_BOMS):
        return True
    sample = data[:_UTF16_SAMPLE]
    if len(sample) < 4:
        return False
    even = sample[0::2]
    odd = sample[1::2]
    return even.count(0) == len(even) or odd.count(0) == len(odd)
