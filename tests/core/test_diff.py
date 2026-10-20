"""Structural diff of two JSON or YAML documents (#65 PR 1).

Objects match by key and ignore key order. Arrays match by index. Scalars
compare by type and value. A unique identical subtree under a different
parent is Moved. Changes come in left-document order with spans into
each side's text.
"""

import json
import os
import pickle
import subprocess
import sys
import time
from io import StringIO

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.constructor import ConstructorError, DuplicateKeyError

from galley.core.diff import (
    SUMMARY_LIMIT,
    Change,
    Doc,
    Span,
    Summary,
    _yaml_document,
    diff,
    load_document,
)
from galley.core.errors import MAX_DEPTH, InputError
from galley.core.format import _clib_available
from galley.core.problems import classify_failure


def _json(value: object) -> Doc:
    return load_document(json.dumps(value, indent=2), "json")


def _changes(left: object, right: object) -> list[tuple[str, str]]:
    return [
        (change.kind, change.path_text)
        for change in diff(_json(left), _json(right)).changes
    ]


def _only(left: Doc, right: Doc) -> Change:
    changes = diff(left, right).changes
    assert len(changes) == 1, changes
    return changes[0]


# --- Kinds --------------------------------------------------------------


def test_identical_documents_have_no_changes() -> None:
    value = {"a": [1, {"b": None}], "c": "x"}
    assert diff(_json(value), _json(value)).changes == []


def test_added_key() -> None:
    change = _only(_json({"a": 1}), _json({"a": 1, "b": 2}))
    assert change.kind == "added"
    assert change.path == ("b",)
    assert change.old is None
    assert change.new == Summary("number", "2")
    assert change.left_span is None
    assert change.right_span is not None


def test_removed_key() -> None:
    change = _only(_json({"a": 1, "b": 2}), _json({"a": 1}))
    assert change.kind == "removed"
    assert change.path == ("b",)
    assert change.old == Summary("number", "2")
    assert change.new is None
    assert change.left_span is not None
    assert change.right_span is None


def test_changed_scalar() -> None:
    change = _only(_json({"role": "user"}), _json({"role": "admin"}))
    assert change.kind == "changed"
    assert change.path_text == "role"
    assert change.old == Summary("string", '"user"')
    assert change.new == Summary("string", '"admin"')
    assert change.left_span is not None
    assert change.right_span is not None


def test_moved_subtree() -> None:
    item = {"sku": "A-1", "qty": 3}
    left = {"cart": {"line": item}, "saved": {}}
    right = {"cart": {}, "saved": {"line": item}}
    change = _only(_json(left), _json(right))
    assert change.kind == "moved"
    assert change.path == ("cart", "line")
    assert change.path_text == "cart.line"
    assert change.moved_to == ("saved", "line")
    assert change.moved_to_text == "saved.line"
    assert change.old == change.new == Summary("object", '{"sku": "A-1", "qty": 3}')
    assert change.left_span is not None
    assert change.right_span is not None


def test_root_scalar_change_has_empty_path() -> None:
    change = _only(_json(1), _json(2))
    assert change.path == ()
    assert change.path_text == ""


# --- Objects and arrays -------------------------------------------------


def test_key_order_is_ignored() -> None:
    left = load_document('{"a": 1, "b": {"x": 1, "y": 2}}', "json")
    right = load_document('{"b": {"y": 2, "x": 1}, "a": 1}', "json")
    assert diff(left, right).changes == []


def test_key_order_is_ignored_in_yaml() -> None:
    left = load_document("a: 1\nb:\n  x: 1\n  y: 2\n", "yaml")
    right = load_document("b:\n  y: 2\n  x: 1\na: 1\n", "yaml")
    assert diff(left, right).changes == []


def test_arrays_match_by_index() -> None:
    assert _changes([1, 2, 3], [1, 5, 3]) == [("changed", "[1]")]


def test_extra_right_items_are_added_at_the_end() -> None:
    assert _changes({"a": [1]}, {"a": [1, 2, 3]}) == [
        ("added", "a[1]"),
        ("added", "a[2]"),
    ]


def test_extra_left_items_are_removed_at_the_end() -> None:
    assert _changes({"a": [1, 2, 3]}, {"a": [1]}) == [
        ("removed", "a[1]"),
        ("removed", "a[2]"),
    ]


def test_inserting_early_is_one_added_item() -> None:
    # Alignment keeps "b" and "c" matched after the insert.
    assert _changes(["a", "b", "c"], ["a", "new", "b", "c"]) == [("added", "[1]")]


