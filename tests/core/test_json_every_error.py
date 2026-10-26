"""A JSON document lists every syntax error, not just the first (#78)."""

import json
import os

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from galley.core.format import format_json
from galley.core.json_recover import MAX_FINDINGS, scan_json
from galley.core.problems import Problem, classify_failure, collect_problems

_SETTINGS = settings(
    max_examples=400, deadline=None, derandomize=bool(os.environ.get("CI"))
)

Row = tuple[str, int, int, str, bool]


def _problems(text: str) -> tuple[Problem, ...]:
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json(text)
    return collect_problems(text, "JSON", caught.value)


def _rows(text: str) -> list[Row]:
    """Code, line, column, token, and whether a guess came first."""
    return [
        (item.code, item.lineno, item.colno, item.token, item.uncertain)
        for item in _problems(text)
    ]


def test_missing_commas_on_every_line_are_listed() -> None:
    text = '{\n  "a": 1\n  "b": 2\n  "c": 3\n}\n'
    assert _rows(text) == [
        ("missing_comma", 2, 9, "", False),
        ("missing_comma", 3, 9, "", False),
    ]


def test_missing_commas_on_one_line_keep_their_columns() -> None:
    assert _rows("[1 2 3]") == [
        ("missing_comma", 1, 3, "", False),
        ("missing_comma", 1, 5, "", False),
    ]


def test_trailing_commas_in_arrays_and_objects() -> None:
    text = '{\n  "a": [1, 2,],\n  "b": {"x": 1,},\n}\n'
    assert _rows(text) == [
        ("trailing_comma", 2, 13, "", False),
        ("trailing_comma", 3, 15, "", False),
        ("trailing_comma", 3, 17, "", False),
    ]


def test_unquoted_and_single_quoted_keys() -> None:
    text = "{\n  name: \"x\",\n  'age': 3,\n  city: 'Springfield'\n}\n"
    assert _rows(text) == [
        ("unquoted_key", 2, 3, "", False),
        ("single_quotes", 3, 3, "", False),
        ("unquoted_key", 4, 3, "", False),
        ("single_quotes", 4, 9, "", False),
    ]


def test_the_issue_example_shows_two_problems() -> None:
    # #78: a missing comma on line 2 and an unclosed quote on line 4.
    text = '{\n  "a": 1\n  "b": 2,\n  "c": "hello,\n  "d": 4\n}\n'
    assert _rows(text) == [
        ("missing_comma", 2, 9, "", False),
        ("unclosed_quote", 4, 8, "", False),
    ]


def test_an_unclosed_quote_hides_the_comma_error_right_after_it() -> None:
    # The quote may have swallowed the comma, so one mistake is one row.
    text = '{\n  "a": "hello\n  "b": 2\n}\n'
    assert _rows(text) == [("unclosed_quote", 2, 8, "", False)]


def test_later_problems_after_a_guess_are_marked() -> None:
    text = '{\n  "a": "x,\n  "b": 2,\n  "c": "hello\n}\n'
    assert _rows(text) == [
        ("unclosed_quote", 2, 8, "", False),
        ("unclosed_quote", 4, 8, "", True),
    ]


def test_an_unclosed_string_at_the_end_stops_quietly() -> None:
    # The brackets it leaves open are not reported again.
    assert _rows('{"a": [1 2, "b') == [
        ("missing_comma", 1, 9, "", False),
        ("unclosed_quote", 1, 13, "", False),
    ]


def test_a_missing_close_bracket_is_one_problem() -> None:
    text = '{\n  "tags": [1, 2\n}\n'
    assert _rows(text) == [("missing_close", 2, 11, "[", False)]
    # The first error keeps today's words. The brace then closes the array
    # and its object, so line 5 is still read inside the outer object.
    nested = '{\n  "a": {\n    "b": [1,\n  },\n  "c": 3 "d": 4\n}\n'
    assert _rows(nested) == [
        ("trailing_comma", 3, 12, "", False),
        ("missing_comma", 5, 9, "", True),
    ]


