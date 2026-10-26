"""Syntax problems stay structured and point at the line a person can fix."""

import json

import pytest

from galley.core.format import format_json, format_yaml
from galley.core.problems import Problem, collect_problems, leading_tab_lines


def _json_problems(text: str) -> tuple[Problem, ...]:
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json(text)
    return collect_problems(text, "JSON", caught.value)


def _json_problem(text: str) -> Problem:
    found = _json_problems(text)
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
    assert _json_problem("abc") == Problem("json_other", 1)


def test_unquoted_key_points_at_the_key_line() -> None:
    assert _json_problem("{name: 1}") == Problem("unquoted_key", 1)
    text = '{\n  "a": 1,\n  b: 2\n}\n'
    assert _json_problem(text) == Problem("unquoted_key", 3)


def test_single_quotes_point_at_the_quote_line() -> None:
    assert _json_problem("{'a': 1}") == Problem("single_quotes", 1)
    assert _json_problems("['a', 'b']") == (
        Problem("single_quotes", 1, colno=2),
        Problem("single_quotes", 1, colno=7),
    )
    text = '{\n  "a": 1,\n  "b": \'x\'\n}\n'
    assert _json_problem(text) == Problem("single_quotes", 3)


def test_missing_bracket_names_the_open_square_bracket() -> None:
    text = '{\n  "tags": [1, 2\n}\n'
    assert _json_problem(text) == Problem("missing_close", 2, "[")
    assert _json_problem("[1, 2") == Problem("missing_close", 1, "[")
    ended = '{\n  "a": [\n    1,\n    2\n'
    assert _json_problem(ended) == Problem("missing_close", 2, "[")


def test_missing_bracket_names_the_open_curly_bracket() -> None:
    text = '[\n  {"a": 1\n]\n'
    assert _json_problem(text) == Problem("missing_close", 2, "{")
    assert _json_problem('{"a": 1') == Problem("missing_close", 1, "{")
    assert _json_problem("{") == Problem("missing_close", 1, "{")
    assert _json_problem('{"a":') == Problem("missing_close", 1, "{")
    nested = '{\n  "a": {\n    "b": 1\n  }\n'
    assert _json_problem(nested) == Problem("missing_close", 1, "{")


def test_brackets_inside_strings_are_not_counted() -> None:
    text = '{\n  "note": "[{ \\" ]",\n  "a": [1\n}\n'
    assert _json_problem(text) == Problem("missing_close", 3, "[")


def test_missing_comma_between_objects_in_an_array() -> None:
    text = '[\n  {"a": 1}\n  {"b": 2}\n]\n'
    assert _json_problem(text) == Problem("missing_comma", 2)
    assert _json_problem('[{"a": 1} {"b": 2}]') == Problem("missing_comma", 1)


def test_trailing_comma_before_either_closing_bracket() -> None:
    assert _json_problem('{\n  "a": 1,\n}\n') == Problem("trailing_comma", 2)
    assert _json_problem('[\n  {"a": 1},\n]\n') == Problem("trailing_comma", 2)


def test_unclosed_quote_wins_over_the_brackets_it_leaves_open() -> None:
    assert _json_problem('{\n  "a": ["hi\n') == Problem("unclosed_quote", 2)


def test_the_most_specific_problem_wins_with_its_own_line() -> None:
    # A comma before an unquoted key is not a trailing comma, and a closing
    # brace where an array is still open is not a missing comma.
    text = (
        "{\n"
        '  "name": "Galley",\n'
        "  version: 2,\n"
        "  'kind': \"app\",\n"
        '  "tags": ["json", "yaml"\n'
        "}\n"
    )
    assert _json_problems(text) == (
        Problem("unquoted_key", 3),
        Problem("single_quotes", 4),
        Problem("missing_close", 5, "["),
    )
    text = text.replace("version", '"version"')
    assert _json_problems(text) == (
        Problem("single_quotes", 4),
        Problem("missing_close", 5, "["),
    )
    text = text.replace("'kind'", '"kind"')
    assert _json_problem(text) == Problem("missing_close", 5, "[")


def test_end_of_text_message_stays_when_no_bracket_is_open() -> None:
    assert _json_problem("") == Problem("incomplete", 1)
    assert _json_problem("   ") == Problem("incomplete", 1)


def test_a_second_json_error_is_reported_too() -> None:
    text = '{\n  "a": 1\n  "b": 2\n  "c": 3\n}\n'
    found = collect_problems(text, "json", _decode(text))
    assert found == (Problem("missing_comma", 2), Problem("missing_comma", 3))


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
    assert _yaml_problem("a: 1\na: 2\n") == Problem("yaml_duplicate", 2, "a")
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
