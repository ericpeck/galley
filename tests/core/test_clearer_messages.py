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
from galley.core.problems import collect_problems

_B6 = '{\n"items": [1, 2, 3,\n"done": true\n}'
_B7 = '{\n"order": 7,\n"lines": [\n{"sku": "A-1", "qty": 2},\n{"sku": "B-7", "qty":'
_B9 = '{"name": "Ada", "active": True, "manager": None, "scores": (1, 2, 3)}'
_B10 = '{\n// the user\'s name\n"name": "Ada Example",\n"age": 36 /* years */\n}'
_NOT_PYTHON = "This isn't valid Python, so it can't be converted."


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


def test_b7_text_cut_off_mid_value_is_one_text_ends_problem() -> None:
    assert _rows(_B7) == [("incomplete", 5, 22, "", False)]


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
    assert error.syntax


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
