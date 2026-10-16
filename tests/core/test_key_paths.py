"""Dotted and bracketed paths over formatted JSON and YAML.

Keys that contain dots, brackets, or non-ASCII characters stay single keys.
Array indexes are zero-based. Missing keys and indexes fail. Formatting twice
does not change the text or the value at a path.
"""

import json
from io import StringIO
from typing import NoReturn

import pytest
from ruamel.yaml import YAML

from galley.core.format import format_json, format_yaml


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


DOCUMENT: dict[str, object] = {
    "order": {
        "items": [
            {"price": 1, "name": "a.b"},
            {"price": 2},
            {"price": 3, "sku": "x[0]"},
        ],
        "a.b": {"c": 4},
    },
    "订单": {"café": ["é", "ñ"]},
    "a[0]": 5,
    "0": {"n": 6},
    'a"b': 7,
}


def _yaml_text(data: object) -> str:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    buffer = StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def _formatted_json() -> tuple[str, object]:
    formatted = format_json(json.dumps(DOCUMENT, ensure_ascii=False))
    return formatted, json.loads(formatted)


def _formatted_yaml() -> tuple[str, object]:
    formatted = format_yaml(_yaml_text(DOCUMENT))
    return formatted, YAML(typ="safe").load(formatted)


@pytest.mark.parametrize(
    "segments",
    [
        ["order", "items", 2, "price"],
        ["a.b", "c[0]"],
        ["订单", "café", 0],
        ["a", "b c"],
        [0, "price"],
        ['a"b'],
        [],
    ],
)
def test_path_render_parse_round_trip(segments: list[str | int]) -> None:
    rendered = render_path(segments)
    assert parse_path(rendered) == segments
    assert render_path(parse_path(rendered)) == rendered


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (".a", "empty path segment"),
        ("a..b", "empty path segment"),
        ("a.", "empty path segment"),
        ("items[-1]", "negative index"),
        ("items[01]", "invalid index"),
        ("items[", "unclosed bracket"),
    ],
)
def test_path_syntax_errors_name_a_column(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as caught:
        parse_path(path)
    assert "column" in str(caught.value)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("order.items[2].price", 3),
        ("order.items[0].name", "a.b"),
        ('["order"]["a.b"].c', 4),
        ("order.items[2].sku", "x[0]"),
        ("订单.café[1]", "ñ"),
        ('["a[0]"]', 5),
        ('["0"].n', 6),
        ('["a\\"b"]', 7),
    ],
)
def test_json_paths_resolve_after_format(path: str, expected: object) -> None:
    formatted, loaded = _formatted_json()
    assert lookup(loaded, path) == expected
    assert format_json(formatted) == formatted
    assert "\ufeff" not in formatted


def test_bracket_and_dotted_paths_agree() -> None:
    _formatted, loaded = _formatted_json()
    dotted = "order.items[2].price"
    bracketed = '["order"]["items"][2]["price"]'
    assert lookup(loaded, dotted) == lookup(loaded, bracketed) == 3


def test_keys_with_dots_are_not_split_by_formatting() -> None:
    _formatted, loaded = _formatted_json()
    assert lookup(loaded, '["order"]["a.b"]["c"]') == 4
    with pytest.raises(PathError) as caught:
        lookup(loaded, "order.a.b.c")
    assert caught.value.segment == "a"


def test_json_missing_key_and_index() -> None:
    formatted, loaded = _formatted_json()
    with pytest.raises(PathError) as missing_key:
        lookup(loaded, "order.missing")
    assert missing_key.value.segment == "missing"
    with pytest.raises(PathError) as missing_index:
        lookup(loaded, "order.items[3]")
    assert missing_index.value.segment == 3
    with pytest.raises(PathError) as wrong_kind:
        lookup(loaded, "order.items[0].price[0]")
    assert wrong_kind.value.segment == 0
    assert "missing" not in formatted


def test_json_root_array_index() -> None:
    formatted = format_json("[10, 20, 30]")
    loaded = json.loads(formatted)
    assert lookup(loaded, "[1]") == 20
    assert lookup(loaded, "") == [10, 20, 30]
    with pytest.raises(PathError) as caught:
        lookup(loaded, "[3]")
    assert caught.value.segment == 3
    assert format_json(formatted) == formatted


def test_json_key_order_is_stable() -> None:
    formatted = format_json('{"b": 1, "a": 2, "订单": 3}')
    assert formatted.index('"b"') < formatted.index('"a"')
    assert formatted.index('"a"') < formatted.index('"订单"')
    assert format_json(formatted) == formatted


def test_json_duplicate_key_path_uses_the_last_value() -> None:
    formatted = format_json('{"a": {"b": 1}, "a": {"b": 2}}')
    assert lookup(json.loads(formatted), "a.b") == 2


def test_yaml_paths_match_json_and_round_trip() -> None:
    json_text, json_loaded = _formatted_json()
    yaml_text, yaml_loaded = _formatted_yaml()
    paths = [
        "order.items[2].price",
        '["order"]["a.b"].c',
        "订单.café[0]",
        '["a[0]"]',
        '["0"].n',
        '["a\\"b"]',
    ]
    for path in paths:
        assert lookup(yaml_loaded, path) == lookup(json_loaded, path)
    assert format_yaml(yaml_text) == yaml_text
    assert format_json(json_text) == json_text
    with pytest.raises(PathError) as caught:
        lookup(yaml_loaded, "订单.missing")
    assert caught.value.segment == "missing"


def test_yaml_key_that_looks_like_a_path_stays_one_key() -> None:
    key = "order.items[2].price"
    formatted = format_yaml(f'"{key}": 7\n')
    loaded = YAML(typ="safe").load(formatted)
    assert lookup(loaded, '["order.items[2].price"]') == 7
    with pytest.raises(PathError) as caught:
        lookup(loaded, "order.items[2].price")
    assert caught.value.segment == "order"
