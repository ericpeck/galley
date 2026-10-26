"""The Compare report built in the worker (#65 PR 3a). No Qt."""

import json
import os
import pickle
import random
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from galley.core import diff as diff_module
from galley.core.compare_report import (
    ANCHOR_LIMIT,
    ROOT_LABEL,
    ChangeRow,
    CompareReport,
    GroupRow,
    InfoRow,
    build_report,
    pack_anchors,
    unpack_anchors,
)
from galley.core.diff import Doc, diff, diff_with_pairs, load_document
from galley.core.format import format_document, format_json
from galley.core.worker import (
    CompareEvent,
    CompareFailureEvent,
    CompareJob,
    run_compare,
)

_SETTINGS = settings(
    max_examples=150, deadline=None, derandomize=bool(os.environ.get("CI"))
)


def _doc(value: object) -> Doc:
    return load_document(json.dumps(value, indent=2), "json")


def _report(left: object, right: object) -> CompareReport:
    left_doc = _doc(left)
    right_doc = _doc(right)
    result, pairs = diff_with_pairs(left_doc, right_doc)
    return build_report(result, left_doc, right_doc, pairs)


def _flat(report: CompareReport) -> list[ChangeRow | GroupRow | InfoRow]:
    rows: list[ChangeRow | GroupRow | InfoRow] = []
    for row in report.rows:
        rows.append(row)
        if isinstance(row, GroupRow):
            rows.extend(row.children)
    return rows


def _change_rows(report: CompareReport) -> list[ChangeRow]:
    return [row for row in _flat(report) if isinstance(row, ChangeRow)]


# --- Rows ---------------------------------------------------------------


def test_one_row_per_kind_with_label_glyph_and_spoken_name() -> None:
    left = {"name": "Ada", "role": "user", "old": 1, "from": {"x": {"id": 7}}}
    left["to"] = {}
    right = {"name": "Ada", "role": "admin", "from": {}, "to": {"x": {"id": 7}}}
    right["new"] = [1, 2]
    report = _report(left, right)
    rows = [
        (row.kind, row.glyph, row.label, row.path_text, row.accessible_name)
        for row in report.rows
    ]
    assert rows == [
        ("changed", "~", "Changed", "role", 'Changed, role, "user" to "admin"'),
        ("removed", "−", "Removed", "old", "Removed, old, 1"),
        ("moved", "⇄", "Moved", "from.x", "Moved, from, x, to to, x"),
        ("added", "+", "Added", "new", "Added, new, […]"),
    ]
    assert report.count == 4


def test_rows_carry_the_summaries_spans_and_moved_target() -> None:
    left = {"a": {"b": {"c": 40}}, "from": {"k": {"id": 1}}, "to": {}}
    right = {"a": {"b": {"c": 45}}, "from": {}, "to": {"k": {"id": 1}}}
    left_doc, right_doc = _doc(left), _doc(right)
    result, pairs = diff_with_pairs(left_doc, right_doc)
    report = build_report(result, left_doc, right_doc, pairs)
    changed, moved = report.rows
    assert isinstance(changed, ChangeRow) and isinstance(moved, ChangeRow)
    assert changed.path_text == "a.b.c"
    assert (changed.old.text, changed.new.text) == ("40", "45")
    assert changed.accessible_name == "Changed, a, b, c, 40 to 45"
    assert changed.left_span == left_doc.spans[("a", "b", "c")]
    assert changed.right_span == right_doc.spans[("a", "b", "c")]
    assert (changed.left_line, changed.right_line) == (4, 4)
    assert moved.moved_to_text == "to.k"
    assert moved.left_line == left_doc.spans[("from", "k")].line
    assert moved.right_line == right_doc.spans[("to", "k")].line


def test_nested_paths_read_aloud_without_brackets() -> None:
    left = {"orders": [{"total": 40}, {"total": 1}], "a.b": {"0": True}}
    right = {"orders": [{"total": 45}, {"total": 1}], "a.b": {"0": False}}
    report = _report(left, right)
    assert [(row.path_text, row.accessible_name) for row in report.rows] == [
        ("orders[0].total", "Changed, orders, 0, total, 40 to 45"),
        ('["a.b"]["0"]', "Changed, a.b, 0, true to false"),
    ]


