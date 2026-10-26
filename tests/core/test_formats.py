"""The format table: names, extensions, sniffing, and Output targets (#63)."""

from pathlib import Path

import pytest

from galley.core import formats
from galley.core.format import _normalize_format
from galley.core.formats import (
    FORMAT_LABELS,
    FORMATS,
    JSON,
    YAML,
    DocumentFormat,
    format_for_extension,
    format_named,
    sniff_format,
    targets_for,
)
from galley.core.load import detect_format


def test_the_table_lists_json_then_yaml() -> None:
    assert FORMATS == (JSON, YAML)
    assert FORMAT_LABELS == ("JSON", "YAML")
    assert [fmt.name for fmt in FORMATS] == ["json", "yaml"]
    assert all(fmt.loads_to_value for fmt in FORMATS)


def test_names_labels_and_extensions_are_consistent() -> None:
    names = [fmt.name for fmt in FORMATS]
    assert len(set(names)) == len(names)
    extensions = [suffix for fmt in FORMATS for suffix in fmt.extensions]
    assert len(set(extensions)) == len(extensions)
    for fmt in FORMATS:
        assert fmt.name == fmt.label.lower()
        assert all(s.startswith(".") and s == s.casefold() for s in fmt.extensions)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("json", JSON), ("JSON", JSON), (" Yaml\n", YAML), ("YAML", YAML)],
)
def test_format_named_ignores_case_and_outer_spaces(
    name: str, expected: DocumentFormat
) -> None:
    assert format_named(name) is expected
    assert _normalize_format(name) == expected.name


@pytest.mark.parametrize("name", ["", "yml", "Python", "xml", "html", "js on"])
def test_other_names_are_unsupported(name: str) -> None:
    with pytest.raises(ValueError, match=f"Unsupported format: {name}"):
        format_named(name)
    with pytest.raises(ValueError, match="Unsupported format"):
        _normalize_format(name)


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (".json", JSON),
        (".JSON", JSON),
        (".geojson", JSON),
        (".yaml", YAML),
        (".Yml", YAML),
        (".jsonc", None),
        (".json5", None),
        (".txt", None),
        (".xml", None),
        (".html", None),
        ("", None),
    ],
)
def test_format_for_extension(suffix: str, expected: DocumentFormat | None) -> None:
    assert format_for_extension(suffix) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{a: b}", JSON),
        ("[x]: y", JSON),
        ("\n\n  # c\n- a", YAML),
        ("key:", YAML),
        ("key:value", None),
        (":", None),
        ("<a/>", None),
        ("# only a comment", None),
        ("", None),
    ],
)
def test_sniff_tries_formats_in_table_order(
    text: str, expected: DocumentFormat | None
) -> None:
    assert sniff_format(text) is expected


def test_detect_format_reports_the_label() -> None:
    assert detect_format(Path("a.yml"), "{}") == "YAML"
    assert detect_format(Path("a.txt"), "{}") == "JSON"
    assert detect_format(Path("a.txt"), "words") is None


@pytest.mark.parametrize("source", ["json", "YAML"])
def test_value_formats_convert_to_each_other(source: str) -> None:
    assert targets_for(source) == (JSON, YAML)


def test_a_format_that_does_not_load_to_a_value_targets_only_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markup = DocumentFormat("markup", "MARKUP", (".mk",), lambda _: False, False)
    monkeypatch.setattr(formats, "FORMATS", (JSON, YAML, markup))
    assert targets_for("markup") == (markup,)
    assert targets_for("json") == (JSON, YAML)
    assert format_for_extension(".MK") is markup
    with pytest.raises(ValueError, match="Unsupported format"):
        targets_for("python")
