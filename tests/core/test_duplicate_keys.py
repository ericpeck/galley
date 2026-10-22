"""A JSON object that repeats a key is an error, not a silent last value."""

import json
import pickle
import random
import statistics
import time

import pytest
from ruamel.yaml.constructor import DuplicateKeyError

import galley.core.format as format_module
from galley.core.diff import load_document as diff_load_document
from galley.core.errors import DuplicateKey, InputError, JSONDuplicateKeyError
from galley.core.format import format_document, format_json, load_document
from galley.core.problems import Problem, classify_failure, collect_problems


def _duplicates(text: str) -> tuple[DuplicateKey, ...]:
    with pytest.raises(JSONDuplicateKeyError) as caught:
        format_json(text)
    return caught.value.duplicates


def _problems(text: str) -> tuple[Problem, ...]:
    with pytest.raises(JSONDuplicateKeyError) as caught:
        format_json(text)
    return collect_problems(text, "JSON", caught.value)


def test_top_level_duplicate_points_at_the_second_occurrence() -> None:
    text = '{\n  "name": "a",\n  "age": 1,\n  "name": "b"\n}\n'
    assert _duplicates(text) == (DuplicateKey("name", 4, 3),)
    assert _problems(text) == (Problem("json_duplicate", 4, "name"),)


def test_duplicate_in_an_object_inside_an_array() -> None:
    text = '[\n  {"id": 1},\n  {\n    "id": 2,\n    "id": 3\n  }\n]\n'
    assert _problems(text) == (Problem("json_duplicate", 5, "id"),)


def test_duplicate_in_an_object_inside_an_object() -> None:
    text = '{\n  "outer": {\n    "x": 1,\n    "x": 2\n  },\n  "x": 3\n}\n'
    assert _problems(text) == (Problem("json_duplicate", 4, "x"),)


def test_same_key_in_sibling_objects_is_not_a_duplicate() -> None:
    text = '[{"a": 1}, {"a": 2}, {"a": {"a": 3}}]'
    assert json.loads(format_json(text)) == [{"a": 1}, {"a": 2}, {"a": {"a": 3}}]


def test_every_duplicate_is_reported_in_text_order() -> None:
    text = (
        "{\n"
        '  "a": 1,\n'
        '  "b": [{"c": 1, "c": 2}],\n'
        '  "a": 2,\n'
        '  "d": {"e": 1,\n'
        '        "e": 2},\n'
        '  "a": 3\n'
        "}\n"
    )
    assert _problems(text) == (
        Problem("json_duplicate", 3, "c"),
        Problem("json_duplicate", 4, "a"),
        Problem("json_duplicate", 6, "e"),
        Problem("json_duplicate", 7, "a"),
    )


def test_repeats_on_one_line_are_one_problem() -> None:
    text = '{"a": 1, "a": 2, "a": 3, "b": 1, "b": 2}'
    assert [item.colno for item in _duplicates(text)] == [10, 18, 34]
    assert _problems(text) == (
        Problem("json_duplicate", 1, "a"),
        Problem("json_duplicate", 1, "b"),
    )


def test_duplicate_with_a_different_value_type() -> None:
    text = '{\n  "v": 1,\n  "v": {"nested": [true, null]}\n}\n'
    assert _problems(text) == (Problem("json_duplicate", 3, "v"),)


def test_keys_written_inside_a_string_do_not_count() -> None:
    text = '{"note": "\\"a\\": 1, \\"a\\": 2", "a": "{\\"a\\": 1}"}'
    assert json.loads(format_json(text)) == {
        "note": '"a": 1, "a": 2',
        "a": '{"a": 1}',
    }


def test_keys_that_differ_only_by_case_are_different() -> None:
    assert json.loads(format_json('{"Name": 1, "name": 2, "NAME": 3}')) == {
        "Name": 1,
        "name": 2,
        "NAME": 3,
    }


