"""Tests for the JSON and YAML formatting stubs."""

from ruamel.yaml import YAML

from galley.core.format import format_document, format_json, format_yaml


def test_format_json_pretty_prints() -> None:
    assert format_json('{"a":1}') == '{\n  "a": 1\n}\n'


def test_format_json_nested_value() -> None:
    formatted = format_json('{"b": 1, "a": [2, 3]}')
    assert formatted == '{\n  "b": 1,\n  "a": [\n    2,\n    3\n  ]\n}\n'


def test_format_yaml_round_trip() -> None:
    formatted = format_yaml("a: 1\nb:\n  - 2\n  - 3\n")
    loaded = YAML(typ="safe").load(formatted)
    assert loaded == {"a": 1, "b": [2, 3]}
    assert formatted.endswith("\n")


def test_format_yaml_keeps_comments_and_key_order() -> None:
    formatted = format_yaml("b: 1  # keep\na: 2\n")
    assert formatted.index("b:") < formatted.index("a:")
    assert "# keep" in formatted
    assert YAML(typ="safe").load(formatted) == {"b": 1, "a": 2}


def test_format_document_converts_json_to_yaml() -> None:
    formatted = format_document('{"b": 1, "a": [2, 3]}', "JSON", "YAML")
    assert YAML(typ="safe").load(formatted) == {"b": 1, "a": [2, 3]}
    assert formatted.index("b:") < formatted.index("a:")