def test_nested_paths_and_path_text() -> None:
    left = {"orders": [{"total": 1}, {"total": 2}, {"total": 40}]}
    right = {"orders": [{"total": 1}, {"total": 2}, {"total": 45}]}
    change = _only(_json(left), _json(right))
    assert change.path == ("orders", 2, "total")
    assert change.path_text == "orders[2].total"


def test_path_text_quotes_keys_with_separators() -> None:
    change = _only(_json({"a.b": {"0": 1}}), _json({"a.b": {"0": 2}}))
    assert change.path == ("a.b", "0")
    assert change.path_text == '["a.b"]["0"]'


def test_object_against_array_is_changed() -> None:
    change = _only(_json({"a": {"x": 1}}), _json({"a": [1]}))
    assert change.kind == "changed"
    assert change.old == Summary("object", '{"x": 1}')
    assert change.new == Summary("array", "[1]")


def test_container_against_scalar_is_changed() -> None:
    change = _only(_json({"a": [1, 2]}), _json({"a": "1, 2"}))
    assert change.kind == "changed"
    assert change.old is not None and change.old.type == "array"
    assert change.new is not None and change.new.type == "string"


def test_empty_containers() -> None:
    assert _changes({}, {}) == []
    assert _changes([], []) == []
    assert _changes({"a": {}}, {"a": []}) == [("changed", "a")]


# --- Array alignment -------------------------------------------------------


def test_one_insert_at_the_start_of_12000_items_is_one_change() -> None:
    items = [{"id": index, "name": f"n{index}"} for index in range(12_000)]
    left = _json({"rows": items})
    right = _json({"rows": [{"id": "new"}, *items]})
    result = diff(left, right)
    assert result.aligned is True
    assert [(c.kind, c.path_text) for c in result.changes] == [("added", "rows[0]")]


def test_a_replace_block_pairs_by_position() -> None:
    # "b" lines up with "X", and the extra "Y" is Added after it.
    assert _changes(["a", "b", "c"], ["a", "X", "Y", "c"]) == [
        ("changed", "[1]"),
        ("added", "[2]"),
    ]


def test_a_crossing_match_falls_back_to_position_pairing() -> None:
    # SequenceMatcher would match {} (left [0], right [2]) and leave the
    # other six items unpaired; pairing by position reports four changes.
    result = diff(_json([{}, 1, 0, 1, 0]), _json([1, 1, {}]))
    assert result.aligned is True
    assert [(c.kind, c.path_text) for c in result.changes] == [
        ("changed", "[0]"),
        ("changed", "[2]"),
        ("removed", "[3]"),
        ("removed", "[4]"),
    ]


def test_deleting_from_the_middle_is_one_removed_item() -> None:
    assert _changes([1, 2, 3, 4], [1, 2, 4]) == [("removed", "[2]")]


def test_a_changed_value_in_a_shifted_item_keeps_both_paths() -> None:
    # "a" and "b" match, so the edited last item pairs left [2] with right [3].
    left = load_document('["a", "b", {"id": 1, "role": "user"}]', "json")
    right = load_document('["new", "a", "b", {"id": 1, "role": "admin"}]', "json")
    added, changed = diff(left, right).changes
    assert (added.kind, added.path_text) == ("added", "[0]")
    assert changed.kind == "changed"
    # The path is the left path; right_path and right_span are on the right.
    assert changed.path == (2, "role")
    assert changed.path_text == "[2].role"
    assert changed.right_path == (3, "role")
    assert changed.left_span == Span(1, 22, 1, 36)
    assert changed.right_span == Span(1, 29, 1, 44)


def test_a_renamed_key_in_a_shifted_item_is_not_a_move() -> None:
    # The two items were compared with each other, so they are one parent.
    item = {"sku": "A-1"}
    left = [{"id": 1}, {"id": 2, "x": item}]
    right = [{"id": 0}, {"id": 1}, {"id": 2, "y": item}]
    # "y" follows "id" on the right, so it sits before the Removed "x".
    assert _changes(left, right) == [
        ("added", "[0]"),
        ("added", "[2].y"),
        ("removed", "[1].x"),
    ]


def test_a_large_repetitive_array_still_aligns() -> None:
    # Every item repeats 120 times. With SequenceMatcher's autojunk, items
    # this common would be ignored and nothing would match.
    items = [{"k": index % 50} for index in range(6000)]
    edited = [{"k": "first"}, *items[1:3000], {"k": "new"}, *items[3000:5999]]
    edited.append({"k": "last"})
    result = diff(_json(items), _json(edited))
    assert result.aligned is True
    assert [(c.kind, c.path_text) for c in result.changes] == [
        ("changed", "[0].k"),
        ("added", "[3000]"),
        ("changed", "[5999].k"),
    ]