def test_the_root_is_labelled_whole_document() -> None:
    report = _report({"a": 1}, [1])
    [row] = report.rows
    assert isinstance(row, ChangeRow)
    assert row.path_text == ROOT_LABEL == "Whole document"
    assert row.accessible_name == "Changed, Whole document, {…} to […]"


def test_a_key_with_a_space_sounds_different_from_a_dotted_path() -> None:
    spaced = _report({"first name": 1}, {"first name": 2})
    dotted = _report({"first": {"name": 1}}, {"first": {"name": 2}})
    assert [row.accessible_name for row in spaced.rows] == [
        "Changed, first name, 1 to 2"
    ]
    assert [row.accessible_name for row in dotted.rows] == [
        "Changed, first, name, 1 to 2"
    ]


def test_empty_containers_are_spoken_as_written() -> None:
    report = _report({"a": 1}, {"a": 1, "b": {}, "c": []})
    assert [row.accessible_name for row in report.rows] == [
        "Added, b, {}",
        "Added, c, []",
    ]


def test_identical_documents_give_an_empty_report() -> None:
    value = {"users": [{"id": 1, "tags": ["a", "b"]}], "ok": True}
    report = _report(value, {"ok": True, "users": [{"tags": ["a", "b"], "id": 1}]})
    assert report.rows == ()
    assert report.count == 0
    assert report.left.markers == {} and report.right.markers == {}
    assert report.left.tints == () and report.right.tints == ()
    assert report.anchor_pairs()


def test_without_unaligned_arrays_there_are_no_info_or_group_rows() -> None:
    report = _report({"items": list(range(50))}, {"items": [-1, *range(50), 99]})
    assert all(isinstance(row, ChangeRow) for row in report.rows)
    assert [row.path_text for row in report.rows] == ["items[0]", "items[51]"]


# --- Arrays compared by position -----------------------------------------


def test_runs_in_an_unaligned_array_fold_into_groups() -> None:
    left = {"users": [1, 2, 3, 4, 5, 6]}
    right = {"users": [1, 9, 9, 4, 9, 9, 7]}
    with mock.patch.object(diff_module, "ALIGN_BUDGET", 0):
        report = _report(left, right)
    info, first, second = report.rows
    assert isinstance(info, InfoRow)
    assert info.text == "users: items compared by position (too complex to align)"
    assert info.accessible_name == (
        "users, items compared by position, too complex to align"
    )
    assert isinstance(first, GroupRow) and isinstance(second, GroupRow)
    assert (first.path_text, first.text) == (
        "users[1–2]",
        "users[1–2]: 2 items differ",
    )
    assert [row.path_text for row in first.children] == ["users[1]", "users[2]"]
    assert first.accessible_name == "users, 1 to 2, 2 items differ"
    assert second.text == "users[4–6]: 3 items differ"
    assert [(row.kind, row.path_text) for row in second.children] == [
        ("changed", "users[4]"),
        ("changed", "users[5]"),
        ("added", "users[6]"),
    ]
    assert (first.item_count, first.count, second.count) == (2, 2, 3)
    assert first.left_line == first.children[0].left_line
    assert report.count == 5


def test_a_run_of_one_index_stays_as_plain_rows_under_the_info_row() -> None:
    left = {"users": ["start", *([{}] * 1500), "end"]}
    right = {"users": ["START", *([{}] * 1500), "END"]}
    report = _report(left, right)
    assert [type(row).__name__ for row in report.rows] == [
        "InfoRow",
        "ChangeRow",
        "ChangeRow",
    ]
    assert report.rows[0].text == (
        "users: items compared by position (too complex to align)"
    )
    assert [row.path_text for row in report.rows[1:]] == ["users[0]", "users[1501]"]


