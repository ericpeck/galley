"""Dotted and bracketed paths over JSON and YAML values.

This module does not import Qt. ``parse_path`` reads ``order.items[2].price``
and ``["a.b"][0]``. ``render_path`` writes that form back. ``lookup`` walks a
value. None of this runs the path as code.
"""

import json
from typing import NoReturn


class PathError(LookupError):
    """A path segment is missing or applied to the wrong kind of value."""

    def __init__(self, path: str, segment: str | int) -> None:
        self.path = path
        self.segment = segment
        super().__init__(f"missing {segment!r} in {path!r}")


def _fail(column: int, message: str) -> NoReturn:
    raise ValueError(f"{message} (column {column})")


def _parse_json_string(path: str, start: int) -> tuple[str, int]:
    index = start + 1
    while index < len(path):
        if path[index] == "\\":
            index += 2
            continue
        if path[index] == '"':
            try:
                value = json.loads(path[start : index + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid quoted key (column {start + 1})") from exc
            if not isinstance(value, str):
                _fail(start + 1, "invalid quoted key")
            return value, index + 1
        index += 1
    _fail(start + 1, "unclosed quoted key")


def _parse_bracket(path: str, start: int) -> tuple[str | int, int]:
    index = start + 1
    if index >= len(path):
        _fail(start + 1, "unclosed bracket")
    if path[index] == '"':
        value, index = _parse_json_string(path, index)
        if index >= len(path) or path[index] != "]":
            _fail(start + 1, "unclosed bracket")
        return value, index + 1
    if path[index] == "-":
        _fail(index + 1, "negative index")
    if not path[index].isdigit():
        _fail(index + 1, "expected an index or quoted key")
    number_start = index
    while index < len(path) and path[index].isdigit():
        index += 1
    digits = path[number_start:index]
    if len(digits) > 1 and digits.startswith("0"):
        _fail(number_start + 1, "invalid index")
    if index >= len(path) or path[index] != "]":
        _fail(start + 1, "unclosed bracket")
    return int(digits), index + 1


def parse_path(path: str) -> list[str | int]:
    """Parse ``order.items[2].price`` and ``["a.b"][0]`` into segments."""
    segments: list[str | int] = []
    index = 0
    length = len(path)
    while index < length:
        char = path[index]
        if char == ".":
            if not segments or index + 1 >= length or path[index + 1] in ".[":
                _fail(index + 1, "empty path segment")
            index += 1
            start = index
            while index < length and path[index] not in ".[":
                index += 1
            segments.append(path[start:index])
            continue
        if char == "[":
            segment, index = _parse_bracket(path, index)
            segments.append(segment)
            continue
        if segments:
            _fail(index + 1, "expected '.' or '['")
        start = index
        while index < length and path[index] not in ".[":
            index += 1
        segments.append(path[start:index])
    return segments


def _is_dotted_key(key: str) -> bool:
    if not key or key.isdigit():
        return False
    forbidden = set(".[]\"' \t\r\n")
    return not any(char in forbidden for char in key)


def render_path(segments: list[str | int]) -> str:
    """Render segments in dotted form, quoting keys that contain separators."""
    chunks: list[str] = []
    for segment in segments:
        if isinstance(segment, int):
            if segment < 0:
                raise ValueError(f"negative index {segment}")
            chunks.append(f"[{segment}]")
            continue
        if _is_dotted_key(segment):
            if chunks:
                chunks.append("." + segment)
            else:
                chunks.append(segment)
            continue
        chunks.append("[" + json.dumps(segment, ensure_ascii=False) + "]")
    return "".join(chunks)


def lookup(data: object, path: str) -> object:
    """Walk ``data`` by ``path``. Missing segments raise ``PathError``."""
    current = data
    for segment in parse_path(path):
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                raise PathError(path, segment)
            current = current[segment]
            continue
        if not isinstance(current, dict) or segment not in current:
            raise PathError(path, segment)
        current = current[segment]
    return current