def test_many_identical_items_align_around_an_insert() -> None:
    items: list[object] = [{}] * 8000
    result = diff(_json(items), _json([*items[:5000], {"x": 1}, *items[5000:]]))
    assert result.aligned is True
    assert [(c.kind, c.path_text) for c in result.changes] == [("added", "[5000]")]


def test_over_budget_array_falls_back_to_index_matching() -> None:
    # 3000 zeros and 3000 ones on each side: 18 million pair visits.
    left = [0, 1] * 3000
    right = [1, 0] * 3000
    result = diff(_json(left), _json(right))
    assert result.aligned is False
    assert result.unaligned == ((),)
    assert [(c.kind, c.path) for c in result.changes] == [
        ("changed", (index,)) for index in range(6000)
    ]


def test_fallback_names_the_nested_array_that_fell_back() -> None:
    left = {"data": {"items": [0, 1] * 3000, "small": [1, 2]}}
    right = {"data": {"items": [1, 0] * 3000, "small": [2, 1]}}
    result = diff(_json(left), _json(right))
    assert result.unaligned == (("data", "items"),)
    assert result.aligned is False
    assert pickle.loads(pickle.dumps(result)) == result


def test_two_over_budget_arrays_are_both_listed_in_left_order() -> None:
    left = load_document(
        json.dumps({"z": [0, 1] * 3000, "a": {"b": [2, 3] * 3000}}), "json"
    )
    right = load_document(
        json.dumps({"a": {"b": [3, 2] * 3000}, "z": [1, 0] * 3000}), "json"
    )
    result = diff(left, right)
    assert result.unaligned == (("z",), ("a", "b"))


def test_a_small_repetitive_middle_can_go_over_budget() -> None:
    # 1,500 identical items between different ends: 2.25 million visits.
    left = ["start", *([{}] * 1500), "end"]
    right = ["START", *([{}] * 1500), "END"]
    result = diff(_json(left), _json(right))
    assert result.unaligned == ((),)
    assert [change.path_text for change in result.changes] == ["[0]", "[1501]"]


def test_fallback_keeps_the_matching_head_and_tail() -> None:
    left = ["head", *([0, 1] * 3000), "tail"]
    right = ["head", *([1, 0] * 3000), "tail"]
    result = diff(_json(left), _json(right))
    assert result.aligned is False
    assert [change.path for change in result.changes] == [
        (index,) for index in range(1, 6001)
    ]


def test_an_aligned_result_says_so() -> None:
    result = diff(_json({"a": [1, 2]}), _json({"a": [2, 1]}))
    assert result.aligned is True
    assert result.unaligned == ()


def test_alignment_is_the_same_under_any_hash_seed() -> None:
    script = (
        "import json\n"
        "from galley.core.diff import diff, load_document\n"
        "left = load_document(json.dumps(['x', 'y', {'a': 1}, 'z']), 'json')\n"
        "right = load_document(json.dumps(['y', 'x', 'z', {'a': 1}]), 'json')\n"
        "print([(c.kind, c.path_text) for c in diff(left, right).changes])\n"
    )
    outputs = set()
    for seed in ("1", "2", "3"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            env=environment,
            text=True,
        )
        outputs.add(completed.stdout)
    assert len(outputs) == 1


def _records(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": index,
            "name": f"user{index}",
            "email": f"u{index}@example.com",
            "active": index % 2 == 0,
            "score": index * 1.5,
            "tags": ["a", "b", str(index % 7)],
            "address": {"city": "Town", "zip": f"{index:05d}"},
        }
        for index in range(count)
    ]


def test_diffing_two_one_and_a_half_megabyte_documents_takes_under_half_a_second() -> (
    None
):
    base = _records(6000)
    edited = [{"id": -1}, *base]
    edited[3000] = {**edited[3000], "name": "changed"}
    left_text = json.dumps(base, indent=2)
    assert len(left_text) > 1_400_000
    left = load_document(left_text, "json")
    right = load_document(json.dumps(edited, indent=2), "json")
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        result = diff(left, right)
        timings.append(time.perf_counter() - start)
    assert result.aligned is True
    assert [(c.kind, c.path_text) for c in result.changes] == [
        ("added", "[0]"),
        ("changed", "[2999].name"),
    ]
    assert min(timings) < 0.5, timings


# --- Value rules ----------------------------------------------------------


def test_int_equals_float_with_the_same_value() -> None:
    left = load_document('{"n": 1, "m": [2]}', "json")
    right = load_document('{"n": 1.0, "m": [2.0]}', "json")
    assert diff(left, right).changes == []


def test_string_against_number_is_changed_and_shows_the_type() -> None:
    change = _only(_json({"n": "1"}), _json({"n": 1}))
    assert change.kind == "changed"
    assert change.old == Summary("string", '"1"')
    assert change.new == Summary("number", "1")