def test_an_over_budget_array_is_one_group_counting_every_item() -> None:
    left = {"data": {"items": [0, 1] * 3000, "small": [1, 2]}}
    right = {"data": {"items": [1, 0] * 3000, "small": [2, 1]}}
    report = _report(left, right)
    info, group, *rest = report.rows
    assert info.text == (
        "data.items: items compared by position (too complex to align)"
    )
    assert info.accessible_name == (
        "data, items, items compared by position, too complex to align"
    )
    assert isinstance(group, GroupRow)
    assert group.text == "data.items[0–5,999]: 6,000 items differ"
    assert group.accessible_name == "data, items, 0 to 5,999, 6,000 items differ"
    assert group.count == 6000
    assert all(isinstance(row, ChangeRow) for row in rest)
    assert report.count == 6000 + len(rest)


def test_added_items_join_the_group_by_the_arrays_right_path() -> None:
    # The outer array aligns, so left teams[1] pairs with right teams[2]; the
    # inner array falls back, and its Added item has a right path.
    team = {"name": "core"}
    left = {"teams": [team, {"m": [1, 2, 2, 3]}]}
    right = {"teams": [{"name": "new"}, team, {"m": [9, 2, 2, 9, 8]}]}
    with mock.patch.object(diff_module, "ALIGN_BUDGET", 1):
        result, _pairs = diff_with_pairs(_doc(left), _doc(right))
        report = _report(left, right)
    assert result.unaligned == (("teams", 1, "m"),)
    assert [type(row).__name__ for row in report.rows] == [
        "ChangeRow",
        "InfoRow",
        "ChangeRow",
        "GroupRow",
    ]
    added, info, single, group = report.rows
    assert added.path_text == "teams[0]"
    assert info.path_text == "teams[1].m"
    assert single.path_text == "teams[1].m[0]"
    assert group.text == "teams[1].m[3–4]: 2 items differ"
    assert [(row.kind, row.path_text) for row in group.children] == [
        ("changed", "teams[1].m[3]"),
        ("added", "teams[2].m[4]"),
    ]


def test_an_unaligned_array_inside_a_group_gets_its_info_row_inside() -> None:
    left = {"rows": [[1, 2, 1], [5], "w", "x"]}
    right = {"rows": [[2, 1, 3], [6], "x", "w"]}
    with mock.patch.object(diff_module, "ALIGN_BUDGET", 0):
        result, _pairs = diff_with_pairs(_doc(left), _doc(right))
        report = _report(left, right)
    assert result.unaligned == (("rows",), ("rows", 0))
    info, group = report.rows
    assert info.path_text == "rows"
    assert isinstance(group, GroupRow)
    assert group.text == "rows[0–3]: 4 items differ"
    assert [type(row).__name__ for row in group.children][:2] == [
        "InfoRow",
        "ChangeRow",
    ]
    assert group.children[0].text == (
        "rows[0]: items compared by position (too complex to align)"
    )
    assert group.count == report.count == len(result.changes)


def test_neighbouring_unaligned_arrays_keep_their_own_info_rows_and_groups() -> None:
    # "b" picks up at index 3, right after "a"'s run ends at 2, so only the
    # change of array keeps the two runs apart.
    left = {"a": [1, 2, 3, 9], "b": [0, 0, 0, 1, 2]}
    right = {"a": [2, 1, 4, 9], "b": [0, 0, 0, 2, 1]}
    with mock.patch.object(diff_module, "ALIGN_BUDGET", 0):
        result, _pairs = diff_with_pairs(_doc(left), _doc(right))
        report = _report(left, right)
    assert result.unaligned == (("a",), ("b",))
    assert [
        (type(row).__name__, row.path_text, getattr(row, "count", None))
        for row in report.rows
    ] == [
        ("InfoRow", "a", None),
        ("GroupRow", "a[0\u20132]", 3),
        ("InfoRow", "b", None),
        ("GroupRow", "b[3\u20134]", 2),
    ]


