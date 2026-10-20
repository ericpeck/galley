"""Fold ranges for formatted JSON and YAML output.

``fold_ranges`` makes one linear pass with an explicit stack. It does not
parse: the Output is already valid, so JSON only needs its brackets outside
strings, and YAML only needs its indentation. Lines are 0-based and split on
``\\n``. ``line_count`` counts lines the same way, so a caller can check that
its document has the same lines before it uses the ranges.

Each range starts on the line that opens an object or array and ends on the
line that closes it. A container that opens and closes on one line is not a
range. Where two containers open on one line, only the outer one is kept.
Ranges are sorted by ``start_line``. ``depth`` is 0 for the root container,
so Collapse all can keep the root open. YAML has no line that opens the
root, so YAML ranges start at depth 1.

This module does not import Qt.
"""

import logging
import re
from array import array
from enum import IntEnum
from typing import Literal, NamedTuple

from galley.core.errors import MAX_DEPTH

FoldFormat = Literal["JSON", "YAML"]

_LOG = logging.getLogger(__name__)

# A JSON string, as UTF-8. Valid JSON strings never span lines.
_JSON_STRING = re.compile(rb'"[^"\\\n]*(?:\\.[^"\\\n]*)*"')
# Brackets, commas and newlines keep their byte. Every other byte but
# whitespace becomes ``x``, a value, until the empty containers are gone.
_STRUCTURE = b"[]{},\n"
_TOKENS = bytes(byte if byte in _STRUCTURE else ord("x") for byte in range(256))
_SPACE = b" \t\r"
# Empty containers never fold. Removing them leaves every other container
# with at least one child; a newline inside one stays for the line count.
_EMPTIES = ((b"[]", b""), (b"{}", b""), (b"[\n]", b"\n"), (b"{\n}", b"\n"))
# Fields per range in ``pack_ranges``: start, end, kind, count, depth.
_PACKED_FIELDS = 5


class FoldKind(IntEnum):
    OBJECT = 0
    ARRAY = 1


_KINDS = (FoldKind.OBJECT, FoldKind.ARRAY)


class FoldRange(NamedTuple):
    """One foldable container. ``count`` is its keys or items."""

    start_line: int
    end_line: int
    kind: FoldKind
    count: int
    depth: int


# Builds a FoldRange from a list in field order without a Python call.
_new_range = tuple.__new__


def fold_ranges(text: str, fmt: FoldFormat) -> list[FoldRange]:
    """Foldable containers in ``text``, sorted by ``start_line``.

    Text nested past ``MAX_DEPTH`` has no ranges. The scan never raises:
    if it fails, it logs and returns no ranges.
    """
    try:
        if fmt == "YAML":
            return _yaml_ranges(text)
        return _json_ranges(text)
    except Exception:
        _LOG.exception("Fold scan failed; Output will not fold.")
        return []


def line_count(text: str) -> int:
    """Lines in ``text`` as ``fold_ranges`` numbers them."""
    return text.count("\n") + 1


def pack_ranges(ranges: list[FoldRange], lines: int) -> bytes:
    """``ranges`` and ``lines`` as compact bytes for the worker's queue."""
    packed = array("I", [lines])
    for item in ranges:
        packed.extend(item)
    return packed.tobytes()


def unpack_ranges(data: bytes) -> tuple[list[FoldRange], int]:
    """The ranges and line count ``pack_ranges`` wrote. Empty data is none."""
    if not data:
        return [], 0
    packed = array("I")
    packed.frombytes(data)
    ranges = [
        _new_range(
            FoldRange,
            (
                packed[index],
                packed[index + 1],
                _KINDS[packed[index + 2]],
                packed[index + 3],
                packed[index + 4],
            ),
        )
        for index in range(1, len(packed), _PACKED_FIELDS)
    ]
    return ranges, packed[0]


def _json_ranges(text: str) -> list[FoldRange]:
    # Strings become "" so the brackets and commas left are structure. Then
    # the text shrinks to those bytes and its newlines, so the loop below
    # runs once per bracket, comma or line: the 2 MB budget is 50 ms.
    bare = _JSON_STRING.sub(b'""', text.encode("utf-8"))
    tokens = bare.translate(_TOKENS, _SPACE)
    for empty, kept in _EMPTIES:
        tokens = tokens.replace(empty, kept)
    tokens = tokens.translate(None, b"x")
    slots: list[FoldRange | None] = []
    # Open containers: (slot, start line, the parent's commas, opening byte).
    stack: list[tuple[int, int, int, int]] = []
    push, pop, reserve = stack.append, stack.pop, slots.append
    kinds, new_range, max_depth = _KINDS, _new_range, MAX_DEPTH
    line = 0
    commas = 0
    # The byte values are literal: the loop runs about 100,000 times a MB.
    for byte in tokens:
        if byte == 10:  # newline
            line += 1
        elif byte == 44:  # comma
            commas += 1
        elif not byte & 4:  # [ or {; ] and } have bit 4 set
            if len(stack) >= max_depth:
                return []
            push((len(slots), line, commas, byte))
            reserve(None)
            commas = 0
        elif stack:
            slot, start, outer, opening = pop()
            # One range per line: one that opens on its parent's line is
            # dropped and the parent kept.
            if line > start and not (stack and stack[-1][1] == start):
                kind = kinds[opening == 91]  # [
                item = (start, line, kind, commas + 1, len(stack))
                slots[slot] = new_range(FoldRange, item)
            commas = outer
    return [item for item in slots if item is not None]