@pytest.mark.parametrize(
    ("old", "new"),
    [(True, 1), (False, 0), (1, True), (0, False), (True, 1.0)],
)
def test_booleans_are_not_numbers(old: object, new: object) -> None:
    change = _only(_json({"flag": old}), _json({"flag": new}))
    assert change.kind == "changed"
    assert change.old is not None and change.new is not None
    assert {change.old.type, change.new.type} == {"boolean", "number"}


def test_booleans_inside_arrays_are_not_numbers() -> None:
    # true and 1 never match in the alignment, so they pair up as Changed.
    assert _changes([True, False], [1, 0]) == [("changed", "[0]"), ("changed", "[1]")]


def test_null_is_not_a_missing_key() -> None:
    removed = _only(_json({"a": 1, "b": None}), _json({"a": 1}))
    assert removed.kind == "removed"
    assert removed.old == Summary("null", "null")
    added = _only(_json({"a": 1}), _json({"a": 1, "b": None}))
    assert added.kind == "added"
    assert added.new == Summary("null", "null")


def test_null_against_a_value_is_changed() -> None:
    change = _only(_json({"a": None}), _json({"a": 0}))
    assert change.kind == "changed"
    assert change.old == Summary("null", "null")


def test_nan_equals_nan() -> None:
    left = load_document("a: .nan\nb: [.NaN]\n", "yaml")
    right = load_document('{"a": NaN, "b": [NaN]}', "json")
    assert diff(left, right).changes == []
    assert diff(left, left).changes == []


def test_nan_against_a_number_is_changed() -> None:
    change = _only(load_document("a: .nan\n", "yaml"), load_document("a: 1\n", "yaml"))
    assert change.kind == "changed"
    assert change.old == Summary("number", "NaN")


def test_infinity_compares_by_value() -> None:
    left = load_document("a: .inf\nb: -.inf\n", "yaml")
    right = load_document('{"a": Infinity, "b": -Infinity}', "json")
    assert diff(left, right).changes == []


def test_yaml_anchors_compare_by_resolved_value() -> None:
    left = load_document(
        "base: &b {x: 1, y: [1, 2]}\ncopy: *b\nname: &n Ada\nwho: *n\n", "yaml"
    )
    right = _json(
        {"base": {"x": 1, "y": [1, 2]}, "copy": {"x": 1, "y": [1, 2]}, "name": "Ada"}
        | {"who": "Ada"}
    )
    assert diff(left, right).changes == []


def test_yaml_alias_change_is_found_inside_the_alias() -> None:
    left = load_document("base: &b {x: 1}\ncopy: *b\n", "yaml")
    right = load_document("base: {x: 1}\ncopy: {x: 2}\n", "yaml")
    change = _only(left, right)
    assert change.path_text == "copy.x"
    # The alias has no inner spans, so the left span is the alias member.
    assert change.left_span == Span(2, 1, 2, 9)
    assert change.right_span == Span(2, 8, 2, 12)


def test_yaml_merge_keys_compare_by_resolved_value() -> None:
    left = load_document("base: &b\n  x: 1\n  y: 2\nm:\n  <<: *b\n  y: 3\n", "yaml")
    right = _json({"base": {"x": 1, "y": 2}, "m": {"x": 1, "y": 3}})
    assert diff(left, right).changes == []


def test_yaml_merge_list_gives_the_first_mapping_priority() -> None:
    text = "a: &a {k: 1}\nb: &b {k: 2, j: 3}\nm:\n  <<: [*a, *b]\n"
    value = load_document(text, "yaml").value
    assert isinstance(value, dict)
    assert value["m"] == {"k": 1, "j": 3}


@pytest.mark.parametrize(
    ("yaml_text", "json_text"),
    [
        ("a: !!str 1\n", '{"a": "1"}'),
        ("a: !!float 1\n", '{"a": 1}'),
        ("a: !!int '7'\n", '{"a": 7}'),
        ("a: !!bool true\n", '{"a": true}'),
        ("a: !!null ''\n", '{"a": null}'),
        ("a: !!map {b: 1}\n", '{"a": {"b": 1}}'),
        ("a: !!seq [1]\n", '{"a": [1]}'),
        ("a: !!set {x}\n", '{"a": {"x": null}}'),
    ],
)
def test_yaml_tags_compare_by_resolved_value(yaml_text: str, json_text: str) -> None:
    assert (
        diff(load_document(yaml_text, "yaml"), load_document(json_text, "json")).changes
        == []
    )


