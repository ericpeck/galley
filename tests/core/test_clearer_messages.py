"""Clearer messages for broken JSON and Convert from Python (#195, #91 C2).

Each input is the one in #195. All data is made up. The sentences are
Bill's wording on #195.
"""

import json

import pytest

from galley.app.problem_text import message_for
from galley.core.convert_python import (
    NAME_DISPLAY_LIMIT,
    ConvertError,
    ConvertResult,
    convert_python,
)
from galley.core.json_recover import scan_json
from galley.core.problems import collect_problems

_B6 = '{\n"items": [1, 2, 3,\n"done": true\n}'
_B7 = '{\n"order": 7,\n"lines": [\n{"sku": "A-1", "qty": 2},\n{"sku": "B-7", "qty":'
_B9 = '{"name": "Ada", "active": True, "manager": None, "scores": (1, 2, 3)}'
_B10 = '{\n// the user\'s name\n"name": "Ada Example",\n"age": 36 /* years */\n}'
_NOT_PYTHON = "This isn't valid Python, so it can't be converted."
_ONLY_COMMENTS = "There's no value to convert. The text has only comments."
_ENDS_EARLY = "The text ends on line 1 before the JSON document finishes."


def _rows(text: str) -> list[tuple[str, int, int, str, bool]]:
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(text)
    return [
        (item.code, item.lineno, item.colno, item.token, item.uncertain)
        for item in collect_problems(text, "JSON", caught.value)
    ]


def _decode_error(text: str) -> json.JSONDecodeError:
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(text)
    return caught.value


def _error(text: str, target: str = "json") -> ConvertError:
    result = convert_python(text, target)
    assert isinstance(result, ConvertError), result
    return result


# --- Broken JSON ---------------------------------------------------------------


def test_b6_a_key_inside_an_unclosed_array_is_one_missing_bracket() -> None:
    assert _rows(_B6) == [("missing_close", 2, 10, "[", False)]


def test_b6_does_not_apply_when_a_bracket_closes_the_array_later() -> None:
    """The ``]`` is there, so the keys are missing commas, as on main."""
    assert _rows('{"a": [1, "x" : 2, "y" : 3]}') == [
        ("missing_comma", 1, 14, "", False),
        ("missing_comma", 1, 23, "", True),
    ]


def test_b6_needs_a_string_right_before_the_colon() -> None:
    """A string inside a closed inner array is not a key of the object."""
    scan = scan_json('{"a": [[1 2, "y"] : 3}')
    assert not any(slip.array_key for slip in scan.slips)


# Each "x" : 1 pair is 9 characters, so 200 000 pairs are about 1.8 MB.
_KEY_PAIRS = 200_000


@pytest.mark.perf
def test_b6_checks_each_array_once_not_each_key(perf_budget) -> None:
    """``"x" :`` pairs in one array that closes: B6 looks ahead once.

    Looking ahead from every pair read the rest of the text each time, so
    the cost grew with pairs times size (Erik, #210). The limit of 1 000
    findings still applies, so the scan reads to that limit.
    """
    text = '{"a": [' + '"x" : 1, ' * _KEY_PAIRS + "1]}"
    assert 1_700_000 < len(text) < 1_900_000
    error = _decode_error(text)

    def check() -> None:
        found = collect_problems(text, "JSON", error)
        assert found[0].code == "missing_comma"

    perf_budget.within(0.25, check, fixture="1.8 MB of key pairs in one array")


def test_b7_text_cut_off_mid_value_is_one_text_ends_problem() -> None:
    assert _rows(_B7) == [("incomplete", 5, 22, "", False)]


@pytest.mark.parametrize(
    ("text", "column"), [('{"a"', 5), ('{"a": 1, "b"', 13)], ids=["first", "later"]
)
def test_b7_text_cut_off_right_after_a_key_is_one_text_ends_problem(
    text: str, column: int
) -> None:
    """The colon and value are missing too, so ``}`` alone wouldn't fix it."""
    assert _rows(text) == [("incomplete", 1, column, "", False)]
    found = collect_problems(text, "JSON", _decode_error(text))
    assert message_for(found[0]) == _ENDS_EARLY


