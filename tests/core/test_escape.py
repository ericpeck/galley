"""Escape as String and Unescape String round trips and errors."""

import json

import pytest
from ruamel.yaml import YAML

from galley.core.errors import MAX_DEPTH, InputError
from galley.core.escape import (
    EscapeError,
    UnescapeError,
    escape_document,
    escape_value,
    unescape_document,
)
from galley.core.format import format_document

_YAML = YAML(typ="safe")

ROUND_TRIP_JSON = [
    '{"a": "b"}',
    '{"outer": {"inner": {"deep": [1, 2, {"x": null}]}}}',
    "[1, [2, [3, [4]]], {}, []]",
    "{}",
    "[]",
    '"plain string"',
    "42",
    "-1.5e3",
    "true",
    "null",
    '{"quote": "she said \\"hi\\"", "slash": "C:\\\\path\\\\to"}',
    '{"lines": "one\\ntwo\\r\\nthree", "tab": "a\\tb"}',
    '{"control": "\\u0000\\u0001\\u001f\\b\\f"}',
    '{"unicode": "café naïve 日本語 Ελληνικά"}',
    '{"emoji": "😀 👍🏽 👨‍👩‍👧", "pair": "\\ud83d\\ude00"}',
    '{"separators": "\\u2028 \\u2029"}',
    '{"ünï 😀 key": ["\\"", "\\\\", "/"]}',
    '{"escaped already": "{\\"a\\":\\"b\\"}"}',
]


def _value(text: str, target: str) -> object:
    if target == "json":
        return json.loads(text)
    return _YAML.load(text)


def test_escape_writes_compact_json_inside_one_string_literal() -> None:
    assert escape_document('{"a": "b"}', "json") == '"{\\"a\\":\\"b\\"}"\n'
    assert escape_document('{\n  "a": [1, 2]\n}\n', "json") == '"{\\"a\\":[1,2]}"\n'
    assert escape_document("{}", "json") == '"{}"\n'
    assert escape_document("[]", "json") == '"[]"\n'


def test_escape_reads_yaml_input() -> None:
    escaped = escape_document("a: b\nn: [1, 2]\n", "yaml")
    assert escaped == '"{\\"a\\":\\"b\\",\\"n\\":[1,2]}"\n'
    assert escape_document("a: 1\n", "YAML") == '"{\\"a\\":1}"\n'


def test_escape_keeps_non_ascii_and_escapes_quotes_backslashes_and_controls() -> None:
    escaped = escape_document('{"k": "é 😀 \\" \\\\ \\n \\t \\u0001"}', "json")
    assert escaped == '"{\\"k\\":\\"é 😀 \\\\\\" \\\\\\\\ \\\\n \\\\t \\\\u0001\\"}"\n'
    assert "é" in escaped
    assert "😀" in escaped
    assert "\n" not in escaped.rstrip("\n")
    assert "\t" not in escaped


def test_escape_output_is_one_json_string_holding_compact_json() -> None:
    for text in ROUND_TRIP_JSON:
        escaped = escape_document(text, "json")
        assert escaped.endswith("\n")
        inner = json.loads(escaped)
        assert isinstance(inner, str)
        assert json.loads(inner) == json.loads(text)
        value = json.loads(text)
        assert inner == json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("text", ROUND_TRIP_JSON)
@pytest.mark.parametrize("target", ["json", "yaml"])
def test_unescape_of_escape_matches_format(text: str, target: str) -> None:
    unescaped = unescape_document(escape_document(text, "json"), target)
    formatted = format_document(text, "json", target)
    assert _value(unescaped, target) == _value(formatted, target)
    if target == "json":
        assert unescaped == formatted


@pytest.mark.parametrize(
    "text",
    [
        "a: b\nlist:\n  - 1\n  - two\n  - {c: d}\n",
        "quote: 'she said \"hi\"'\nslash: 'C:\\path'\n",
        'emoji: "😀 \\U0001F44D"\nmulti: |\n  line one\n  line two\n',
        "anchor: &x [1, 2]\nalias: *x\n",
        "{}\n",
        "[]\n",
    ],
)
@pytest.mark.parametrize("target", ["json", "yaml"])
def test_yaml_round_trip_matches_format(text: str, target: str) -> None:
    unescaped = unescape_document(escape_document(text, "yaml"), target)
    formatted = format_document(text, "yaml", target)
    assert _value(unescaped, target) == _value(formatted, target)


