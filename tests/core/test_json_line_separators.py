"""JSON output escapes U+2028 and U+2029; YAML output keeps them (#221).

ui-spec §13 records the escape. A no-break space stays as it is in every
output. All data here is fake.
"""

import json

import pytest

from galley.core.convert_python import ConvertResult, convert_python
from galley.core.escape import escape_document, unescape_document
from galley.core.format import format_document

_SEPARATORS = (" ", " ")


def _assert_escaped(written: str) -> None:
    for char in _SEPARATORS:
        assert char not in written
    assert "\\u2028" in written
    assert "\\u2029" in written


@pytest.mark.parametrize(
    "text",
    [
        pytest.param('{"note": "one two three"}', id="raw"),
        pytest.param('{"note": "one\\u2028two\\u2029three"}', id="escaped"),
    ],
)
@pytest.mark.parametrize("minify", [False, True])
def test_json_to_json_escapes_line_and_paragraph_separators(
    text: str, minify: bool
) -> None:
    written = format_document(text, "json", "json", minify=minify)
    _assert_escaped(written)
    assert json.loads(written) == {"note": "one two three"}


def test_yaml_to_json_escapes_line_and_paragraph_separators() -> None:
    written = format_document('note: "one\\Ltwo\\Pthree"\n', "yaml", "json")
    _assert_escaped(written)
    assert json.loads(written) == {"note": "one two three"}


def test_escape_as_string_and_convert_escape_them_too() -> None:
    escaped = escape_document('{"note": "one\\u2028two\\u2029three"}', "json")
    _assert_escaped(escaped)
    assert json.loads(unescape_document(escaped, "json")) == {
        "note": "one two three"
    }
    converted = convert_python("{'note': 'one\\u2028two\\u2029three'}", "json")
    assert isinstance(converted, ConvertResult)
    _assert_escaped(converted.text)
    assert json.loads(converted.text) == {"note": "one two three"}


def test_json_output_keeps_a_no_break_space() -> None:
    written = format_document('{"name": "Pat Example"}', "json", "json")
    assert written == '{\n  "name": "Pat Example"\n}\n'


@pytest.mark.parametrize(
    ("text", "source", "expected"),
    [
        ('note: "one two"\n', "yaml", "note: 'one   two'\n"),
        ('note: "one two"\n', "yaml", "note: 'one   two'\n"),
        ('{"note": "one two"}', "json", "note: 'one   two'\n"),
        ('{"note": "one\\u2029two"}', "json", "note: 'one   two'\n"),
        ('name: "Pat Example"\n', "yaml", "name: Pat Example\n"),
    ],
)
def test_yaml_output_is_unchanged(text: str, source: str, expected: str) -> None:
    assert format_document(text, source, "yaml") == expected