@pytest.mark.parametrize(
    ("text", "row"),
    [
        ('{"a": [1, 2,', ("missing_close", 1, 7, "[", False)),
        ('{"a": 1,', ("missing_close", 1, 1, "{", False)),
        ('{"a": "Ad', ("unclosed_quote", 1, 7, "", False)),
    ],
    ids=["after a comma in an array", "after a comma in an object", "in a string"],
)
def test_other_early_endings_keep_their_bracket_or_quote_row(
    text: str, row: tuple[str, int, int, str, bool]
) -> None:
    """There the named bracket or quote is the fix (Bill, #195)."""
    assert _rows(text) == [row]


def test_b9_python_values_each_get_a_row_and_none_reads_as_a_key() -> None:
    assert _rows(_B9) == [
        ("python_literal", 1, 27, "True", False),
        ("python_literal", 1, 44, "None", False),
        ("python_tuple", 1, 60, "(", False),
    ]
    assert _rows('{"done": False}') == [("python_literal", 1, 10, "False", False)]


def test_b9_a_word_that_starts_like_none_is_not_none() -> None:
    assert [row[0] for row in _rows('{"a": Nonexistent}')] == ["json_other"]


def test_b10_each_comment_gets_a_row_and_no_comma_row() -> None:
    assert _rows(_B10) == [
        ("json_comment", 2, 1, "", False),
        ("json_comment", 4, 11, "", False),
    ]


@pytest.mark.parametrize("text", ["1)", ")", '{"a": 1})', "[1])", '"x" )'])
def test_a_closer_with_no_tuple_open_is_read_past(text: str) -> None:
    """A stray ")" is junk. Reading it as a tuple's end stopped the scan."""
    assert [row[0] for row in _rows(text)] == ["json_other"]


@pytest.mark.parametrize(
    "text", ['{"a": (1, 2]}', "(1, 2]"], ids=["in an object", "alone"]
)
def test_a_tuple_closed_with_a_square_bracket_is_one_row(text: str) -> None:
    """Following the tuple row fixes both ends, so no "Missing )" row."""
    rows = _rows(text)
    assert [row[0] for row in rows] == ["python_tuple"]
    found = collect_problems(text, "JSON", _decode_error(text))
    assert (
        message_for(found[0]) == "Use [ and ] instead of ( and ) for a list on line 1."
    )


def test_a_tuple_left_open_names_its_closer() -> None:
    assert _rows("(1, 2") == [
        ("python_tuple", 1, 1, "(", False),
        ("missing_close", 1, 1, "(", False),
    ]
    found = collect_problems("(1, 2", "JSON", _decode_error("(1, 2"))
    assert message_for(found[-1]) == "Missing ) for the ( opened on line 1."


# --- Convert from Python ------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "opener"),
    [("{'name': 'Ada', 'age': 36", "{"), ("['Ada', 36", "["), ("('Ada', 36", "(")],
    ids=["curly", "square", "round"],
)
def test_e7_a_bracket_that_never_closes_names_its_opener(
    source: str, opener: str
) -> None:
    """The app words it like JSON's row; core keeps no bracket sentences."""
    error = _error(source)
    assert (error.opener, error.lineno, error.colno) == (opener, 1, 1)
    assert error.message == ""
    assert error.syntax


def test_e8_an_assignment_names_what_to_remove() -> None:
    error = _error("config = {'debug': True}")
    assert error.message == (
        'Remove "config =" on line 1. '
        "Convert from Python reads a value, not an assignment."
    )
    assert (error.lineno, error.colno) == (1, 1)
    # The text is valid Python, so the status keeps the problem count.
    assert not error.syntax


@pytest.mark.parametrize(
    ("source", "line"),
    [("import os", 1), ("x = 1; y = 2", 1), ("a.b = 1", 1), ("1\nimport os", 2)],
    ids=["import", "two statements", "attribute", "a value then a statement"],
)
def test_e8_other_valid_python_says_it_is_a_statement(source: str, line: int) -> None:
    error = _error(source)
    assert error.message == (
        f"Convert from Python reads a value, not a statement, on line {line}."
    )
    assert (error.lineno, error.colno) == (line, 1)
    assert not error.syntax


