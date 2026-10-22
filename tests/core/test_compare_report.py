"""The Compare report built in the worker (#65 PR 3a). No Qt."""

import json
import os
import pickle
import random
import time
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
        ("moved", "⇄", "Moved", "from.x", "Moved, from x, to to x"),
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
    assert changed.accessible_name == "Changed, a b c, 40 to 45"
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
        ("orders[0].total", "Changed, orders 0 total, 40 to 45"),
        ('["a.b"]["0"]', "Changed, a.b 0, true to false"),
    ]


def test_the_root_is_labelled_whole_document() -> None:
    report = _report({"a": 1}, [1])
    [row] = report.rows
    assert isinstance(row, ChangeRow)
    assert row.path_text == ROOT_LABEL == "Whole document"
    assert row.accessible_name == "Changed, Whole document, {…} to […]"


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
    assert info.text == "users items: compared by position (too complex to align)."
    assert info.accessible_name == info.text
    assert isinstance(first, GroupRow) and isinstance(second, GroupRow)
    assert (first.path_text, first.text) == (
        "users[1–2]",
        "users[1–2]: 2 items differ",
    )
    assert [row.path_text for row in first.children] == ["users[1]", "users[2]"]
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
        "users items: compared by position (too complex to align)."
    )
    assert [row.path_text for row in report.rows[1:]] == ["users[0]", "users[1501]"]


def test_an_over_budget_array_is_one_group_counting_every_item() -> None:
    left = {"data": {"items": [0, 1] * 3000, "small": [1, 2]}}
    right = {"data": {"items": [1, 0] * 3000, "small": [2, 1]}}
    report = _report(left, right)
    info, group, *rest = report.rows
    assert info.text == (
        "data.items items: compared by position (too complex to align)."
    )
    assert isinstance(group, GroupRow)
    assert group.text == "data.items[0–5999]: 6000 items differ"
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
        "rows[0] items: compared by position (too complex to align)."
    )
    assert group.count == report.count == len(result.changes)


def test_an_unaligned_root_array_uses_the_root_label() -> None:
    report = _report([0, 1] * 3000, [1, 0] * 3000)
    info, group = report.rows
    assert info.text == (
        "Whole document items: compared by position (too complex to align)."
    )
    assert group.text == "[0–5999]: 6000 items differ"


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


@pytest.mark.skip(
    reason="needs PR #100 (Sort keys, #59): format_document(sort_keys=...) "
    "is not on main yet"
)
def test_reformat_ignores_the_sort_keys_setting() -> None:
    text = '{"b": 1, "a": {"d": 2, "c": 3}}'
    event = run_compare(_job(text, text, reformat=True))
    assert isinstance(event, CompareEvent)
    unsorted = format_document(text, "json", "json", sort_keys=False)
    sorted_text = format_document(text, "json", "json", sort_keys=True)
    assert unsorted != sorted_text
    assert event.left_text == event.right_text == unsorted
    assert "sort_keys" not in CompareJob.__dataclass_fields__


# --- Performance --------------------------------------------------------

# Local budget for building the report of a 1.5 MB pair. Gating CI is about
# 3.5x slower on its shared runners, so it gets the loose budget (Erik, #65
# point 6). The case prints its time either way.
_REPORT_BUDGET_S = 0.25
# The whole job in-process, pickling the event as the queue does: both
# loads, the diff, the report, and Format first when ``reformat`` is set.
_JOB_BUDGET_S = 1.5
_CI_FACTOR = 3.5


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


@pytest.mark.perf
def test_building_the_report_for_a_1_5_mb_pair_is_within_budget() -> None:
    left_text, right_text = _large_pair()
    assert len(left_text.encode()) > 1_400_000
    left_doc = load_document(left_text, "json")
    right_doc = load_document(right_text, "json")
    result, pairs = diff_with_pairs(left_doc, right_doc)
    assert result.unaligned == (("flags",),)
    assert len(result.changes) > 5_000
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        report = build_report(result, left_doc, right_doc, pairs)
        samples.append(time.perf_counter() - started)
    best = min(samples)
    budget = _REPORT_BUDGET_S * (_CI_FACTOR if os.environ.get("CI") else 1.0)
    print(
        f"\nreport build, 1.5 MB pair: best {best * 1000:.0f} ms of 3, "
        f"{len(result.changes)} changes, {len(report.rows)} rows, "
        f"{len(report.anchors)} anchor bytes, "
        f"{len(pickle.dumps(report))} pickled bytes"
    )
    assert len(report.anchors) <= ANCHOR_LIMIT * 8
    assert best <= budget, samples


@pytest.mark.perf
@pytest.mark.parametrize("reformat", [False, True])
def test_a_compare_job_for_a_1_5_mb_pair_is_within_budget(reformat: bool) -> None:
    left_text, right_text = _large_pair()
    job = _job(left_text, right_text, reformat=reformat)
    samples = []
    for _ in range(2):
        started = time.perf_counter()
        event = pickle.loads(pickle.dumps(run_compare(job)))
        samples.append(time.perf_counter() - started)
    assert isinstance(event, CompareEvent)
    best = min(samples)
    budget = _JOB_BUDGET_S * (_CI_FACTOR if os.environ.get("CI") else 1.0)
    print(f"\ncompare job, 1.5 MB pair, reformat={reformat}: best {best:.2f} s of 2")
    assert best <= budget, samples
