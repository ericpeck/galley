"""Minify writes compact JSON through Format's JSON path."""

import json
from queue import SimpleQueue

import pytest

import galley.core.escape as escape_module
import galley.core.format as format_module
from galley.core.errors import InputError, JSONDuplicateKeyError
from galley.core.escape import escape_value
from galley.core.format import compact_json, format_document, format_json
from galley.core.worker import FormatJob, SuccessEvent, _execute

_PRETTY = '{\n  "name": "Ann",\n  "tags": [\n    "a",\n    "b"\n  ],\n  "n": 1.5\n}\n'


def test_compact_json_has_no_spaces_or_line_breaks() -> None:
    value = {"b": [1, {"c": None}], "a": "x y", "é": "😀"}
    assert compact_json(value) == '{"b":[1,{"c":null}],"a":"x y","é":"😀"}'


def test_escape_as_string_writes_compact_json_through_the_shared_function(
    monkeypatch,
) -> None:
    """Escape and Minify share one compact writer, so they cannot drift."""
    calls: list[object] = []

    def spy(value: object, **kwargs: object) -> str:
        calls.append(value)
        return compact_json(value, **kwargs)

    monkeypatch.setattr(escape_module, "compact_json", spy)
    assert escape_value({"a": [1, 2]}) == '"{\\"a\\":[1,2]}"\n'
    assert calls == [{"a": [1, 2]}]


def test_minify_writes_compact_json_with_no_trailing_newline() -> None:
    assert (
        format_document(_PRETTY, "json", "json", minify=True)
        == '{"name":"Ann","tags":["a","b"],"n":1.5}'
    )
    assert format_json(_PRETTY, minify=True) == format_document(
        _PRETTY, "json", "JSON", minify=True
    )


def test_minify_keeps_text_unicode_and_key_order() -> None:
    text = '{ "z": "café 日本語 😀", "a": "say \\"hi\\"\\n", "m": [ ] }'
    assert format_json(text, minify=True) == (
        '{"z":"café 日本語 😀","a":"say \\"hi\\"\\n","m":[]}'
    )


def test_minify_of_a_scalar_document() -> None:
    assert format_json("  42  \n", minify=True) == "42"


def test_minify_matches_the_compact_value_of_format() -> None:
    text = json.dumps({"rows": [{"id": i, "v": [i, None, True]} for i in range(50)]})
    formatted = format_document(text, "json", "json")
    assert format_document(text, "json", "json", minify=True) == compact_json(
        json.loads(formatted)
    )


def test_minify_can_sort_keys_like_format() -> None:
    assert format_json(
        '{"b": 1, "a": {"d": 2, "c": 3}}', minify=True, sort_keys=True
    ) == ('{"a":{"c":3,"d":2},"b":1}')


def test_minify_raises_formats_errors() -> None:
    with pytest.raises(json.JSONDecodeError) as minified:
        format_json('{"a": 1,}', minify=True)
    with pytest.raises(json.JSONDecodeError) as formatted:
        format_json('{"a": 1,}')
    assert (minified.value.lineno, minified.value.colno) == (
        formatted.value.lineno,
        formatted.value.colno,
    )
    with pytest.raises(JSONDuplicateKeyError):
        format_json('{"a": 1, "a": 2}', minify=True)
    with pytest.raises(InputError):
        format_json('{"a": "\\ud800"}', minify=True)


def test_minify_takes_the_character_walk_when_format_does(monkeypatch) -> None:
    monkeypatch.setattr(
        format_module, "_json_fast_value", lambda source: format_module._CHARACTER_WALK
    )
    assert format_json('{"a": [1, 2]}', minify=True) == '{"a":[1,2]}'


@pytest.mark.parametrize(
    ("source", "target"),
    [("yaml", "yaml"), ("json", "yaml"), ("yaml", "json"), ("xml", "xml")],
)
def test_minify_is_json_to_json_only(source: str, target: str) -> None:
    with pytest.raises(ValueError):
        format_document("a", source, target, minify=True)


def test_the_worker_minifies_and_finds_no_folds() -> None:
    events: SimpleQueue = SimpleQueue()
    job = FormatJob(job_id=7, text=_PRETTY, source="JSON", target="JSON", minify=True)
    _execute(job, events)
    events.get_nowait()
    done = events.get_nowait()
    assert isinstance(done, SuccessEvent)
    assert done.text == '{"name":"Ann","tags":["a","b"],"n":1.5}'
    assert done.folds == b""
