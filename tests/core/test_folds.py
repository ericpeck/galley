"""Fold ranges for formatted Output (#64). No Qt."""

import json
import random
import time

import pytest

from galley.core import folds
from galley.core.errors import MAX_DEPTH
from galley.core.folds import (
    FoldKind,
    FoldRange,
    fold_ranges,
    line_count,
    pack_ranges,
    unpack_ranges,
)
from galley.core.format import format_document
from galley.core.worker import FormatJob, SuccessEvent, _execute

OBJECT = FoldKind.OBJECT
ARRAY = FoldKind.ARRAY
# Erik's #64 budget for the scan of a 2 MB file.
SCAN_BUDGET_S = 0.050
TWO_MB = 2_000_000


def _json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _spans(text: str, fmt: str = "JSON") -> list[tuple[int, int, FoldKind, int, int]]:
    return [tuple(item) for item in fold_ranges(text, fmt)]


def test_objects_and_arrays_fold_from_their_first_line_to_their_last() -> None:
    text = _json({"a": {"b": 1, "c": [1, 2]}, "d": 2})
    # 0 {   1 "a": {   2 "b": 1,   3 "c": [   4 1,   5 2   6 ]   7 },
    # 8 "d": 2   9 }
    assert _spans(text) == [
        (0, 9, OBJECT, 2, 0),
        (1, 7, OBJECT, 2, 1),
        (3, 6, ARRAY, 2, 2),
    ]


def test_a_root_array_counts_items() -> None:
    text = _json([{"a": 1}, [1, 2, 3], "x"])
    ranges = fold_ranges(text, "JSON")
    assert ranges[0] == FoldRange(0, 10, ARRAY, 3, 0)
    assert ranges[1] == FoldRange(1, 3, OBJECT, 1, 1)
    assert ranges[2] == FoldRange(4, 8, ARRAY, 3, 1)


def test_empty_and_one_line_containers_do_not_fold() -> None:
    assert _spans(_json({"a": {}, "b": []})) == [(0, 3, OBJECT, 2, 0)]
    assert fold_ranges('{"a": [1, 2], "b": {"c": 3}}', "JSON") == []
    assert fold_ranges("[]", "JSON") == []
    assert fold_ranges("42", "JSON") == []
    assert fold_ranges("", "JSON") == []


def test_empty_containers_over_lines_do_not_fold() -> None:
    assert _spans('{\n  "a": [\n  ],\n  "b": {\n}\n}\n') == [(0, 5, OBJECT, 2, 0)]


def test_brackets_and_commas_inside_strings_are_text() -> None:
    value = {
        'q"{': "[},",
        "b": ["x\\", "]", '\\"[', "{"],
        "c": "a, b, c",
    }
    text = _json(value)
    assert _spans(text) == [
        (0, 9, OBJECT, 3, 0),
        (2, 7, ARRAY, 4, 1),
    ]


def test_unicode_escapes_and_non_ascii_keep_their_lines() -> None:
    text = _json({"ключ": ["\u00e9", "\\u0041"], "🙂": {"x": "\u2028"}})
    assert _spans(text) == [
        (0, 8, OBJECT, 2, 0),
        (1, 4, ARRAY, 2, 1),
        (5, 7, OBJECT, 1, 1),
    ]


def test_ranges_match_the_tree_dumped_from_random_values() -> None:
    generator = random.Random(64)
    for _ in range(200):
        value = _random_value(generator, depth=0)
        text = _json(value)
        assert fold_ranges(text, "JSON") == _expected_ranges(value)


def test_two_containers_on_one_line_keep_the_outer_one() -> None:
    text = '[{\n  "a": 1\n}, {\n  "b": 2\n}]\n'
    assert _spans(text) == [(0, 4, ARRAY, 2, 0), (2, 4, OBJECT, 1, 1)]


def test_unbalanced_text_does_not_raise() -> None:
    assert fold_ranges('{\n  "a": [\n    1\n', "JSON") == []
    assert _spans("]\n}\n{\n  1\n}\n") == [(2, 4, OBJECT, 1, 0)]
    assert _spans('{\n  "a": "unterminated\n}\n') == [(0, 2, OBJECT, 1, 0)]


def test_nesting_to_max_depth_folds_and_past_it_does_not() -> None:
    deepest = _nested(MAX_DEPTH)
    ranges = fold_ranges(deepest, "JSON")
    assert len(ranges) == MAX_DEPTH
    assert ranges[-1].depth == MAX_DEPTH - 1
    assert fold_ranges(_nested(MAX_DEPTH + 1), "JSON") == []


