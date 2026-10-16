"""Tests for the JSON and YAML formatting stubs."""

from ruamel.yaml import YAML

from galley.core.format import format_json, format_yaml


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