@pytest.mark.parametrize("swap", [False, True])
def test_a_group_jumps_to_the_first_child_that_has_a_line_on_each_side(
    swap: bool,
) -> None:
    # Each item gains key "a" before "b" changes. Left to right, "a" is
    # Added and has no left line; swapped, it is Removed and has no right
    # line. Either way the group's first child lacks a line on one side.
    item_left = {"b": 2}
    item_right = {"a": 1, "b": 3}
    left = {"users": [item_left, item_left, "p", "q"]}
    right = {"users": [item_right, item_right, "q", "p"]}
    left_doc, right_doc = _doc(left), _doc(right)
    if swap:
        left_doc, right_doc = right_doc, left_doc
    with mock.patch.object(diff_module, "ALIGN_BUDGET", 0):
        result, pairs = diff_with_pairs(left_doc, right_doc)
    report = build_report(result, left_doc, right_doc, pairs)
    _info, group = report.rows
    assert isinstance(group, GroupRow)
    first, second = group.children[:2]
    assert second.kind == "changed"
    if swap:
        assert (first.kind, first.right_line) == ("removed", None)
        assert group.right_line == second.right_line is not None
        assert group.left_line == first.left_line is not None
    else:
        assert (first.kind, first.left_line) == ("added", None)
        assert group.left_line == second.left_line is not None
        assert group.right_line == first.right_line is not None


def test_an_unaligned_root_array_uses_the_root_label() -> None:
    report = _report([0, 1] * 3000, [1, 0] * 3000)
    info, group = report.rows
    assert info.text == (
        "Whole document: items compared by position (too complex to align)"
    )
    assert info.accessible_name == (
        "Whole document, items compared by position, too complex to align"
    )
    assert group.path_text == "Whole document [0–5,999]"
    assert group.text == "Whole document [0–5,999]: 6,000 items differ"
    assert group.accessible_name == ("Whole document, 0 to 5,999, 6,000 items differ")


@_SETTINGS
@given(
    st.recursive(
        st.sampled_from([0, 1, "a", None, True]),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.sampled_from(["a", "b", "c"]), children, max_size=3),
        ),
        max_leaves=20,
    ),
    st.recursive(
        st.sampled_from([0, 1, "a", None, True]),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.sampled_from(["a", "b", "c"]), children, max_size=3),
        ),
        max_leaves=20,
    ),
    st.sampled_from([0, 1, 2_000_000]),
)
def test_every_change_appears_exactly_once(
    left: object, right: object, budget: int
) -> None:
    with mock.patch.object(diff_module, "ALIGN_BUDGET", budget):
        left_doc, right_doc = _doc(left), _doc(right)
        result, pairs = diff_with_pairs(left_doc, right_doc)
        report = build_report(result, left_doc, right_doc, pairs)
    rows = _change_rows(report)
    assert [(row.kind, row.left_span, row.right_span) for row in rows] == [
        (change.kind, change.left_span, change.right_span) for change in result.changes
    ]
    assert report.count == len(result.changes)
    for row in report.rows:
        if isinstance(row, GroupRow):
            assert row.first < row.last
            assert all(not isinstance(child, GroupRow) for child in row.children)
    infos = [row.path_text for row in _flat(report) if isinstance(row, InfoRow)]
    assert len(infos) == len(set(infos))


# --- Marks --------------------------------------------------------------


def test_marks_follow_each_sides_spans() -> None:
    left = {"keep": 1, "gone": {"x": 1, "y": 2}, "c": 1, "from": {"k": {"id": 1}}}
    left["to"] = {}
    right = {"keep": 1, "c": 2, "from": {}, "to": {"k": {"id": 1}}, "new": True}
    left_doc, right_doc = _doc(left), _doc(right)
    result, pairs = diff_with_pairs(left_doc, right_doc)
    report = build_report(result, left_doc, right_doc, pairs)
    gone = left_doc.spans[("gone",)]
    assert report.left.markers == {
        gone.line: "removed",
        left_doc.spans[("c",)].line: "changed",
        left_doc.spans[("from", "k")].line: "moved",
    }
    assert report.right.markers == {
        right_doc.spans[("c",)].line: "changed",
        right_doc.spans[("to", "k")].line: "moved",
        right_doc.spans[("new",)].line: "added",
    }
    assert report.left.tints[0] == (gone.line, gone.end_line, "removed")
    assert gone.end_line > gone.line
    assert "added" not in report.left.markers.values()
    assert "removed" not in report.right.markers.values()