def test_yaml_dates_and_binary_load_as_json_strings() -> None:
    left = load_document(
        "d: 2026-10-18\nt: 2026-10-18 09:30:00\nb: !!binary aGk=\n", "yaml"
    )
    right = load_document(
        '{"d": "2026-10-18", "t": "2026-10-18T09:30:00", "b": "aGk="}', "json"
    )
    assert diff(left, right).changes == []


def test_yaml_date_against_another_date_is_a_string_change() -> None:
    change = _only(
        load_document("d: 2026-10-18\n", "yaml"),
        load_document('{"d": "2026-10-19"}', "json"),
    )
    assert change.old == Summary("string", '"2026-10-18"')
    assert change.new == Summary("string", '"2026-10-19"')


def test_yaml_non_string_keys_compare_as_text() -> None:
    left = load_document("1: a\ntrue: b\nnull: c\n", "yaml")
    right = load_document('{"1": "a", "true": "b", "null": "c"}', "json")
    assert diff(left, right).changes == []


def test_json_against_yaml_with_changes() -> None:
    left = load_document(
        '{"name": "Ada", "tags": ["x", "y"], "age": 36, "admin": false}', "json"
    )
    right = load_document("age: 37\nname: Ada\ntags: [x]\nteam: core\n", "yaml")
    # "team" follows "tags" on the right, so it sits after the tags changes.
    assert [(c.kind, c.path_text) for c in diff(left, right).changes] == [
        ("removed", "tags[1]"),
        ("added", "team"),
        ("changed", "age"),
        ("removed", "admin"),
    ]


def test_json_against_equivalent_yaml() -> None:
    value = {"users": [{"name": "Ada", "admin": True, "age": 36}], "n": None}
    yaml = YAML(typ="safe")
    buffer = StringIO()
    yaml.dump(value, buffer)
    assert diff(_json(value), load_document(buffer.getvalue(), "yaml")).changes == []


# --- Moved ----------------------------------------------------------------


def test_moved_needs_a_different_parent() -> None:
    # A renamed key keeps its parent, so it is a Removed plus an Added.
    item = {"sku": "A-1"}
    assert sorted(_changes({"old": item}, {"new": item})) == [
        ("added", "new"),
        ("removed", "old"),
    ]


def test_moved_needs_a_unique_match_on_the_right() -> None:
    item = {"sku": "A-1"}
    left = {"a": {"line": item}, "b": {}, "c": {}}
    right = {"a": {}, "b": {"line": item}, "c": {"line": item}}
    assert sorted(_changes(left, right)) == [
        ("added", "b.line"),
        ("added", "c.line"),
        ("removed", "a.line"),
    ]


def test_moved_needs_a_unique_match_on_the_left() -> None:
    item = {"sku": "A-1"}
    left = {"a": {"line": item}, "b": {"line": item}, "c": {}}
    right = {"a": {}, "b": {}, "c": {"line": item}}
    assert sorted(_changes(left, right)) == [
        ("added", "c.line"),
        ("removed", "a.line"),
        ("removed", "b.line"),
    ]


def test_moved_needs_identical_values() -> None:
    left = {"a": {"line": {"sku": "A-1"}}, "b": {}}
    right = {"a": {}, "b": {"line": {"sku": "A-2"}}}
    assert _changes(left, right) == [("removed", "a.line"), ("added", "b.line")]


def test_moved_ignores_key_order_inside_the_subtree() -> None:
    left = load_document('{"a": {"x": {"p": 1, "q": 2}}, "b": {}}', "json")
    right = load_document('{"a": {}, "b": {"x": {"q": 2, "p": 1}}}', "json")
    change = _only(left, right)
    assert change.kind == "moved"
    assert change.moved_to_text == "b.x"


def test_moved_respects_the_value_rules() -> None:
    # true and 1 are different values, so these are not one move.
    left = {"a": {"x": [True]}, "b": {}}
    right = {"a": {}, "b": {"x": [1]}}
    assert _changes(left, right) == [("removed", "a.x"), ("added", "b.x")]
    # 1 and 1.0 are the same value.
    moved = _only(
        _json({"a": {"x": [1]}, "b": {}}), _json({"a": {}, "b": {"x": [1.0]}})
    )
    assert moved.kind == "moved"


def test_moved_between_arrays() -> None:
    item = {"id": 7, "name": "Ada"}
    left = {"active": [item], "archived": []}
    right = {"active": [], "archived": [item]}
    change = _only(_json(left), _json(right))
    assert change.kind == "moved"
    assert change.path_text == "active[0]"
    assert change.moved_to_text == "archived[0]"


def test_moved_keeps_other_changes() -> None:
    item = {"id": 7}
    left = {"a": {"x": item}, "b": {}, "n": 1}
    right = {"a": {}, "b": {"x": item}, "n": 2}
    assert _changes(left, right) == [("moved", "a.x"), ("changed", "n")]


