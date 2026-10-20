"""Plain-language problem sentences. No column and no character offset."""

import json

import pytest

from galley.app.problem_text import accessible_text, message_for, problem_count_text
from galley.core.format import format_json, format_yaml
from galley.core.problems import Problem, collect_problems


def _json(text: str) -> Problem:
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json(text)
    found = collect_problems(text, "JSON", caught.value)
    assert len(found) == 1
    return found[0]


def test_messages_name_the_line_and_drop_column_and_char() -> None:
    samples = {
        '{\n  "price": 4.5\n  "qty": 3\n}\n': "Missing comma at the end of line 2.",
        '{"a": "hi}': "Unclosed quote on line 1.",
        '{"a": 1,}': "Trailing comma at the end of line 1.",
        '{"a": 1}}': "Unexpected } on line 1.",
        "[1]]": "Unexpected ] on line 1.",
        "{name: 1}": "Put double quotes around the key on line 1.",
        "{'a': 1}": "Use double quotes instead of single quotes on line 1.",
        '{\n  "a": [1, 2\n}\n': "Missing ] for the [ opened on line 2.",
        '[\n  {"a": 1\n]\n': "Missing } for the { opened on line 2.",
        '{"a": 1': "Missing } for the { opened on line 1.",
        "": "The text ends on line 1 before the JSON document finishes.",
        "abc": "Couldn't read this JSON on line 1.",
    }
    for text, expected in samples.items():
        message = message_for(_json(text))
        assert message == expected
        lowered = message.casefold()
        assert "column" not in lowered
        assert "(char" not in lowered


def test_yaml_messages_match_the_plain_style() -> None:
    tabs = collect_problems("a:\n\tb: 1\n\tc: 2\n", "YAML")
    assert [message_for(problem) for problem in tabs] == [
        "Line 2 starts with a tab. YAML needs spaces for indenting.",
        "Line 3 starts with a tab. YAML needs spaces for indenting.",
    ]
    assert accessible_text(tabs[0]) == (
        "Line 2: Starts with a tab. YAML needs spaces for indenting."
    )
    duplicate = _yaml("a: 1\na: 2\n")
    assert message_for(duplicate) == "A key is repeated on line 2."
    ended = _yaml('a: "hi\n')
    assert message_for(ended) == (
        "The text ends on line 1 before the YAML document finishes."
    )
    colon = _yaml("a: 1\nb\n")
    assert message_for(colon) == "Missing colon after the key on line 2."
    other = _yaml("a: [1, 2\n")
    assert message_for(other) == "Couldn't read this YAML on line 1."
    for problem in (duplicate, ended, colon, other, *tabs):
        lowered = message_for(problem).casefold()
        assert "column" not in lowered
        assert "(char" not in lowered


def test_raw_yaml_tab_error_is_not_the_sentence() -> None:
    text = "a:\n\tb: 1\n"
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    raw = str(caught.value).casefold()
    assert "column" in raw or "cannot start any token" in raw
    message = message_for(collect_problems(text, "YAML")[0])
    assert message == "Line 2 starts with a tab. YAML needs spaces for indenting."
    assert message not in str(caught.value)


def test_problem_count_uses_the_singular_for_one() -> None:
    assert problem_count_text(1) == "1 problem"
    assert problem_count_text(2) == "2 problems"


def _yaml(text: str) -> Problem:
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    found = collect_problems(text, "YAML", caught.value)
    assert len(found) == 1
    return found[0]