def test_the_first_change_on_a_shared_line_wins_the_marker() -> None:
    left = load_document('{"a": 1, "b": 2}', "json")
    right = load_document('{"a": 2}', "json")
    result, pairs = diff_with_pairs(left, right)
    report = build_report(result, left, right, pairs)
    assert [change.kind for change in result.changes] == ["changed", "removed"]
    assert report.left.markers == {1: "changed"}
    assert report.left.tints == ((1, 1, "changed"), (1, 1, "removed"))


# --- Anchors ------------------------------------------------------------


def test_scalar_array_items_are_not_anchored_by_index() -> None:
    # After an insert, left "a" sits at index 0 and right "a" at index 1.
    # Scalar items carry no pairing, so only the array and its neighbours
    # anchor: pairing by index would join left "a" to right "new".
    left_doc = _doc({"items": ["a", "b", "c"], "k": 1})
    right_doc = _doc({"items": ["new", "a", "b", "c"], "k": 1})
    result, pairs = diff_with_pairs(left_doc, right_doc)
    report = build_report(result, left_doc, right_doc, pairs)
    assert report.anchor_pairs() == [(1, 1), (2, 2), (6, 7), (7, 8), (8, 9)]


def test_anchors_follow_the_alignment_after_an_early_insert() -> None:
    items = [{"id": index, "name": f"n{index}"} for index in range(20)]
    left_doc = _doc({"items": items})
    right_doc = _doc({"items": [{"id": -1, "name": "new"}, *items]})
    result, pairs = diff_with_pairs(left_doc, right_doc)
    report = build_report(result, left_doc, right_doc, pairs)
    anchors = dict(report.anchor_pairs())
    for index in range(20):
        left_line = left_doc.spans[("items", index, "name")].line
        right_line = right_doc.spans[("items", index + 1, "name")].line
        assert anchors[left_line] == right_line


def test_anchors_strictly_increase_on_both_sides() -> None:
    left = {"b": {"x": 1, "y": 2}, "a": [1, 2, 3], "c": {"z": [{"k": 1}]}}
    right = {"c": {"z": [{"k": 2}]}, "a": [3, 2, 1], "b": {"y": 2, "x": 1}}
    pairs = _report(left, right).anchor_pairs()
    assert len(pairs) >= 2
    for (left_a, right_a), (left_b, right_b) in zip(pairs, pairs[1:], strict=False):
        assert left_a < left_b
        assert right_a < right_b


def test_two_left_lines_on_one_right_line_keep_one_anchor() -> None:
    # Left is spread over lines 1-4, right is one line: every left line maps
    # to right line 1, and anchors must strictly increase on both sides, so
    # one pair is left. Of the tied pairs, the last one (the closing brace)
    # is the one kept.
    left = load_document('{\n  "a": 1,\n  "b": 2\n}', "json")
    right = load_document('{"a": 1, "b": 2}', "json")
    result, pairs = diff_with_pairs(left, right)
    assert build_report(result, left, right, pairs).anchor_pairs() == [(4, 1)]
    # The other way round, each left line keeps its lowest right line.
    result, pairs = diff_with_pairs(right, left)
    assert build_report(result, right, left, pairs).anchor_pairs() == [(1, 1)]


def test_anchor_limit_is_ten_thousand_pairs() -> None:
    assert ANCHOR_LIMIT == 10_000


def test_the_anchor_payload_is_at_most_80_kb() -> None:
    value = {f"key{index:05d}": index for index in range(ANCHOR_LIMIT * 2)}
    report = _report(value, value)
    assert len(report.anchors) == 80_000


def test_anchors_at_exactly_the_limit_are_all_kept() -> None:
    # A flat object of n keys gives n + 2 anchors: its first line, each key
    # line, and its last line.
    value = {f"key{index:05d}": index for index in range(ANCHOR_LIMIT - 2)}
    pairs = _report(value, value).anchor_pairs()
    assert pairs == [(line, line) for line in range(1, ANCHOR_LIMIT + 1)]


