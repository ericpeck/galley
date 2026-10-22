"""Sort keys (#59): output keys in code-point order, input untouched."""

import json
import multiprocessing

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError

from galley.core.errors import JSONDuplicateKeyError
from galley.core.format import (
    _format_json_walk_first,
    _format_yaml_impl,
    format_document,
    format_json,
    format_yaml,
    sort_mapping_keys,
)
from galley.core.worker import FailureEvent, FormatJob, SuccessEvent, run_worker

_PAIRS = (("json", "json"), ("json", "yaml"), ("yaml", "json"), ("yaml", "yaml"))


def _run_job(job: FormatJob) -> object:
    """Run ``job`` in a spawn worker and return its last event."""
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
    process.start()
    try:
        jobs.put(job)
        event = events.get(timeout=30)
        while not isinstance(event, (SuccessEvent, FailureEvent)):
            event = events.get(timeout=30)
        jobs.put(None)
        process.join(timeout=10)
        return event
    finally:
        if process.exitcode is None:
            process.kill()
            process.join(timeout=5)
        jobs.cancel_join_thread()
        events.cancel_join_thread()


def _keys_json(text: str) -> list[str]:
    return list(json.loads(text))


def _load_yaml(text: str) -> object:
    return YAML(typ="safe").load(text)


def test_off_by_default_keeps_input_order() -> None:
    text = '{"b": 1, "a": 2}'
    assert format_json(text) == format_document(text, "json", "json")
    assert _keys_json(format_json(text)) == ["b", "a"]
    assert format_yaml("b: 1\na: 2\n") == "b: 1\na: 2\n"


def test_json_keys_sort_at_every_level() -> None:
    text = '{"b": {"z": 1, "y": {"d": 0, "c": 0}}, "a": [{"k": 1, "j": 2}]}'
    out = format_json(text, sort_keys=True)
    assert out == (
        "{\n"
        '  "a": [\n'
        "    {\n"
        '      "j": 2,\n'
        '      "k": 1\n'
        "    }\n"
        "  ],\n"
        '  "b": {\n'
        '    "y": {\n'
        '      "c": 0,\n'
        '      "d": 0\n'
        "    },\n"
        '    "z": 1\n'
        "  }\n"
        "}\n"
    )


def test_arrays_keep_their_order() -> None:
    text = '{"list": [3, 1, 2, "b", "a", {"y": 1, "x": 2}, [9, 8]]}'
    value = json.loads(format_json(text, sort_keys=True))
    assert value["list"][:5] == [3, 1, 2, "b", "a"]
    assert list(value["list"][5]) == ["x", "y"]
    assert value["list"][6] == [9, 8]
    yaml_out = format_yaml("list: [c, a, b]\n", sort_keys=True)
    assert yaml_out == "list:\n- c\n- a\n- b\n"


def test_order_is_code_point_not_case_insensitive() -> None:
    text = '{"b": 1, "B": 2, "a": 3, "A": 4, "_": 5, "10": 6, "9": 7}'
    out = format_json(text, sort_keys=True)
    assert _keys_json(out) == ["10", "9", "A", "B", "_", "a", "b"]
    assert _keys_json(out) == sorted(json.loads(text))


def test_unicode_keys_sort_by_code_point() -> None:
    text = '{"é": 1, "z": 2, "e": 3, "日本": 4, "Ω": 5, "😀": 6}'
    out = format_json(text, sort_keys=True)
    assert _keys_json(out) == ["e", "z", "é", "Ω", "日本", "😀"]
    yaml_out = format_yaml("é: 1\nz: 2\ne: 3\n", sort_keys=True)
    assert yaml_out == "e: 3\nz: 2\né: 1\n"


def test_escaped_keys_sort_by_their_decoded_text() -> None:
    # "\u0062" is "b", so it sorts between "a" and "c".
    text = '{"c": 1, "\\u0062": 2, "a": 3}'
    assert _keys_json(format_json(text, sort_keys=True)) == ["a", "b", "c"]


def test_yaml_keys_sort_at_every_level() -> None:
    text = "b:\n  z: 1\n  y:\n    d: 0\n    c: 0\na:\n- k: 1\n  j: 2\n"
    assert format_yaml(text, sort_keys=True) == (
        "a:\n- j: 2\n  k: 1\nb:\n  y:\n    c: 0\n    d: 0\n  z: 1\n"
    )


def test_yaml_number_keys_sort_as_the_text_written() -> None:
    """``10`` sorts before ``9``, the same order JSON output gives "10" and "9"."""
    text = "9: nine\n10: ten\nb: bee\n1.5: half\nnull: none\nfalse: no\n"
    out = format_yaml(text, sort_keys=True)
    assert out == ("1.5: half\n10: ten\n9: nine\nb: bee\nfalse: no\nnull: none\n")
    as_json = format_document(text, "yaml", "json", sort_keys=True)
    assert _keys_json(as_json) == ["1.5", "10", "9", "b", "false", "null"]


def test_yaml_number_and_string_with_the_same_text_keep_input_order() -> None:
    out = format_yaml("'1': string\n1: number\n0: zero\n", sort_keys=True)
    assert out == "0: zero\n'1': string\n1: number\n"


def test_yaml_date_keys_sort_as_iso_text() -> None:
    out = format_yaml("2024-02-01: b\n2023-12-31: a\nz: c\n", sort_keys=True)
    assert out == "2023-12-31: a\n2024-02-01: b\nz: c\n"


def test_yaml_merge_keys_sort_with_the_mapping_s_own_keys() -> None:
    text = "base: &b\n  x: 1\n  a: 2\nd:\n  <<: *b\n  c: 3\n"
    out = format_yaml(text, sort_keys=True)
    value = _load_yaml(out)
    assert list(value) == ["base", "d"]
    assert list(value["d"]) == ["a", "c", "x"]
    assert list(value["base"]) == ["a", "x"]