def test_a_failed_scan_logs_and_returns_nothing(monkeypatch, caplog) -> None:
    def broken(_text: str) -> list[FoldRange]:
        raise RuntimeError("boom")

    monkeypatch.setattr(folds, "_json_ranges", broken)
    assert fold_ranges("{\n}\n", "JSON") == []
    assert "Fold scan failed" in caplog.text


def test_line_count_matches_the_lines_ranges_use() -> None:
    assert line_count("") == 1
    assert line_count("{\n}\n") == 3
    text = _json({"a": [1, 2]})
    assert line_count(text) == len(text.split("\n"))


def test_packed_ranges_round_trip() -> None:
    text = _json({"a": {"b": [1, 2]}, "c": [{"d": 1}]})
    ranges = fold_ranges(text, "JSON")
    data = pack_ranges(ranges, line_count(text))
    assert isinstance(data, bytes)
    assert unpack_ranges(data) == (ranges, line_count(text))
    assert all(isinstance(item.kind, FoldKind) for item in unpack_ranges(data)[0])
    assert unpack_ranges(b"") == ([], 0)


def test_the_format_job_returns_the_output_fold_ranges() -> None:
    events: list[object] = []

    class Queue:
        def put(self, event: object) -> None:
            events.append(event)

    _execute(FormatJob(1, '{"a": [1, 2], "b": {"c": 3}}', "json", "json"), Queue())
    success = events[-1]
    assert isinstance(success, SuccessEvent)
    ranges, lines = unpack_ranges(success.folds)
    assert lines == line_count(success.text)
    assert ranges == fold_ranges(success.text, "JSON")
    assert [item.start_line for item in ranges] == [0, 1, 5]

    events.clear()
    _execute(FormatJob(2, '{"a": [1, 2]}', "json", "yaml"), Queue())
    success = events[-1]
    assert isinstance(success, SuccessEvent)
    assert success.text == "a:\n- 1\n- 2\n"
    assert unpack_ranges(success.folds) == ([FoldRange(0, 2, ARRAY, 2, 1)], 4)


# YAML folds by indentation (Erik's plan, ui-spec "folding by indentation").


def test_yaml_block_mappings_and_sequences() -> None:
    text = "a:\n  b: 1\n  c:\n  - 1\n  - 2\nd: 2\n"
    assert _spans(text, "YAML") == [
        (0, 4, OBJECT, 2, 1),
        (2, 4, ARRAY, 2, 2),
    ]


def test_yaml_items_that_are_mappings_fold_from_their_dash() -> None:
    text = "items:\n- id: 1\n  tags:\n  - a\n  - b\n- id: 2\n  name: x\n"
    assert _spans(text, "YAML") == [
        (0, 6, ARRAY, 2, 1),
        (1, 4, OBJECT, 2, 2),
        (2, 4, ARRAY, 2, 3),
        (5, 6, OBJECT, 2, 2),
    ]


def test_yaml_nested_sequences_and_bare_dashes() -> None:
    text = "- - 1\n  - 2\n-\n  a: 1\n  b: 2\n- x\n"
    assert _spans(text, "YAML") == [(0, 1, ARRAY, 2, 1), (2, 4, OBJECT, 2, 1)]


def test_yaml_scalars_that_span_lines_do_not_fold() -> None:
    text = (
        "a: |\n  line one: x\n  - line two\n"
        'b: "long text: that\n  wraps: here"\n'
        "c:\n- plain words\n  that wrap\n- 2\n"
    )
    assert _spans(text, "YAML") == [(5, 8, ARRAY, 2, 1)]


def test_yaml_empty_collections_anchors_and_documents() -> None:
    text = "a: {}\nb: []\nc: &id001\n  x: 1\nd: *id001\n---\n- 1\n"
    assert _spans(text, "YAML") == [(2, 3, OBJECT, 1, 1)]
    assert fold_ranges("just a scalar\n", "YAML") == []
    assert fold_ranges("", "YAML") == []


def test_yaml_quoted_keys_with_colons() -> None:
    text = "\"a: b\":\n  'c'': d': 1\n  e: 2\n"
    assert _spans(text, "YAML") == [(0, 2, OBJECT, 2, 1)]


def test_yaml_ranges_match_json_ranges_for_the_same_values() -> None:
    """YAML has the JSON ranges below the root, in order, less some.

    A block that fits on one YAML line (``- - {}``) does not fold, and an
    item's first key can open a block on the item's own ``-`` line, where
    YAML keeps only the outer one.
    """
    generator = random.Random(165)
    for _ in range(100):
        value = _random_value(generator, depth=0)
        if not isinstance(value, dict | list):
            continue
        yaml_text = format_document(_json(value), "json", "yaml")
        expected = [
            (item.kind, item.count) for item in _expected_ranges(value) if item.depth
        ]
        found = [(item.kind, item.count) for item in fold_ranges(yaml_text, "YAML")]
        remaining = iter(expected)
        assert all(item in remaining for item in found), yaml_text
        # Every block under a ``key:`` line keeps its own line.
        assert len(found) >= _keyed_blocks(value), yaml_text