def test_a_mistyped_closer_closes_the_inner_bracket() -> None:
    assert _rows('{"a": [1, 2}, "b": 3 "c": 4}') == [
        ("missing_close", 1, 7, "[", False),
        ("missing_comma", 1, 21, "", True),
    ]


def test_cascades_after_an_unclosed_array_stay_short() -> None:
    text = '{\n  "a": [1, 2,\n  "b": 3\n}\n'
    assert _rows(text) == [
        ("missing_close", 2, 8, "[", True),
        ("missing_comma", 3, 6, "", False),
    ]


def test_repeated_keys_and_syntax_errors_share_text_order() -> None:
    text = '{\n  "a": 1,\n  "a": 2\n  "b": 3,\n  "b": 4,\n}\n'
    assert _rows(text) == [
        ("json_duplicate", 3, 3, "a", False),
        ("missing_comma", 3, 9, "", False),
        ("json_duplicate", 5, 3, "b", False),
        ("trailing_comma", 5, 9, "", False),
    ]


def test_repeated_keys_compare_after_escapes() -> None:
    assert _rows('{"a": 1, "\\u0061": 2 "c": 3}') == [
        ("json_duplicate", 1, 10, "a", False),
        ("missing_comma", 1, 21, "", False),
    ]


def test_crlf_lines_match_lf_lines() -> None:
    text = '{\n  "a": 1\n  "b": "x\n  "c": 3,\n}\n'
    crlf = text.replace("\n", "\r\n")
    assert _rows(crlf) == _rows(text)
    assert _rows(text) == [
        ("missing_comma", 2, 9, "", False),
        ("unclosed_quote", 3, 8, "", False),
        ("trailing_comma", 4, 9, "", True),
    ]


def test_a_bom_is_reported_and_the_rest_is_checked() -> None:
    text = '﻿{\n  "a": 1\n  "b": 2\n}\n'
    assert _rows(text) == [
        ("json_other", 1, 1, "", False),
        ("missing_comma", 2, 9, "", False),
    ]


def test_comments_are_skipped_after_their_error() -> None:
    # A comment after a value reads as a missing comma, as it does today.
    text = '{\n  // note\n  "a": 1\n  "b": 2 /* why */\n}\n'
    assert _rows(text) == [
        ("json_other", 2, 3, "", False),
        ("missing_comma", 3, 9, "", False),
        ("missing_comma", 4, 9, "", False),
    ]


def test_json_lines_check_every_document() -> None:
    text = '{"a": 1}\n{"a": 2 "b": 3}\n{"c": 4,}\n'
    assert _rows(text) == [
        ("json_other", 2, 1, "", False),
        ("missing_comma", 2, 8, "", False),
        ("trailing_comma", 3, 8, "", False),
    ]


def test_bad_escapes_and_control_characters_do_not_end_the_string() -> None:
    text = '["a\\x", "b\tc" "d"]'
    assert _rows(text) == [
        ("json_other", 1, 4, "", False),
        ("json_other", 1, 11, "", False),
        ("missing_comma", 1, 14, "", False),
    ]


def test_end_of_text_names_the_innermost_open_bracket() -> None:
    assert _rows('[1 2, {"a": [3') == [
        ("missing_comma", 1, 3, "", False),
        ("missing_close", 1, 13, "[", False),
    ]
    assert _rows("") == [("incomplete", 1, 1, "", False)]


def test_one_line_of_junk_is_one_problem_per_token() -> None:
    text = "[{'a': 1, 'b': True, 'c': None}]"
    assert [row[0] for row in _rows(text)] == [
        "single_quotes",
        "single_quotes",
        "json_other",
        "single_quotes",
        "json_other",
    ]