def test_one_anchor_over_the_limit_is_thinned_to_the_limit() -> None:
    value = {f"key{index:05d}": index for index in range(ANCHOR_LIMIT - 1)}
    report = _report(value, value)
    pairs = report.anchor_pairs()
    assert len(pairs) == ANCHOR_LIMIT
    assert len(report.anchors) == ANCHOR_LIMIT * 8
    assert pairs[0] == (1, 1)
    assert pairs[-1] == (ANCHOR_LIMIT + 1, ANCHOR_LIMIT + 1)
    every = {(line, line) for line in range(1, ANCHOR_LIMIT + 2)}
    assert len(every - set(pairs)) == 1


def test_the_anchor_payload_is_capped() -> None:
    value = {f"key{index:05d}": index for index in range(ANCHOR_LIMIT * 3)}
    report = _report(value, value)
    pairs = report.anchor_pairs()
    assert len(pairs) == ANCHOR_LIMIT
    assert len(report.anchors) <= ANCHOR_LIMIT * 8
    assert pairs[0] == (1, 1)
    last = ANCHOR_LIMIT * 3 + 2
    assert pairs[-1] == (last, last)
    assert all(a < b for (a, _), (b, _) in zip(pairs, pairs[1:], strict=False))


def test_anchors_round_trip_through_bytes() -> None:
    pairs = [(1, 1), (3, 4), (70_000, 2**31)]
    assert unpack_anchors(pack_anchors(pairs)) == pairs
    assert unpack_anchors(b"") == []


# --- Worker -------------------------------------------------------------


def _job(left: str, right: str, fmt: str = "json", **kwargs: object) -> CompareJob:
    return CompareJob(
        job_id=3,
        left_text=left,
        left_format=fmt,
        right_text=right,
        right_format=fmt,
        **kwargs,
    )


def test_the_compare_event_carries_the_report_and_pickles() -> None:
    event = run_compare(_job('{"a": [1, 2], "b": 1}', '{"a": [2], "c": 1}'))
    assert isinstance(event, CompareEvent)
    left = load_document('{"a": [1, 2], "b": 1}', "json")
    right = load_document('{"a": [2], "c": 1}', "json")
    result, pairs = diff_with_pairs(left, right)
    assert event.result == result == diff(left, right)
    assert event.report == build_report(result, left, right, pairs)
    assert (event.left_text, event.right_text) == (None, None)
    assert pickle.loads(pickle.dumps(event)) == event


def test_reformat_compares_the_formatted_text_in_one_job() -> None:
    left = '{"name":"Ada","orders":[{"total":40}]}'
    right = '{"orders":[{"total":45}],"name":"Ada","vip":true}'
    event = run_compare(_job(left, right, reformat=True))
    assert isinstance(event, CompareEvent)
    assert event.left_text == format_json(left)
    assert event.right_text == format_json(right)
    left_lines = event.left_text.splitlines()
    right_lines = event.right_text.splitlines()
    rows = _change_rows(event.report)
    # Added "vip" sits after "name", its nearest key shared with the left.
    assert [row.path_text for row in rows] == ["vip", "orders[0].total"]
    added, changed = rows
    assert '"total": 40' in left_lines[changed.left_line - 1]
    assert '"total": 45' in right_lines[changed.right_line - 1]
    assert '"vip": true' in right_lines[added.right_line - 1]
    assert pickle.loads(pickle.dumps(event)) == event


@pytest.mark.parametrize(
    "text",
    [
        '{"name":"Ada","n":[1.50,1E2,-0.0,123456789012345678901234567890]}',
        '{"caf\\u00e9": "\u65e5\u672c", "e": "\\ud83d\\ude00", "z": null}',
        "[" * 400 + "1" + "]" * 400,
        '{"a": {"b": [true, false, {}], "c": []}, "d": 1e400}',
    ],
)
def test_reformat_reuses_the_formatters_json_value(text: str) -> None:
    other = '{"name": "Bo", "d": 2}'

    def _no_second_parse(*_args: object) -> None:
        raise AssertionError("JSON was parsed again after formatting")

    with mock.patch("galley.core.worker.load_document", _no_second_parse):
        event = run_compare(_job(text, other, reformat=True))
    assert isinstance(event, CompareEvent)
    assert event.left_text == format_document(text, "json", "json")
    left = load_document(event.left_text, "json")
    right = load_document(event.right_text, "json")
    result, pairs = diff_with_pairs(left, right)
    assert event.result == result
    assert event.report == build_report(result, left, right, pairs)