def _yaml_ranges(text: str) -> list[FoldRange]:
    scan = _YamlScan()
    try:
        for number, raw in enumerate(text.split("\n")):
            scan.line(number, raw)
    except _TooDeep:
        return []
    scan.close_all()
    return _finish(scan.found)


def _finish(found: list[list]) -> list[FoldRange]:
    """Ranges that span lines, one per start line, outermost first."""
    ranges: list[FoldRange] = []
    last_start = -1
    for entry in found:
        start = entry[0]
        if entry[1] > start != last_start:
            last_start = start
            ranges.append(_new_range(FoldRange, entry))
    return ranges


_MAP = 0
_SEQ = 1


class _YamlScan:
    """Block YAML by indentation, one line at a time.

    A block mapping is the keys at one column. A block sequence is the
    ``-`` items at one column, which may be the column of the key that owns
    it. A line that ends a key or a ``-`` with no value is a header: the
    container that starts on the next lines folds from it. A line indented
    past an entry with a value continues that value (a long or block
    scalar) and has no structure of its own.
    """

    def __init__(self) -> None:
        self.found: list[list[int]] = []
        # Open containers: [column, map or seq, index in found or -1].
        self._stack: list[list[int]] = []
        self._header = -1
        self._header_column = -1
        self._value_column = -1
        self._last = -1

    def line(self, number: int, raw: str) -> None:
        content = raw.lstrip(" ")
        if not content or content.startswith("#"):
            return
        column = len(raw) - len(content)
        if self._value_column >= 0 and column > self._value_column:
            self._last = number
            return
        self._value_column = -1
        if column == 0 and (content.startswith("---") or content.startswith("...")):
            self.close_all()
            self._header = -1
            self._last = number
            return
        header = self._header
        header_column = self._header_column
        self._header = -1
        position = column
        dash = -1
        while content == "-" or content.startswith("- "):
            self._enter(_SEQ, position, number, header, header_column)
            header = -1
            dash = position
            if content == "-":
                self._set_header(number, position)
                self._last = number
                return
            rest = content[2:]
            content = rest.lstrip(" ")
            position += 2 + len(rest) - len(content)
        colon = _key_colon(content)
        if colon < 0:
            # A scalar or flow value. Lines deeper than its key or ``-``
            # continue it.
            self._value_column = dash if dash >= 0 else position
            self._last = number
            return
        self._enter(_MAP, position, number, header, header_column)
        value = _without_properties(content[colon + 1 :])
        if value:
            self._value_column = position
        else:
            self._set_header(number, position)
        self._last = number

    def close_all(self) -> None:
        while self._stack:
            self._close()

    def _set_header(self, number: int, column: int) -> None:
        self._header = number
        self._header_column = column

    def _enter(
        self, kind: int, column: int, number: int, header: int, header_column: int
    ) -> None:
        stack = self._stack
        while stack and stack[-1][0] > column:
            self._close()
        if kind == _MAP and stack and stack[-1][0] == column and stack[-1][1] == _SEQ:
            # A key at the column of a sequence ends that sequence: it was
            # the value of the previous key.
            self._close()
        if stack and stack[-1][0] == column and stack[-1][1] == kind:
            index = stack[-1][2]
            if index >= 0:
                self.found[index][3] += 1
            return
        if len(stack) >= MAX_DEPTH:
            raise _TooDeep
        index = -1
        if stack or header >= 0:
            # The root has no line of its own to fold from.
            owned = header >= 0 and column >= header_column
            start = header if owned else number
            kind_value = FoldKind.OBJECT if kind == _MAP else FoldKind.ARRAY
            index = len(self.found)
            self.found.append([start, -1, kind_value, 1, len(stack)])
        stack.append([column, kind, index])

    def _close(self) -> None:
        _column, _kind, index = self._stack.pop()
        if index >= 0:
            self.found[index][1] = self._last


class _TooDeep(Exception):
    """Nested past MAX_DEPTH."""


def _key_colon(content: str) -> int:
    """Index of the ``:`` that ends a mapping key in ``content``, or -1."""
    first = content[0]
    if first in "\"'":
        end = _quoted_end(content, first)
        if end < 0:
            return -1
        colon = end + 1
        if content.startswith(":", colon) and (
            colon + 1 == len(content) or content[colon + 1] == " "
        ):
            return colon
        return -1
    if first in "{[|>":
        return -1
    if first == "?":
        # An explicit key: its value starts at the column of its ``:``.
        return 0 if content == "?" or content.startswith("? ") else -1
    colon = content.find(": ")
    if colon >= 0:
        return colon
    return len(content) - 1 if content.endswith(":") else -1


def _quoted_end(content: str, quote: str) -> int:
    index = 1
    length = len(content)
    while index < length:
        char = content[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char == quote:
            if quote == "'" and content.startswith("'", index + 1):
                index += 2
                continue
            return index
        index += 1
    return -1


def _without_properties(value: str) -> str:
    """``value`` without leading ``&anchor`` and ``!tag`` properties."""
    rest = value.strip(" ")
    while rest and rest[0] in "&!":
        space = rest.find(" ")
        if space < 0:
            return ""
        rest = rest[space + 1 :].lstrip(" ")
    return rest
