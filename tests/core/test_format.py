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


def test_format_yaml_keeps_the_mapping() -> None:
    formatted = format_yaml("b: 1  # keep\na: 2\n")
    assert YAML(typ="safe").load(formatted) == {"b": 1, "a": 2}


def test_format_document_converts_json_to_yaml() -> None:
    formatted = format_document('{"b": 1, "a": [2, 3]}', "JSON", "YAML")
    assert YAML(typ="safe").load(formatted) == {"b": 1, "a": [2, 3]}
    assert formatted == format_yaml("b: 1\na:\n  - 2\n  - 3\n")


def test_format_yaml_preserves_mapping_key_order() -> None:
    formatted = format_yaml("z: 1\na:\n  y: 2\n  b: 3\n")
    assert formatted == "z: 1\na:\n  y: 2\n  b: 3\n"


def test_format_document_preserves_json_key_order_in_yaml() -> None:
    formatted = format_document('{"z": 1, "a": {"y": 2, "b": 3}}', "JSON", "YAML")
    assert formatted == "z: 1\na:\n  y: 2\n  b: 3\n"


def test_format_document_preserves_yaml_key_order_in_json() -> None:
    formatted = format_document("z: 1\na:\n  y: 2\n  b: 3\n", "YAML", "JSON")
    assert formatted == ('{\n  "z": 1,\n  "a": {\n    "y": 2,\n    "b": 3\n  }\n}\n')