def test_escaped_spellings_of_one_key_are_a_duplicate() -> None:
    text = '{"a": 1,\n "\\u0061": 2}'
    assert _problems(text) == (Problem("json_duplicate", 2, "a"),)
    pair = '{"\\ud83d\\ude00": 1, "\U0001f600": 2}'
    assert _problems(pair) == (Problem("json_duplicate", 1, "\U0001f600"),)


def test_empty_and_quoted_keys_are_located() -> None:
    assert _problems('{"": 1, "": 2}') == (Problem("json_duplicate", 1, ""),)
    text = '{"say \\"hi\\"": 1,\n"say \\"hi\\"": 2}'
    assert _problems(text) == (Problem("json_duplicate", 2, 'say "hi"'),)


def test_crlf_line_endings_count_lines() -> None:
    text = '{\r\n  "a": 1,\r\n  "a": 2\r\n}\r\n'
    assert _duplicates(text) == (DuplicateKey("a", 3, 3),)


def test_bytes_with_a_bom_are_located() -> None:
    data = b'\xef\xbb\xbf{\n  "a": 1,\n  "a": 2\n}\n'
    with pytest.raises(JSONDuplicateKeyError) as caught:
        format_json(data)
    assert caught.value.duplicates == (DuplicateKey("a", 3, 3),)


def test_a_syntax_error_wins_over_an_earlier_duplicate() -> None:
    with pytest.raises(json.JSONDecodeError):
        format_json('{"a": 1, "a": 2}\n[1,]')
    with pytest.raises(json.JSONDecodeError):
        format_json('{"a": 1, "a": 2,')


def test_located_input_errors_win_over_a_duplicate() -> None:
    deep = '{"a": 1, "a": 2, "b": ' + "[" * 513 + "]" * 513 + "}"
    with pytest.raises(InputError) as caught:
        format_json(deep)
    assert caught.value.kind == "too_deep"
    surrogate = '{"a": 1, "a": "\\ud800"}'
    with pytest.raises(InputError) as caught:
        format_json(surrogate)
    assert caught.value.kind == "bad_escape"


def test_error_round_trips_through_pickle() -> None:
    error = JSONDuplicateKeyError((DuplicateKey("a", 2, 3), DuplicateKey("b", 4, 1)))
    restored = pickle.loads(pickle.dumps(error))
    assert isinstance(restored, JSONDuplicateKeyError)
    assert restored.duplicates == error.duplicates
    assert (restored.lineno, restored.colno) == (2, 3)
    assert str(restored) == "duplicate_key"


def test_error_needs_a_duplicate() -> None:
    with pytest.raises(ValueError):
        JSONDuplicateKeyError(())


def test_classify_failure_names_the_first_duplicate() -> None:
    text = '{"a": 1,\n"a": 2,\n"b": 1,\n"b": 2}'
    with pytest.raises(JSONDuplicateKeyError) as caught:
        format_json(text)
    assert classify_failure(caught.value, text) == Problem("json_duplicate", 2, "a")


@pytest.mark.parametrize("target", ["json", "yaml"])
def test_format_document_rejects_duplicates_for_each_target(target: str) -> None:
    with pytest.raises(JSONDuplicateKeyError):
        format_document('{"a": 1, "a": 2}', "json", target)


def test_both_load_documents_raise_the_same_error() -> None:
    text = '{\n  "a": 1,\n  "b": {"c": 1, "c": 2},\n  "a": 3\n}\n'
    with pytest.raises(JSONDuplicateKeyError) as from_format:
        load_document(text, "json")
    with pytest.raises(JSONDuplicateKeyError) as from_diff:
        diff_load_document(text, "json")
    assert from_format.value.duplicates == from_diff.value.duplicates
    assert from_diff.value.duplicates == (
        DuplicateKey("c", 3, 17),
        DuplicateKey("a", 4, 3),
    )
    assert collect_problems(text, "json", from_diff.value) == (
        Problem("json_duplicate", 3, "c"),
        Problem("json_duplicate", 4, "a"),
    )


