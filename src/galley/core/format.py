"""JSON and YAML formatting.

Invalid UTF-8, documents nested past the safe limit, and JSON lone-surrogate
escapes raise ``InputError``. The message is the error banner. Other syntax
errors keep the line and column from the parser.
"""

import json
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.reader import ReaderError
from ruamel.yaml.tokens import (
    BlockEndToken,
    BlockMappingStartToken,
    BlockSequenceStartToken,
    FlowMappingEndToken,
    FlowMappingStartToken,
    FlowSequenceEndToken,
    FlowSequenceStartToken,
)

from galley.core.errors import (
    MAX_NESTING_DEPTH,
    InputError,
    nesting_error,
    surrogate_error,
    utf8_input_error,
)

_HEX = frozenset("0123456789abcdefABCDEF")
_HIGH_MIN = 0xD800
_HIGH_MAX = 0xDBFF
_LOW_MIN = 0xDC00
_LOW_MAX = 0xDFFF
_YAML_OPEN = (
    BlockMappingStartToken,
    BlockSequenceStartToken,
    FlowMappingStartToken,
    FlowSequenceStartToken,
)
_YAML_CLOSE = (BlockEndToken, FlowMappingEndToken, FlowSequenceEndToken)


def format_json(text: str | bytes) -> str:
    """Return ``text`` parsed as JSON and pretty-printed."""
    source = _decode_json(text)
    opened = _json_limits(source)
    try:
        parsed = json.loads(source)
        return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    except RecursionError:
        raise nesting_error(opened[0], opened[1]) from None


def format_yaml(text: str | bytes) -> str:
    """Return ``text`` parsed as YAML and dumped as a block document."""
    opened = _yaml_limits(text)
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    try:
        parsed = yaml.load(text)
    except RecursionError:
        raise nesting_error(opened[0], opened[1]) from None
    except ReaderError as exc:
        error = _reader_utf8_error(text, exc)
        if error is not None:
            raise error from None
        raise
    buffer = StringIO()
    try:
        yaml.dump(parsed, buffer)
    except RecursionError:
        raise nesting_error(opened[0], opened[1]) from None
    return buffer.getvalue()


def _decode_json(text: str | bytes) -> str:
    """Return JSON text, stripping a leading UTF-8 BOM from bytes.

    ``json.loads`` accepts a BOM on bytes and rejects it on text. Decoding
    here keeps that split, and turns a bad byte into a located banner.
    """
    if isinstance(text, str):
        return text
    try:
        decoded = text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise utf8_input_error(text, exc.start) from None
    if decoded.startswith("\ufeff"):
        return decoded[1:]
    return decoded


def _json_limits(text: str) -> tuple[int, int]:
    """Reject nesting past the limit and lone UTF-16 surrogates.

    Returns the 1-based line and column of the last container opened, so a
    later ``RecursionError`` still has a place to point.
    """
    depth = 0
    line = 1
    column = 1
    index = 0
    size = len(text)
    in_string = False
    opened = (1, 1)
    while index < size:
        char = text[index]
        if in_string:
            if char == "\\":
                escape = _unicode_escape(text, index)
                if escape is not None:
                    literal, unit = escape
                    if _HIGH_MIN <= unit <= _HIGH_MAX:
                        pair = _unicode_escape(text, index + 6)
                        if pair is not None and _LOW_MIN <= pair[1] <= _LOW_MAX:
                            index, line, column = _advance(
                                text, index, line, column, 12
                            )
                            continue
                    if _HIGH_MIN <= unit <= _LOW_MAX:
                        raise surrogate_error(line, column, literal)
                    index, line, column = _advance(text, index, line, column, 6)
                    continue
                step = 2 if index + 1 < size else 1
                index, line, column = _advance(text, index, line, column, step)
                continue
            if char == '"':
                in_string = False
            elif _HIGH_MIN <= ord(char) <= _LOW_MAX:
                raise surrogate_error(line, column, f"\\u{ord(char):04X}")
            index, line, column = _advance(text, index, line, column, 1)
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            opened = (line, column)
            if depth > MAX_NESTING_DEPTH:
                raise nesting_error(line, column)
        elif char in "}]":
            depth = max(0, depth - 1)
        index, line, column = _advance(text, index, line, column, 1)
    return opened


def _unicode_escape(text: str, index: int) -> tuple[str, int] | None:
    """Return the ``\\uXXXX`` escape and its code unit at ``index``."""
    if (
        index + 6 > len(text)
        or text[index] != "\\"
        or text[index + 1] != "u"
        or any(char not in _HEX for char in text[index + 2 : index + 6])
    ):
        return None
    literal = text[index : index + 6]
    return literal, int(literal[2:], 16)


def _advance(
    text: str, index: int, line: int, column: int, count: int
) -> tuple[int, int, int]:
    end = index + count
    while index < end:
        if text[index] == "\n":
            line += 1
            column = 1
        else:
            column += 1
        index += 1
    return index, line, column


def _yaml_limits(text: str | bytes) -> tuple[int, int]:
    """Reject UTF-8 bytes and nesting past the limit before the composer runs.

    The scanner walks tokens in a loop. Stopping at depth 64 keeps the
    composer off the call stack, so a deep document cannot overflow the
    Windows stack. The returned line and column are the last container the
    scanner opened.
    """
    yaml = YAML(typ="safe")
    depth = 0
    opened = (1, 1)
    try:
        for token in yaml.scan(text):
            if isinstance(token, _YAML_OPEN):
                depth += 1
                opened = _mark(token, opened)
                if depth > MAX_NESTING_DEPTH:
                    raise nesting_error(opened[0], opened[1])
            elif isinstance(token, _YAML_CLOSE):
                depth = max(0, depth - 1)
    except InputError:
        raise
    except RecursionError:
        raise nesting_error(opened[0], opened[1]) from None
    except ReaderError as exc:
        error = _reader_utf8_error(text, exc)
        if error is not None:
            raise error from None
        return opened
    except YAMLError:
        return opened
    return opened


def _mark(token: object, fallback: tuple[int, int]) -> tuple[int, int]:
    mark = getattr(token, "start_mark", None)
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        return line + 1, column + 1
    return fallback


def _reader_utf8_error(text: str | bytes, exc: ReaderError) -> InputError | None:
    if not isinstance(text, bytes) or exc.encoding != "utf-8":
        return None
    position = exc.position
    if not isinstance(position, int):
        return None
    return utf8_input_error(text, position)