def test_the_scan_stops_at_its_limit_and_says_where() -> None:
    text = "[" + " ".join(["1"] * (MAX_FINDINGS + 50)) + "]"
    found = _problems(text)
    assert len(found) == MAX_FINDINGS + 1
    assert all(item.code == "missing_comma" for item in found[:-1])
    stop = found[-1]
    assert stop.code == "json_stopped"
    # It stops where the next problem would have been.
    assert stop.lineno == 1
    assert stop.colno > found[-2].colno


def test_findings_under_the_limit_have_no_stop_row() -> None:
    text = "[" + " ".join(["1"] * 20) + "]"
    assert [item.code for item in _problems(text)] == ["missing_comma"] * 19


def test_a_large_valid_prefix_is_skipped_by_the_decoder() -> None:
    rows = [{"id": index, "tags": ["a", "b"]} for index in range(3000)]
    text = json.dumps(rows, indent=2)
    broken = text[: -len("\n  }\n]")] + '\n  "x": 1\n  }\n]'
    found = _rows(broken)
    assert [row[0] for row in found] == ["missing_comma"]


def test_a_repeat_inside_a_skippable_bracket_is_still_found() -> None:
    text = '[{"a": 1, "a": 2}, {"b": 1} {"c": 2}]'
    assert _rows(text) == [
        ("json_duplicate", 1, 11, "a", False),
        ("missing_comma", 1, 28, "", False),
    ]


def test_the_old_first_problem_is_kept_when_the_scan_disagrees(monkeypatch) -> None:
    import galley.core.problems as problems

    text = '{\n  "a": 1\n  "b": 2\n}\n'
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(text)
    real = problems.scan_json

    def shifted(source: str, limit: int = MAX_FINDINGS):
        scan = real(source, limit)
        first = scan.slips[0]
        moved = type(first)(first.message, first.pos + 1, None, 0, False)
        return type(scan)((moved, *scan.slips[1:]), scan.repeats, None)

    monkeypatch.setattr(problems, "scan_json", shifted)
    assert collect_problems(text, "JSON", caught.value) == (
        classify_failure(caught.value, text),
    )


_PIECES = st.sampled_from(
    [
        "{",
        "}",
        "[",
        "]",
        ":",
        ",",
        '"',
        "'",
        "\\",
        "/",
        "*",
        " ",
        "\n",
        "\r\n",
        "\t",
        "a",
        "1",
        "-",
        ".",
        "e",
        "true",
        "nul",
        "NaN",
        "-Infinity",
        "\\u12",
        "\\uD83D",
        "﻿",
        "\x00",
    ]
)
_SEEDS = st.sampled_from(
    [
        '{"a": [1, 2.5, true, null, "x\\ny"], "b": {"c": {}, "d": []}}',
        '[{"id": 1, "t": ["p"]}, {"id": 2, "o": {"k": "v"}}]',
        '{\n  "a": 1,\n  "b": [\n    "x",\n    {"c": NaN}\n  ]\n}\n',
    ]
)


@st.composite
def _broken_json(draw: st.DrawFn) -> str:
    text = draw(_SEEDS)
    for _ in range(draw(st.integers(min_value=1, max_value=5))):
        index = draw(st.integers(min_value=0, max_value=len(text)))
        if draw(st.booleans()):
            text = text[:index] + draw(_PIECES) + text[index:]
        else:
            text = text[:index] + text[index + 1 :]
    return text


@_SETTINGS
@given(_broken_json())
@example('"\\ud83d\\ude00')
@example('{"a": "\\u0041')
@example("﻿")
def test_the_first_error_is_the_one_json_reports(text: str) -> None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        expected = exc
    else:
        return
    first = scan_json(text).slips[0]
    assert (first.message, first.pos) == (expected.msg, expected.pos)
    found = collect_problems(text, "JSON", expected)
    old = classify_failure(expected, text)
    assert old is not None
    assert (old.code, old.lineno, old.token) in {
        (item.code, item.lineno, item.token) for item in found
    }
    rows = [item for item in found if item.code != "json_stopped"]
    places = [(item.lineno, item.colno) for item in rows]
    assert places == sorted(places)
