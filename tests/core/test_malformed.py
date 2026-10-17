"""Located errors for malformed JSON and YAML.

Truncated documents, bad escapes, invalid UTF-8, BOMs, mixed line endings,
and tab indentation must not crash. Syntax errors name a 1-based line and
column. Invalid UTF-8 names the line and column of the first bad byte.
"""

import json

import pytest
from ruamel.yaml import YAML

from galley.core.errors import InputError, utf8_input_error
from galley.core.format import format_json, format_yaml


def line_column(exc: BaseException) -> tuple[int, int]:
    """Return the 1-based line and column carried by a parse error."""
    lineno = getattr(exc, "lineno", None)
    colno = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        return lineno, colno
    mark = getattr(exc, "problem_mark", None)
    if mark is not None and isinstance(mark.line, int) and isinstance(mark.column, int):
        return mark.line + 1, mark.column + 1
    message = f"{type(exc).__name__} does not name a line and column: {exc}"
    raise AssertionError(message)


def assert_location(exc: BaseException, line: int, column: int) -> None:
    assert line_column(exc) == (line, column)
    text = str(exc)
    assert f"line {line}" in text
    assert f"column {column}" in text


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [
        ("", 1, 1),
        ("{", 1, 2),
        ("[", 1, 2),
        ('{"a": [1, 2', 1, 12),
        ('{"a":', 1, 6),
        ('"unterminated', 1, 1),
        ('{"a": "\\"}', 1, 7),
        ("[\n1,\n2,\n", 4, 1),
        ('{"a": "\\q"}', 1, 8),
        ('{"a": "\\u12"}', 1, 9),
        ('{"a":"\t"}', 1, 7),
        ('\ufeff{"a": 1}', 1, 1),
        ('{\r\n  "a": 1,\n  "b": \r\n}\n', 4, 1),
        ('{\r\n  "a": 1,\n  "b": "\\q"\r\n}\n', 3, 9),
    ],
)
def test_malformed_json_names_line_and_column(
    text: str, line: int, column: int
) -> None:
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json(text)
    assert_location(caught.value, line, column)


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [
        ("a: [1, 2", 1, 9),
        ('a: "\\q"\n', 1, 6),
        ("a:\n\tb: 1\n", 2, 1),
        ("a: 1\r\nb:\r\n\tc: 2\n", 3, 1),
        ("a: 1\r\nb: [\n", 3, 1),
        ("a: 1\na: 2\n", 2, 1),
        ("a:\n  b: 1\n  b: 2\n", 3, 3),
        ("value: !custom [1, 2]\n", 1, 8),
    ],
)
def test_malformed_yaml_names_line_and_column(
    text: str, line: int, column: int
) -> None:
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    assert not isinstance(caught.value, RecursionError)
    assert_location(caught.value, line, column)


def test_json_mixed_line_endings_format_without_carrying_cr() -> None:
    text = '{\r\n  "a": 1,\n  "b": 2\r}'
    formatted = format_json(text)
    assert json.loads(formatted) == {"a": 1, "b": 2}
    assert "\r" not in formatted
    assert formatted.endswith("\n")
    assert format_json(formatted) == formatted


def test_yaml_crlf_and_mixed_endings_round_trip() -> None:
    crlf = "a: 1\r\nb:\r\n  - 2\r\n  - 3\r\n"
    mixed = "a: 1\r\nb:\n  - 2\r\n  - 3\n"
    expected = {"a": 1, "b": [2, 3]}
    loader = YAML(typ="safe")
    for text in (crlf, mixed):
        formatted = format_yaml(text)
        assert loader.load(formatted) == expected
        assert "\r" not in formatted
        assert formatted.endswith("\n")
        assert format_yaml(formatted) == formatted


def test_json_utf8_bom_bytes_are_accepted() -> None:
    formatted = format_json(b'\xef\xbb\xbf{"a": 1}')
    assert formatted == '{\n  "a": 1\n}\n'
    assert "\ufeff" not in formatted


def test_yaml_bom_is_accepted_for_text_and_bytes() -> None:
    expected = "a: 1\n"
    assert format_yaml("\ufeffa: 1\n") == expected
    assert format_yaml(b"\xef\xbb\xbfa: 1\n") == expected


def test_json_escaped_tab_round_trips() -> None:
    formatted = format_json('{"a":"\\t"}')
    assert json.loads(formatted) == {"a": "\t"}


def test_yaml_tab_inside_a_quoted_scalar_round_trips() -> None:
    formatted = format_yaml('note: "has\\ttab"\n')
    assert YAML(typ="safe").load(formatted) == {"note": "has\ttab"}
    assert format_yaml(formatted) == formatted


def test_json_invalid_utf8_does_not_crash() -> None:
    with pytest.raises(Exception) as caught:
        format_json(b'{"a": "\xff"}')
    exc = caught.value
    assert not isinstance(exc, RecursionError)
    has_offset = getattr(exc, "start", None) == 7
    has_line = isinstance(getattr(exc, "lineno", None), int)
    assert has_offset or has_line


def test_yaml_invalid_utf8_does_not_crash() -> None:
    with pytest.raises(Exception) as caught:
        format_yaml(b'a: "\xff"\n')
    exc = caught.value
    assert not isinstance(exc, RecursionError)
    details = getattr(exc, "details", None)
    has_offset = isinstance(details, dict) and details.get("offset") == 4
    has_line = isinstance(getattr(exc, "lineno", None), int)
    assert has_offset or has_line


def test_json_invalid_utf8_names_line_and_column() -> None:
    with pytest.raises(InputError) as caught:
        format_json(b'{"a": "\xff"}')
    # Byte 0xff sits at offset 7, which is column 8 on line 1.
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 1
    assert error.colno == 8
    assert error.details == {"offset": 7}


def test_yaml_invalid_utf8_names_line_and_column() -> None:
    with pytest.raises(InputError) as caught:
        format_yaml(b'a: "\xff"\n')
    # Byte 0xff sits at offset 4, which is column 5 on line 1.
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 1
    assert error.colno == 5
    assert error.details == {"offset": 4}


def test_json_invalid_utf8_counts_newlines_before_the_bad_byte() -> None:
    with pytest.raises(InputError) as caught:
        format_json(b'{\n  "a": "\xff"}')
    # The bad byte is the 9th character on line 2, at byte offset 10.
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 2
    assert error.colno == 9
    assert error.details == {"offset": 10}


def test_invalid_utf8_column_counts_characters_not_bytes() -> None:
    # U+00E9 is two bytes. The bad byte is the next character, not the next byte.
    with pytest.raises(InputError) as caught:
        format_json(b'{"a": "\xc3\xa9\xff"}')
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 1
    assert error.colno == 9
    assert error.details == {"offset": 9}
    with pytest.raises(InputError) as caught:
        format_yaml(b'a: "\xc3\xa9\xff"\n')
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 1
    assert error.colno == 6
    assert error.details == {"offset": 6}


def test_utf8_error_falls_back_to_the_byte_offset() -> None:
    # Offset 1 cuts through an invalid prefix, so the line cannot be counted.
    error = utf8_input_error(b"\xff" * 1500, 1234)
    assert error.kind == "invalid_utf8"
    assert error.lineno is None
    assert error.colno is None
    assert error.details == {"offset": 1234}