@pytest.mark.parametrize(
    ("source", "column"),
    [
        ("{'a': 1}\n{'b': 2}", 1),
        ("{'a': 1}\n{'b': 2}\n{'c': 3}", 1),
        ("[1]\n[2]  # two lists", 1),
    ],
    ids=["two", "three", "with a comment"],
)
def test_extra_values_name_the_first_extra_one(source: str, column: int) -> None:
    """One row, at the start of the second value (Bill, #195)."""
    error = _error(source)
    assert error.message == (
        "Convert from Python reads one value. Remove the extra value on line 2."
    )
    assert (error.lineno, error.colno) == (2, column)
    assert not error.syntax


@pytest.mark.parametrize(
    "source", ["# just a comment", "\n  # first\n# second\n"], ids=["one", "several"]
)
def test_comment_only_text_has_no_value_to_convert(source: str) -> None:
    error = _error(source)
    assert error.message == _ONLY_COMMENTS
    assert (error.lineno, error.colno) == (1, 1)
    # The text is valid Python, so the status keeps the problem count.
    assert not error.syntax


def test_e8_a_long_name_is_cut_like_a_long_key() -> None:
    name = "settings_" * 8
    error = _error(f"{name} = 1")
    assert error.message.startswith(f'Remove "{name[:NAME_DISPLAY_LIMIT]}… ="')


def test_e8_any_other_syntax_error_gets_the_6l_sentence() -> None:
    error = _error("{'owner': <Customer object at 0x10>}")
    assert error.message == _NOT_PYTHON
    assert (error.lineno, error.colno) == (1, 11)
    assert error.syntax


@pytest.mark.parametrize("name", ["true", "false", "null"])
def test_e9_json_names_point_to_format(name: str) -> None:
    error = _error(f'{{"name": "Ada", "active": {name}}}')
    assert error.message == "This looks like JSON, not Python. Use Format instead."
    assert (error.lineno, error.colno) == (1, 27)
    assert not error.syntax


def test_e9_the_issue_input_points_to_format() -> None:
    error = _error('{"name": "Ada", "active": true, "manager": null}')
    assert error.message == "This looks like JSON, not Python. Use Format instead."


def test_e9_other_names_keep_their_sentence() -> None:
    assert _error("{'owner': manager}").message == "Names can't be converted."


@pytest.mark.parametrize(
    "value",
    ["float('nan')", "float('inf')", "float('-inf')", "nan", "inf", "-inf"],
)
def test_e10_nan_and_infinity_reach_their_sentence_for_json(value: str) -> None:
    error = _error(f"{{'ratio': {value}}}")
    assert error.message == "NaN and Infinity can't be converted to JSON."
    assert (error.lineno, error.colno) == (1, 11)


def test_e10_yaml_keeps_nan_and_infinity() -> None:
    result = convert_python(
        "{'a': float('nan'), 'b': inf, 'c': -inf, 'd': float('-inf')}", "yaml"
    )
    assert isinstance(result, ConvertResult)
    assert result.text == "a: .nan\nb: .inf\nc: -.inf\nd: -.inf\n"


@pytest.mark.parametrize("value", ["3+4j", "4j-3", "3-4j", "-4j+3"])
def test_e11_a_sum_with_a_complex_number_names_complex_numbers(value: str) -> None:
    error = _error(f"{{'z': {value}}}")
    assert error.message == "Complex numbers can't be converted."
    assert (error.lineno, error.colno) == (1, 7)


@pytest.mark.parametrize("value", ["3+4", "9 ** 9", "2 * 3"])
def test_e11_other_arithmetic_says_to_write_the_result(value: str) -> None:
    error = _error(f"{{'z': {value}}}")
    assert error.message == "Arithmetic can't be converted. Write the result instead."


@pytest.mark.parametrize(
    "source",
    [
        "config = {'debug': True}",
        "import os",
        "{'a': <x>}",
        '{"a": true}',
        "{'a': float('nan')}",
        "{'z': 3+4j}",
        "{'a': missing}",
        "[datetime(2026, 10, 18)]",
    ],
)
def test_no_convert_error_names_a_column(source: str) -> None:
    """The place goes in the row's label, never the sentence (Bill, #195)."""
    message = _error(source).message
    assert "column" not in message
    assert message.endswith(".")


def test_a_nonfinite_float_is_never_called() -> None:
    """``float`` is matched by name and its string by spelling; nothing runs."""
    error = _error("{'a': float('1e5')}")
    assert error.message == "Function calls can't be converted."