# --- Order ----------------------------------------------------------------


def test_changes_follow_the_left_document() -> None:
    left = load_document('{\n  "z": 1,\n  "a": {"k": 1},\n  "m": [1, 2]\n}', "json")
    right = load_document('{\n  "m": [1, 3],\n  "a": {"k": 2},\n  "z": 2\n}', "json")
    assert [change.path_text for change in diff(left, right).changes] == [
        "z",
        "a.k",
        "m[1]",
    ]


def test_added_sits_after_its_nearest_left_neighbour() -> None:
    left = {"a": 1, "b": 1, "c": 1}
    right = {"a": 2, "b": 1, "new": 1, "c": 2}
    assert _changes(left, right) == [
        ("changed", "a"),
        ("added", "new"),
        ("changed", "c"),
    ]


def test_added_with_no_left_neighbour_comes_first_in_its_parent() -> None:
    left = {"p": {"a": 1, "b": 1}}
    right = {"p": {"first": 0, "a": 2, "b": 1}}
    assert _changes(left, right) == [("added", "p.first"), ("changed", "p.a")]


def test_added_after_a_removed_neighbour_keeps_right_order() -> None:
    left = {"a": 1, "gone": 1, "z": 1}
    right = {"a": 1, "x": 1, "y": 1, "z": 2}
    assert _changes(left, right) == [
        ("added", "x"),
        ("added", "y"),
        ("removed", "gone"),
        ("changed", "z"),
    ]


def test_added_follows_the_changes_inside_its_neighbour() -> None:
    left = {"a": {"k": 1}, "z": 1}
    right = {"a": {"k": 2}, "new": 1, "z": 2}
    assert _changes(left, right) == [
        ("changed", "a.k"),
        ("added", "new"),
        ("changed", "z"),
    ]


def test_moved_sits_where_the_left_value_was() -> None:
    item = {"id": 7}
    left = {"first": 1, "a": {"x": item}, "b": {}, "last": 1}
    right = {"first": 2, "a": {}, "b": {"x": item}, "last": 2}
    assert _changes(left, right) == [
        ("changed", "first"),
        ("moved", "a.x"),
        ("changed", "last"),
    ]


def test_yaml_merged_keys_sort_by_their_merge_line() -> None:
    # Merged keys come first in the loaded mapping, but "<<" is below "y".
    left = load_document("base: &b {x: 1, z: 1}\nm:\n  y: 3\n  <<: *b\n", "yaml")
    right = _json({"base": {"x": 1, "z": 1}, "m": {"x": 5, "y": 4, "z": 1}})
    changes = diff(left, right).changes
    assert [change.path_text for change in changes] == ["m.y", "m.x"]
    assert [change.left_span for change in changes] == [
        Span(3, 3, 3, 7),
        Span(4, 3, 4, 9),
    ]


def test_order_is_stable() -> None:
    left = _json({"a": [1, 2, {"b": 3}], "c": {"d": 4}, "e": 5, "f": {"g": 1}})
    right = _json({"h": 1, "e": 6, "c": {"d": 7}, "a": [1, 9], "g": {"g": 1}})
    first = diff(left, right).changes
    assert all(diff(left, right).changes == first for _ in range(5))


def test_left_spans_are_in_document_order() -> None:
    left = _json({"a": [1, 2, {"b": 3}], "c": {"d": 4}, "e": 5})
    right = _json({"e": 6, "c": {"d": 7}, "a": [1, 9, {"b": 8}]})
    spans = [change.left_span for change in diff(left, right).changes]
    assert all(span is not None for span in spans)
    starts = [(span.line, span.column) for span in spans if span is not None]
    assert starts == sorted(starts)


# --- Spans ----------------------------------------------------------------

_JSON_TEXT = """{
  "a": {
    "b": 1
  },
  "c": [
    10,
    "twenty"
  ],
  "d\\"e": true
}"""


def test_json_spans() -> None:
    spans = load_document(_JSON_TEXT, "json").spans
    assert spans[()] == Span(1, 1, 10, 2)
    # A member starts at its key.
    assert spans[("a",)] == Span(2, 3, 4, 4)
    assert spans[("a", "b")] == Span(3, 5, 3, 11)
    assert spans[("c",)] == Span(5, 3, 8, 4)
    # An array item starts at its value.
    assert spans[("c", 0)] == Span(6, 5, 6, 7)
    assert spans[("c", 1)] == Span(7, 5, 7, 13)
    assert spans[('d"e',)] == Span(9, 3, 9, 15)