def test_yaml_keys_that_are_not_strings_become_strings_as_in_format_to_json() -> None:
    text = "1: one\n2.5: two\n"
    assert (
        escape_document(text, "yaml") == '"{\\"1\\":\\"one\\",\\"2.5\\":\\"two\\"}"\n'
    )
    unescaped = unescape_document(escape_document(text, "yaml"), "yaml")
    assert _YAML.load(unescaped) == _YAML.load(format_document(text, "yaml", "json"))


def test_unescape_allows_whitespace_around_the_literal() -> None:
    assert unescape_document('  \n"{\\"a\\":1}"\n\n', "json") == '{\n  "a": 1\n}\n'


def test_unescape_formats_with_the_target_format() -> None:
    text = '"{\\"a\\":[1,2],\\"b\\":\\"é\\"}"'
    assert unescape_document(text, "json") == format_document(
        '{"a": [1, 2], "b": "é"}', "json", "json"
    )
    assert unescape_document(text, "YAML") == "a:\n- 1\n- 2\nb: é\n"


@pytest.mark.parametrize(
    "text",
    [
        '{"a": "b"}',
        "[1, 2]",
        "42",
        "null",
        "",
        "   ",
        "not json at all",
        '"unclosed',
        '"one" "two"',
        "'{\"a\":1}'",
        "a: b\n",
    ],
)
def test_unescape_rejects_text_that_is_not_one_json_string(text: str) -> None:
    with pytest.raises(UnescapeError) as raised:
        unescape_document(text, "json")
    assert raised.value.kind == "not_string"


@pytest.mark.parametrize(
    "text",
    [
        '"hello"',
        '""',
        '"{\\"a\\":}"',
        '"{a: 1}"',
        "\"{'a': 1}\"",
        '"[1, 2,]"',
        '"\\"\\\\ud800\\""',
        '"' + "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1) + '"',
    ],
)
def test_unescape_rejects_a_string_whose_contents_are_not_json(text: str) -> None:
    with pytest.raises(UnescapeError) as raised:
        unescape_document(text, "json")
    assert raised.value.kind == "not_json"


def test_unescape_reports_a_repeated_key_in_the_string_contents() -> None:
    text = '"{\\"a\\":1,\\"b\\":{\\"c\\":2,\\"c\\":3},\\"a\\":4}"'
    with pytest.raises(UnescapeError) as raised:
        unescape_document(text, "json")
    assert raised.value.kind == "duplicate_key"
    assert [(item.key, item.lineno) for item in raised.value.duplicates] == [
        ("c", 1),
        ("a", 1),
    ]


def test_unescape_peels_one_layer_of_a_double_escaped_string() -> None:
    once = escape_document('{"a": "b"}', "json")
    twice = escape_value(json.loads(once))
    assert twice == '"\\"{\\\\\\"a\\\\\\":\\\\\\"b\\\\\\"}\\""\n'
    peeled = unescape_document(twice, "json")
    assert peeled == once
    assert unescape_document(peeled, "json") == '{\n  "a": "b"\n}\n'


def test_unescape_rejects_an_unknown_target() -> None:
    with pytest.raises(ValueError, match="Unsupported format"):
        unescape_document('"{}"', "toml")


@pytest.mark.parametrize(
    "text",
    [
        "day: 2020-01-01\n",
        "blob: !!binary aGk=\n",
        "set: !!set {a, b}\n",
        "? [1, 2]\n: pair key\n",
        "loop: &x [1, *x]\n",
    ],
)
def test_escape_rejects_yaml_values_json_cannot_hold(text: str) -> None:
    with pytest.raises(EscapeError):
        escape_document(text, "yaml")
    with pytest.raises((TypeError, ValueError)):
        format_document(text, "yaml", "json")


def test_escape_raises_the_same_parse_errors_as_format() -> None:
    with pytest.raises(json.JSONDecodeError):
        escape_document('{"a": 1,}', "json")
    with pytest.raises(InputError) as raised:
        escape_document("[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1), "json")
    assert raised.value.kind == "too_deep"
    with pytest.raises(InputError) as raised:
        escape_document('{"a": "\\ud800"}', "json")
    assert raised.value.kind == "bad_escape"