def test_diff_load_document_still_raises_syntax_first() -> None:
    with pytest.raises(json.JSONDecodeError):
        diff_load_document('{"a": 1, "a": 2}}', "json")


def test_yaml_duplicate_names_the_repeated_key() -> None:
    text = "name: a\nage: 1\nname: b\n"
    with pytest.raises(DuplicateKeyError) as caught:
        format_module.format_yaml(text)
    assert collect_problems(text, "YAML", caught.value) == (
        Problem("yaml_duplicate", 3, "name"),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a:\n  x: 1\n  x: 2\n", Problem("yaml_duplicate", 3, "x")),
        (
            '"say \\"hi\\"": 1\n"say \\"hi\\"": 2\n',
            Problem("yaml_duplicate", 2, 'say "hi"'),
        ),
        ("{a: 1, a: 2}\n", Problem("yaml_duplicate", 1, "a")),
        ("1: a\n1: b\n", Problem("yaml_duplicate", 2, "1")),
        ("? [1]\n: a\n? [1]\n: b\n", Problem("yaml_duplicate", 3, "")),
    ],
)
def test_yaml_duplicates_keep_the_first_repeat_only(
    text: str, expected: Problem
) -> None:
    """The safe loader stops at the first repeat, so YAML reports one."""
    with pytest.raises(DuplicateKeyError) as caught:
        diff_load_document(text, "yaml")
    assert collect_problems(text, "YAML", caught.value) == (expected,)


def test_valid_documents_never_run_the_located_scan(monkeypatch) -> None:
    def fail(source: str) -> JSONDuplicateKeyError:
        raise AssertionError("the located scan ran on a valid document")

    monkeypatch.setattr(format_module, "_duplicate_key_error", fail)
    text = json.dumps(_large_value(2_000), indent=2, ensure_ascii=False)
    assert format_json(text) == text + "\n"
    diff_load_document(text, "json")


def test_duplicate_check_keeps_large_valid_documents_cheap(monkeypatch) -> None:
    """Time ``format_json`` with and without the duplicate check on ~2 MB.

    The printed numbers go in the PR. The bound is loose on purpose: the
    check may cost at most half again the format time, which only a per-key
    scan on the valid path would break. Runs alternate so both share noise.
    """
    text = json.dumps(_large_value(7_500), indent=2, ensure_ascii=False)
    assert 1_800_000 < len(text.encode()) < 2_400_000
    hooked = format_module._json_loads

    def without_check() -> str:
        monkeypatch.setattr(format_module, "_json_loads", json.loads)
        try:
            return format_json(text)
        finally:
            monkeypatch.setattr(format_module, "_json_loads", hooked)

    runs = (
        ("json.loads", lambda: json.loads(text)),
        ("hooked loads", lambda: hooked(text)),
        ("format_json without check", without_check),
        ("format_json", lambda: format_json(text)),
    )
    samples: dict[str, list[float]] = {name: [] for name, _run in runs}
    for _ in range(7):
        for name, run in runs:
            started = time.perf_counter()
            run()
            samples[name].append(time.perf_counter() - started)
    medians = {name: statistics.median(values) for name, values in samples.items()}
    size = len(text.encode()) / 1_000_000
    print(
        f"duplicate-key check on {size:.1f} MB (median ms): "
        + ", ".join(f"{name} {value * 1000:.1f}" for name, value in medians.items()),
        flush=True,
    )
    assert medians["format_json"] < medians["format_json without check"] * 1.5


def _large_value(count: int) -> dict[str, object]:
    rng = random.Random(58)
    words = ("alpha", "beta", "gamma", "delta", "café", "日本語")
    return {
        "items": [
            {
                "id": index,
                "name": " ".join(rng.choice(words) for _ in range(6)),
                "score": rng.random(),
                "tags": [rng.choice(words) for _ in range(3)],
                "child": {"ok": rng.random() < 0.5, "note": None, "n": index * 3},
            }
            for index in range(count)
        ],
    }