def test_json_spans_on_one_line() -> None:
    spans = load_document('[{"a": [1, {}]}, null]', "json").spans
    assert spans[()] == Span(1, 1, 1, 23)
    assert spans[(0,)] == Span(1, 2, 1, 16)
    assert spans[(0, "a")] == Span(1, 3, 1, 15)
    assert spans[(0, "a", 1)] == Span(1, 12, 1, 14)
    assert spans[(1,)] == Span(1, 18, 1, 22)


def test_json_spans_count_characters_not_bytes() -> None:
    spans = load_document('{"é": "ñ", "b": 1}'.encode(), "json").spans
    assert spans[("é",)] == Span(1, 2, 1, 10)
    assert spans[("b",)] == Span(1, 12, 1, 18)


def test_json_spans_cover_every_value() -> None:
    value = {"a": [1, {"b": [None, True]}], "c": {}, "d": []}
    document = _json(value)
    assert set(document.spans) == {
        (),
        ("a",),
        ("a", 0),
        ("a", 1),
        ("a", 1, "b"),
        ("a", 1, "b", 0),
        ("a", 1, "b", 1),
        ("c",),
        ("d",),
    }


_YAML_TEXT = """name: Ada
tags: [x, y]
address:
  city: Town
  zip: "01234"
note: |
  line one
  line two
items:
  - id: 1
  - id: 2
"""


def test_yaml_spans() -> None:
    spans = load_document(_YAML_TEXT, "yaml").spans
    assert spans[("name",)] == Span(1, 1, 1, 10)
    assert spans[("tags",)] == Span(2, 1, 2, 13)
    assert spans[("tags", 1)] == Span(2, 11, 2, 12)
    assert spans[("address",)] == Span(3, 1, 5, 15)
    assert spans[("address", "zip")] == Span(5, 3, 5, 15)
    assert spans[("items", 1)] == Span(11, 5, 11, 10)
    assert spans[("items", 1, "id")] == Span(11, 5, 11, 10)


def test_yaml_block_scalar_span_ends_on_its_last_line() -> None:
    spans = load_document(_YAML_TEXT, "yaml").spans
    assert spans[("note",)] == Span(6, 1, 8, 11)


def test_yaml_spans_match_between_c_and_pure_python() -> None:
    if not _clib_available():
        pytest.skip("ruamel.yaml.clib is not installed")
    text = _YAML_TEXT + "flow: {a: [1, {b: 2}]}\nquoted: 'it''s'\nmulti: >\n  a\n  b\n"
    c_document = _yaml_document(text, pure=False)
    pure_document = _yaml_document(text, pure=True)
    assert c_document.spans == pure_document.spans
    assert c_document.value == pure_document.value


def test_yaml_spans_skip_a_leading_bom() -> None:
    spans = load_document("\ufeffa: 1\n".encode(), "yaml").spans
    assert spans[("a",)] == Span(1, 1, 1, 5)


def test_spans_reach_the_changes_on_both_sides() -> None:
    left = load_document('{\n  "a": 1,\n  "b": {\n    "c": [1, 2]\n  }\n}', "json")
    right = load_document("b:\n  c:\n    - 1\n    - 3\na: 1\n", "yaml")
    change = _only(left, right)
    assert change.path_text == "b.c[1]"
    assert change.left_span == Span(4, 14, 4, 15)
    assert change.right_span == Span(4, 7, 4, 8)


def test_multi_line_removed_value_spans_all_its_lines() -> None:
    left = load_document('{\n  "keep": 1,\n  "gone": {\n    "x": 1\n  }\n}', "json")
    right = load_document('{"keep": 1}', "json")
    change = _only(left, right)
    assert change.left_span == Span(3, 3, 5, 4)


# --- Summaries --------------------------------------------------------------


def test_long_string_summary_is_cut() -> None:
    change = _only(_json({"a": "x" * 500}), _json({"a": "y"}))
    assert change.old is not None
    assert len(change.old.text) == SUMMARY_LIMIT
    assert change.old.text.endswith("…")
    assert change.old.text.startswith('"xxx')


def test_large_container_summary_is_cut() -> None:
    change = _only(_json({"a": list(range(100_000))}), _json({"a": None}))
    assert change.old is not None
    assert change.old.type == "array"
    assert len(change.old.text) == SUMMARY_LIMIT
    assert change.old.text.startswith("[0, 1, 2, ")


def test_summary_that_fits_is_not_cut() -> None:
    change = _only(_json({"a": {"k": [1, "two", None, False]}}), _json({"a": 1}))
    assert change.old == Summary("object", '{"k": [1, "two", null, false]}')


def test_float_summary_keeps_the_decimal_point() -> None:
    change = _only(_json({"a": 40.0}), _json({"a": 45.5}))
    assert change.old == Summary("number", "40.0")
    assert change.new == Summary("number", "45.5")