def test_yaml_nesting_past_max_depth_has_no_ranges() -> None:
    lines = [f"{'  ' * level}k:" for level in range(MAX_DEPTH + 1)]
    text = "\n".join(lines) + " 1\n"
    assert fold_ranges(text, "YAML") == []


def test_the_scan_of_2_mb_of_json_is_within_budget() -> None:
    text = _two_mb_json()
    assert len(text.encode("utf-8")) >= TWO_MB
    best = _best_of(3, lambda: fold_ranges(text, "JSON"))
    assert len(fold_ranges(text, "JSON")) > 20_000
    assert best <= SCAN_BUDGET_S, f"2 MB scan took {best * 1000:.1f} ms"


@pytest.mark.parametrize("fmt", ["JSON", "YAML"])
def test_scanning_never_raises_on_odd_text(fmt: str) -> None:
    generator = random.Random(7)
    alphabet = "{}[]\",:\\- \n#&!|>'?abc"
    for _ in range(300):
        text = "".join(generator.choice(alphabet) for _ in range(80))
        for item in fold_ranges(text, fmt):
            assert item.end_line > item.start_line


def _best_of(runs: int, operation) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        operation()
        best = min(best, time.perf_counter() - started)
    return best


def _two_mb_json() -> str:
    """Just over 2 MB of records shaped like an API response.

    About 90 bytes a container, with brackets, commas and escaped quotes in
    the strings.
    """

    def record(index: int) -> dict[str, object]:
        return {
            "id": index,
            "name": f"item {index}",
            "tags": ["a", "b{", "c]"],
            "active": index % 2 == 0,
            "owner": {"login": f"user{index}", "site_admin": False},
            "metrics": {"views": index * 3, "scores": [1.5, 2.25, index]},
            "notes": 'quote " and [brackets] {x}',
        }

    count = 1000
    while True:
        text = _json({"items": [record(index) for index in range(count)]})
        size = len(text.encode("utf-8"))
        if size >= TWO_MB:
            return text
        count += max(1, (TWO_MB - size) * count // size // 2 + 1)


def _nested(depth: int) -> str:
    """``depth`` arrays, one inside the next, the innermost holding ``1``."""
    opening = "".join(f"{'  ' * level}[\n" for level in range(depth))
    closing = "".join(f"{'  ' * level}]\n" for level in reversed(range(depth)))
    return opening + f"{'  ' * depth}1\n" + closing


def _random_value(generator: random.Random, depth: int) -> object:
    roll = generator.random()
    if depth < 4 and roll < 0.35:
        return {
            generator.choice(["k", "a{", 'b"]', "c,d"]) + str(index): _random_value(
                generator, depth + 1
            )
            for index in range(generator.randint(0, 4))
        }
    if depth < 4 and roll < 0.6:
        return [
            _random_value(generator, depth + 1) for _ in range(generator.randint(0, 4))
        ]
    return generator.choice([1, 2.5, None, True, "x", "[", "}", 'a "b"', ""])


def _keyed_blocks(value: object, in_item: bool = False) -> int:
    """Non-empty blocks that are a mapping value, not an item's first one."""
    count = 0
    if isinstance(value, dict):
        for index, child in enumerate(value.values()):
            shares_dash = in_item and index == 0
            if isinstance(child, dict | list) and child and not shares_dash:
                count += 1
            count += _keyed_blocks(child)
    elif isinstance(value, list):
        for child in value:
            count += _keyed_blocks(child, in_item=True)
    return count


def _expected_ranges(value: object) -> list[FoldRange]:
    """Ranges of ``json.dumps(value, indent=2)``, found from the value itself."""
    ranges: list[FoldRange] = []
    line = 0

    def walk(node: object, depth: int) -> None:
        nonlocal line
        if not isinstance(node, dict | list) or not node:
            line += 1
            return
        start = line
        line += 1
        children = node.values() if isinstance(node, dict) else node
        index = len(ranges)
        ranges.append(FoldRange(0, 0, OBJECT, 0, 0))
        for child in children:
            walk(child, depth + 1)
        kind = OBJECT if isinstance(node, dict) else ARRAY
        ranges[index] = FoldRange(start, line, kind, len(node), depth)
        line += 1

    walk(value, 0)
    return ranges
