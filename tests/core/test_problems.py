"""Syntax problems stay structured and point at the line a person can fix."""

import json

import pytest

from galley.core.format import format_json, format_yaml
from galley.core.problems import Problem, collect_problems, leading_tab_lines


def _json_problem(text: str) -> Problem:
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json(text)
    found = collect_problems(text, "JSON", caught.value)
    assert len(found) == 1
    return found[0]


def _yaml_problem(text: str) -> Problem:
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    assert not isinstance(caught.value, RecursionError)
    found = collect_problems(text, "YAML", caught.value)
    assert len(found) == 1
    return found[0]


def test_missing_comma_points_at_the_previous_nonblank_line() -> None:
    text = '{\n  "price": 4.5\n  "qty": 3\n}\n'
    assert _json_problem(text) == Problem("missing_comma", 2)
    blank = '{\n  "a": 1\n\n  "b": 2\n}\n'
    assert _json_problem(blank) == Problem("missing_comma", 2)


def test_missing_comma_on_the_same_line_keeps_that_line() -> None:
    assert _json_problem('{"price": 4.5 "qty": 3}') == Problem("missing_comma", 1)


def test_mapped_json_errors_keep_a_single_line() -> None:
    assert _json_problem('{"a": "hi}') == Problem("unclosed_quote", 1)
    assert _json_problem('{\n  "a": "hi\n}\n') == Problem("unclosed_quote", 2)
    assert _json_problem('{"a": 1,}') == Problem("trailing_comma", 1)
    assert _json_problem("[1,]") == Problem("trailing_comma", 1)
    assert _json_problem("[\n  1,\n]\n") == Problem("trailing_comma", 2)
    assert _json_problem('{"a": 1}}') == Problem("unexpected_close", 1, "}")
    assert _json_problem("[1]]") == Problem("unexpected_close", 1, "]")
    assert _json_problem("{]") == Problem("unexpected_close", 1, "]")
    assert _json_problem("[}") == Problem("unexpected_close", 1, "}")
    assert _json_problem('{"a": 1') == Problem("incomplete", 1)
    assert _json_problem("{") == Problem("incomplete", 1)
    assert _json_problem('{"a":') == Problem("incomplete", 1)
    assert _json_problem("[1, 2") == Problem("incomplete", 1)
    assert _json_problem("abc") == Problem("json_other", 1)


def test_a_second_json_error_is_not_reported_yet() -> None:
    text = '{\n  "a": 1\n  "b": 2\n  "c": 3\n}\n'
    found = collect_problems(text, "json", _decode(text))
    assert found == (Problem("missing_comma", 2),)


def test_yaml_tab_scan_lists_every_leading_tab_and_stops() -> None:
    text = "a:\n\tb: 1\n\tc: 2\n"
    assert leading_tab_lines(text) == [2, 3]
    assert collect_problems(text, "YAML") == (
        Problem("yaml_tab", 2),
        Problem("yaml_tab", 3),
    )
    mixed = "a: 1\n\tb: [\n\tc: 2\n"
    assert collect_problems(mixed, "yaml") == (
        Problem("yaml_tab", 2),
        Problem("yaml_tab", 3),
    )


def test_tabs_after_content_are_not_indent_errors() -> None:
    assert leading_tab_lines('note: "has\ttab"\n') == []
    assert leading_tab_lines("a:\n  b: 1\n") == []


def test_yaml_errors_use_a_line_and_plain_codes() -> None:
    assert _yaml_problem("a: 1\na: 2\n") == Problem("yaml_duplicate", 2)
    assert _yaml_problem('a: "hi\n') == Problem("yaml_incomplete", 1)
    assert _yaml_problem("a: 1\nb\n") == Problem("yaml_colon", 2)
    other = _yaml_problem("a: [1, 2\n")
    assert other.code == "yaml_other"
    assert other.lineno == 1


def _decode(text: str) -> json.JSONDecodeError:
    try:
        format_json(text)
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("invalid JSON was accepted")