# --- Depth and errors -------------------------------------------------------


def _nested(depth: int) -> object:
    value: object = 0
    for _ in range(depth):
        value = [value]
    return value


def test_documents_at_max_depth_compare() -> None:
    left = load_document("[" * MAX_DEPTH + "1" + "]" * MAX_DEPTH, "json")
    right = load_document("[" * MAX_DEPTH + "2" + "]" * MAX_DEPTH, "json")
    change = _only(left, right)
    assert change.path == (0,) * (MAX_DEPTH - 1) + (0,)


def test_json_past_max_depth_is_an_input_error() -> None:
    text = "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
    with pytest.raises(InputError) as caught:
        load_document(text, "json")
    assert caught.value.kind == "too_deep"
    assert caught.value.lineno == 1
    assert caught.value.colno == MAX_DEPTH + 1


def test_yaml_past_max_depth_is_an_input_error() -> None:
    text = "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
    with pytest.raises(InputError) as caught:
        load_document(text, "yaml")
    assert caught.value.kind == "too_deep"
    assert caught.value.lineno == 1


def test_diff_past_max_depth_is_an_input_error_not_a_recursion_error() -> None:
    deep = Doc(_nested(MAX_DEPTH + 5), "json")
    with pytest.raises(InputError) as caught:
        diff(deep, Doc(_nested(MAX_DEPTH + 5), "json")).changes
    assert caught.value.kind == "too_deep"
    assert caught.value.details == {"depth": MAX_DEPTH + 1}


def test_moved_hash_past_max_depth_is_an_input_error() -> None:
    deep = _nested(MAX_DEPTH + 5)
    left = Doc({"a": {"x": deep}, "b": {}}, "json")
    right = Doc({"a": {}, "b": {"x": deep}}, "json")
    with pytest.raises(InputError) as caught:
        diff(left, right).changes
    assert caught.value.kind == "too_deep"


def test_recursive_yaml_alias_is_a_yaml_error() -> None:
    with pytest.raises(ComposerError, match="recursive alias"):
        load_document("a: &x [1, *x]\n", "yaml")


def test_undefined_yaml_alias_is_a_yaml_error() -> None:
    with pytest.raises(ComposerError, match="undefined alias"):
        load_document("a: *nope\n", "yaml")


def test_multiple_yaml_documents_are_a_yaml_error() -> None:
    with pytest.raises(ComposerError, match="single document"):
        load_document("---\na: 1\n---\nb: 2\n", "yaml")


def test_duplicate_yaml_key_is_the_safe_loader_error() -> None:
    text = "a: 1\nb: 2\na: 3\n"
    with pytest.raises(DuplicateKeyError) as caught:
        load_document(text, "yaml")
    problem = classify_failure(caught.value, text)
    assert problem is not None
    assert problem.code == "yaml_duplicate"
    assert problem.lineno == 3


def test_unknown_yaml_tag_is_a_constructor_error() -> None:
    with pytest.raises(ConstructorError):
        load_document("a: !custom {b: 1}\n", "yaml")
    with pytest.raises(ConstructorError):
        load_document("a: !custom x\n", "yaml")


def test_json_syntax_error_is_the_json_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        load_document('{"a": 1,}', "json")


def test_invalid_utf8_is_an_input_error() -> None:
    for source in ("json", "yaml"):
        with pytest.raises(InputError) as caught:
            load_document(b'{"a": "\xff"}', source)
        assert caught.value.kind == "invalid_utf8"


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError):
        load_document("{}", "toml")


def test_empty_yaml_is_null() -> None:
    assert load_document("", "yaml").value is None
    assert diff(load_document("", "yaml"), load_document("null", "json")).changes == []


# --- Worker readiness -------------------------------------------------------


def test_results_and_documents_pickle() -> None:
    left = load_document(_YAML_TEXT, "yaml")
    right = load_document(_YAML_TEXT.replace("Ada", "Grace"), "yaml")
    result = diff(left, right)
    assert result.changes
    assert pickle.loads(pickle.dumps(result)) == result
    restored = pickle.loads(pickle.dumps(left))
    assert restored == left


@pytest.mark.parametrize(
    "text",
    [
        _YAML_TEXT,
        "a: &a {x: [1, 2]}\nb: *a\nc:\n  <<: *a\n  y: 3\n",
        "c: 1.5\nd: ~\ne: 'q'\nf: !!int '7'\n",
        "- [1, 2]\n- {a: b}\n- |\n  text\n",
    ],
)
def test_yaml_values_match_the_safe_loader(text: str) -> None:
    assert load_document(text, "yaml").value == YAML(typ="safe").load(text)