def test_reformat_keeps_each_sides_format() -> None:
    left = "b: 1\na:   [1,  2]\n"
    right = "a: [1, 3]\nb: 1\n"
    event = run_compare(_job(left, right, "yaml", reformat=True))
    assert isinstance(event, CompareEvent)
    assert event.left_text == format_document(left, "yaml", "yaml")
    assert event.right_text == format_document(right, "yaml", "yaml")
    [row] = event.report.rows
    assert row.path_text == "a[1]"
    assert event.right_text.splitlines()[row.right_line - 1].strip() == "- 3"


def test_without_reformat_the_spans_are_in_the_text_as_sent() -> None:
    left = '{"a": 1,\n "b": 2}'
    event = run_compare(_job(left, '{"a": 1, "b": 3}'))
    assert isinstance(event, CompareEvent)
    [row] = event.report.rows
    assert (row.left_line, row.right_line) == (2, 1)


@pytest.mark.parametrize("reformat", [False, True])
def test_a_side_that_fails_to_load_is_reported_located_in_its_own_text(
    reformat: bool,
) -> None:
    event = run_compare(_job('{"a": 1}', '{\n  "a": 1,\n}', reformat=reformat))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "right"
    assert [problem.lineno for problem in event.problems] == [2]


def test_a_document_too_deep_to_compare_fails_without_a_report() -> None:
    deep = "[" * 2000 + "]" * 2000
    event = run_compare(_job(deep, "[]"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "left"
    assert event.error is not None and event.error.kind == "too_deep"


def test_reformat_ignores_the_sort_keys_setting() -> None:
    # Sort keys (#59) changes Output only. Compare reformats both sides and
    # never sorts, and a compare job has no way to ask for sorting.
    text = '{"b": 1, "a": {"d": 2, "c": 3}}'
    event = run_compare(_job(text, text, reformat=True))
    assert isinstance(event, CompareEvent)
    unsorted = format_document(text, "json", "json", sort_keys=False)
    sorted_text = format_document(text, "json", "json", sort_keys=True)
    assert unsorted != sorted_text
    assert event.left_text == event.right_text == unsorted
    assert "sort_keys" not in CompareJob.__dataclass_fields__


# --- Performance --------------------------------------------------------

# Building the report of a 1.5 MB pair, after the diff.
_REPORT_BUDGET_S = 0.25
# The whole job in-process, pickling the event as the queue does: both
# loads, the diff, the report, and Format first when ``reformat`` is set.
# 1.8 s after the #129 speed-up (team ruling on #133).
_JOB_BUDGET_S = 1.8
# Isaac's #65 budgets for the diff, here with the report built as well.
_ISAAC_BUDGET_S = 0.5
# The reordered array leaves 20,000 rows to build, so the diff plus the
# report gets its own budget: 0.7 s after the #129 speed-up (#133).
_REORDER_BUDGET_S = 0.7


def _large_pair() -> tuple[str, str]:
    """About 1.5 MB of JSON a side: an early array insert, scattered changes,
    and one repetitive array that falls back to comparing by position."""
    rng = random.Random(65)
    users = [
        {
            "id": index,
            "name": f"user-{index}",
            "email": f"user{index}@example.com",
            "tags": [rng.choice(["a", "b", "c"]) for _ in range(3)],
            "score": rng.randint(0, 1000),
        }
        for index in range(8_500)
    ]
    left = {"users": users, "flags": [0, 1] * 1_600}
    right_users = [dict(user) for user in users]
    for user in rng.sample(right_users, 2_500):
        user["score"] += 1
    right_users.insert(3, {"id": -1, "name": "inserted"})
    right = {"users": right_users, "flags": [1, 0] * 1_600}
    return json.dumps(left, indent=2), json.dumps(right, indent=2)


def _keys_changed_pair() -> tuple[str, str]:
    """About 1 MB a side, with 10% of the record keys changed."""
    rng = random.Random(59)
    records = [
        {
            "id": index,
            "name": f"user-{index}",
            "email": f"u{index}@example.com",
            "city": rng.choice(["Oslo", "Lima", "Pune"]),
            "score": rng.randint(0, 999),
        }
        for index in range(7_500)
    ]
    changed = [dict(record) for record in records]
    keys = [
        (index, key)
        for index in range(len(changed))
        for key in ("name", "email", "city", "score")
    ]
    for index, key in rng.sample(keys, len(keys) // 10):
        changed[index][key] = f"{changed[index][key]}x"
    return (
        json.dumps({"records": records}, indent=2),
        json.dumps({"records": changed}, indent=2),
    )


def _reordered_pair() -> tuple[str, str]:
    """A 10,000-item array and the same items shuffled."""
    items = [{"id": index, "value": f"value-{index}"} for index in range(10_000)]
    shuffled = list(items)
    random.Random(60).shuffle(shuffled)
    return json.dumps(items, indent=2), json.dumps(shuffled, indent=2)


def _aligned_pair() -> tuple[str, str]:
    """A 5,000-item array with edits, inserts and deletes that all align."""
    rng = random.Random(61)
    items = [{"id": index, "value": f"value-{index}"} for index in range(5_000)]
    edited = [dict(item) for item in items]
    for index in rng.sample(range(5_000), 250):
        edited[index]["value"] += "!"
    for index in sorted(rng.sample(range(5_000), 50), reverse=True):
        del edited[index]
    for index in rng.sample(range(4_900), 50):
        edited.insert(index, {"id": -index, "value": "new"})
    return json.dumps(items, indent=2), json.dumps(edited, indent=2)


def _loaded(pair: tuple[str, str]) -> tuple[Doc, Doc]:
    return load_document(pair[0], "json"), load_document(pair[1], "json")


@pytest.mark.perf
def test_building_the_report_for_a_1_5_mb_pair_is_within_budget(perf_budget) -> None:
    left_text, right_text = _large_pair()
    assert len(left_text.encode()) > 1_400_000
    left_doc, right_doc = _loaded((left_text, right_text))
    result, pairs = diff_with_pairs(left_doc, right_doc)
    assert result.unaligned == (("flags",),)
    assert len(result.changes) > 5_000
    report = build_report(result, left_doc, right_doc, pairs)
    assert len(report.anchors) <= ANCHOR_LIMIT * 8
    perf_budget.within(
        _REPORT_BUDGET_S,
        lambda: build_report(result, left_doc, right_doc, pairs),
        fixture=f"1.5 MB JSON pair, {len(result.changes)} changes, report only",
    )


@pytest.mark.perf
@pytest.mark.parametrize("reformat", [False, True])
def test_a_compare_job_for_a_1_5_mb_pair_is_within_budget(
    perf_budget, reformat: bool
) -> None:
    left_text, right_text = _large_pair()
    job = _job(left_text, right_text, reformat=reformat)
    assert isinstance(run_compare(job), CompareEvent)
    perf_budget.within(
        _JOB_BUDGET_S,
        lambda: pickle.loads(pickle.dumps(run_compare(job))),
        fixture=f"1.5 MB JSON pair, whole job and pickle, reformat={reformat}",
    )


@pytest.mark.perf
@pytest.mark.parametrize(
    ("make_pair", "budget_s", "fixture"),
    [
        (_keys_changed_pair, _ISAAC_BUDGET_S, "1 MB JSON pair, 10% of keys changed"),
        (_reordered_pair, _REORDER_BUDGET_S, "10,000-item array reordered"),
        (_aligned_pair, _ISAAC_BUDGET_S, "5,000-item array, aligned edits"),
    ],
)
def test_diff_and_report_meet_isaacs_budgets(
    perf_budget, make_pair, budget_s: float, fixture: str
) -> None:
    left_doc, right_doc = _loaded(make_pair())
    result, _pairs = diff_with_pairs(left_doc, right_doc)
    assert result.changes and not result.unaligned

    def _diff_and_report() -> CompareReport:
        result, pairs = diff_with_pairs(left_doc, right_doc)
        return build_report(result, left_doc, right_doc, pairs)

    perf_budget.within(budget_s, _diff_and_report, fixture=fixture)