def test_yaml_anchor_and_alias_stay_shared_after_sorting() -> None:
    text = "z: &x\n  k: 1\n  j: 2\na: *x\n"
    out = format_yaml(text, sort_keys=True)
    assert out == "a: &id001\n  j: 2\n  k: 1\nz: *id001\n"


def test_yaml_recursive_alias_sorts_once() -> None:
    value = _load_yaml("b: &a\n  y: *a\n  x: 1\n")
    sorted_value = sort_mapping_keys(value)
    assert sorted_value is value
    assert list(value["b"]) == ["x", "y"]
    assert value["b"]["y"] is value["b"]


def test_yaml_list_key_sorts_as_its_text() -> None:
    out = format_yaml("z: 1\n? [a, b]\n: 2\n", sort_keys=True)
    assert list(_load_yaml(out)) == [("a", "b"), "z"]


@pytest.mark.parametrize(("source", "target"), _PAIRS)
def test_sorting_is_idempotent(source: str, target: str) -> None:
    text = (
        '{"b": {"d": [1, {"f": 1, "e": 2}], "c": null}, "a": "x", "A": true}'
        if source == "json"
        else "b:\n  d:\n  - 1\n  - f: 1\n    e: 2\n  c: null\na: x\nA: true\n"
    )
    once = format_document(text, source, target, sort_keys=True)
    twice = format_document(once, target, target, sort_keys=True)
    assert once == twice


@pytest.mark.parametrize(("source", "target"), _PAIRS)
def test_sorting_keeps_the_values(source: str, target: str) -> None:
    text = (
        '{"b": {"d": [3, 1], "c": "é"}, "a": 1.5}'
        if source == "json"
        else "b:\n  d: [3, 1]\n  c: é\na: 1.5\n"
    )
    plain = format_document(text, source, target)
    sorted_out = format_document(text, source, target, sort_keys=True)
    load = json.loads if target == "json" else _load_yaml
    assert load(plain) == load(sorted_out)
    assert list(load(sorted_out)) == ["a", "b"]
    assert list(load(sorted_out)["b"]) == ["c", "d"]


@pytest.mark.parametrize(("source", "target"), _PAIRS)
def test_input_text_is_not_changed(source: str, target: str) -> None:
    text = '{"b": 1, "a": 2}' if source == "json" else "b: 1\na: 2\n"
    before = str(text)
    format_document(text, source, target, sort_keys=True)
    assert text == before
    data = text.encode()
    format_document(data, source, target, sort_keys=True)
    assert data == before.encode()


def test_duplicate_json_keys_still_fail_when_sorting() -> None:
    with pytest.raises(JSONDuplicateKeyError) as caught:
        format_json('{"b": 1, "a": 2, "b": 3}', sort_keys=True)
    assert [d.key for d in caught.value.duplicates] == ["b"]
    with pytest.raises(JSONDuplicateKeyError):
        format_document('{"a": 1, "a": 2}', "json", "yaml", sort_keys=True)


def test_duplicate_yaml_keys_still_fail_when_sorting() -> None:
    with pytest.raises(DuplicateKeyError):
        format_yaml("b: 1\na: 2\nb: 3\n", sort_keys=True)
    with pytest.raises(DuplicateKeyError):
        format_document("b: 1\nb: 2\n", "yaml", "json", sort_keys=True)


def test_walk_first_json_path_sorts_too() -> None:
    text = '{"b": 1, "a": {"d": 1, "c": 2}}'
    assert _format_json_walk_first(text, sort_keys=True) == format_json(
        text, sort_keys=True
    )


def test_pure_python_yaml_path_sorts_too() -> None:
    text = "b: 1\na:\n  d: 1\n  c: 2\n"
    expected = "a:\n  c: 2\n  d: 1\nb: 1\n"
    assert _format_yaml_impl(text, pure=True, sort_keys=True) == expected
    assert format_yaml(text, sort_keys=True) == expected


def test_deep_yaml_takes_the_explicit_stack_and_sorts() -> None:
    depth = 300
    text = "".join(f"{'  ' * i}b{i}: 0\n{'  ' * i}a{i}:\n" for i in range(depth))
    text += "  " * depth + "leaf: 1\n"
    out = format_yaml(text, sort_keys=True)
    lines = out.splitlines()
    assert lines[0] == "a0:"
    assert lines[1] == "  a1:"
    assert lines[-1].strip() == "b0: 0"


def test_deep_json_sorts_without_recursion() -> None:
    value: dict[str, object] = {"leaf": 1}
    for _ in range(2000):
        value = {"z": 0, "a": value}
    assert sort_mapping_keys(value) is value
    node: object = value
    while isinstance(node, dict) and "a" in node:
        assert list(node) == ["a", "z"]
        node = node["a"]


def test_worker_sorts_when_the_job_asks() -> None:
    text = '{"b": 1, "a": {"d": 1, "c": 2}}'
    success = _run_job(
        FormatJob(job_id=1, text=text, source="json", target="json", sort_keys=True)
    )
    assert isinstance(success, SuccessEvent)
    assert success.text == format_json(text, sort_keys=True)
    success = _run_job(FormatJob(job_id=2, text=text, source="json", target="json"))
    assert isinstance(success, SuccessEvent)
    assert success.text == format_json(text)


def test_worker_reports_duplicates_when_sorting() -> None:
    failure = _run_job(
        FormatJob(
            job_id=1,
            text='{"a": 1, "a": 2}',
            source="json",
            target="json",
            sort_keys=True,
        )
    )
    assert isinstance(failure, FailureEvent)
    assert failure.problems or failure.error is not None
