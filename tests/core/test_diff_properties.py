"""Property checks for the structural diff (#65 PR 1).

The generators stay small (few leaves, a three-letter key alphabet) so
the two sides share keys often and the suite stays fast on Windows CI.
"""

import json
import os
from collections import Counter
from dataclasses import replace

from hypothesis import example, given, settings
from hypothesis import strategies as st

from galley.core.diff import Change, Doc, _summary, diff, load_document
from galley.core.keypath import render_path

# CI runs the same examples every time (GitHub Actions sets CI), so a red
# build is reproducible. Local runs stay random and keep exploring.
_SETTINGS = settings(
    max_examples=150, deadline=None, derandomize=bool(os.environ.get("CI"))
)

# SequenceMatcher matches {} across every other item here and reports six
# changes for five items. That broke an earlier max(len) bound on macOS CI
# at f8ce29b; the round trip and the len(left) + len(right) bound hold.
_CROSSING_MATCH = ({"items": [{}, 1, 0, 1, 0]}, {"items": [1, 1, {}]})

_KEYS = st.sampled_from(["a", "b", "c", "a.b", "0"])
_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-3, max_value=3),
    st.sampled_from([0.0, 1.0, 1.5, -2.0, float("nan"), float("inf")]),
    st.sampled_from(["", "x", "1", "true", "null"]),
)
_VALUES = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(_KEYS, children, max_size=4),
    ),
    max_leaves=16,
)


@st.composite
def _moved_pairs(draw: st.DrawFn) -> tuple[object, object]:
    """Two documents where one subtree changes parent, plus other edits."""
    # "id" is outside the key alphabet, so no other value equals the subtree.
    subtree = {"id": "moved"} | draw(st.dictionaries(_KEYS, _VALUES, max_size=3))
    left_rest = draw(st.dictionaries(_KEYS, _VALUES, max_size=2))
    right_rest = draw(st.dictionaries(_KEYS, _VALUES, max_size=2))
    key = draw(_KEYS)
    left = {"from": {key: subtree}, "to": {}, "rest": left_rest}
    right = {"from": {}, "to": {key: subtree}, "rest": right_rest}
    if draw(st.booleans()):
        return right, left
    return left, right


@st.composite
def _array_pairs(draw: st.DrawFn) -> tuple[object, object]:
    """Two arrays of repeated small items, the right an edit of the left."""
    item = st.sampled_from([0, 1, True, "a", {}, {"k": 1}, {"k": 2}, [1]])
    left = draw(st.lists(item, max_size=12))
    right = list(left)
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        action = draw(st.sampled_from(["insert", "delete", "replace"]))
        if action == "insert" or not right:
            right.insert(draw(st.integers(0, len(right))), draw(item))
        elif action == "delete":
            del right[draw(st.integers(0, len(right) - 1))]
        else:
            right[draw(st.integers(0, len(right) - 1))] = draw(item)
    return {"items": left}, {"items": right}


_PAIRS = st.one_of(st.tuples(_VALUES, _VALUES), _moved_pairs(), _array_pairs())


def _load(value: object) -> Doc:
    return load_document(json.dumps(value, indent=2), "json")


def _mirror(change: Change) -> Change:
    """The change ``diff(right, left)`` reports for ``change``."""
    swapped = replace(
        change,
        old=change.new,
        new=change.old,
        left_span=change.right_span,
        right_span=change.left_span,
    )
    if change.kind == "added":
        return replace(swapped, kind="removed", right_path=None)
    if change.kind == "removed":
        return replace(swapped, kind="added", right_path=change.path)
    assert change.right_path is not None
    swapped = replace(
        swapped,
        path=change.right_path,
        path_text=render_path(list(change.right_path)),
        right_path=change.path,
    )
    if change.kind == "moved":
        return replace(swapped, moved_to=change.path, moved_to_text=change.path_text)
    return swapped


def _same(left: object, right: object) -> bool:
    """Equality under the diff's value rules: 1 == 1.0, true != 1, NaN == NaN."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, float) and isinstance(right, float):
        if left != left and right != right:
            return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _at(value: object, path: tuple[str | int, ...]) -> object:
    for segment in path:
        value = value[segment]
    return value


def _apply(left: object, right: object, changes: list[Change]) -> object:
    """Rebuild the right document from the left one and ``changes``.

    A Change carries summaries, which are cut at 80 characters, so the new
    values of Added, Changed, and Moved are read from ``right`` at their
    right path. That lookup is test-only. Everything else comes from the
    changes: which left values go (Removed, the source of a Moved), which
    are replaced (Changed), and where right-only values go in each parent
    (Added, the target of a Moved). Kept array items keep their order, so
    they fill the right indexes no insertion takes.
    """
    gone = {c.path for c in changes if c.kind in ("removed", "moved")}
    replaced = {c.path: c.right_path for c in changes if c.kind == "changed"}
    inserted: dict[tuple[str | int, ...], list[str | int]] = {}
    for change in changes:
        target = change.right_path if change.kind in ("added", "moved") else None
        if target is not None:
            inserted.setdefault(target[:-1], []).append(target[-1])
    for change in changes:
        if change.kind in ("removed", "changed", "moved"):
            assert change.old == _summary(_at(left, change.path))
        if change.right_path is not None:
            assert change.new == _summary(_at(right, change.right_path))

    def rebuild(
        node: object,
        left_path: tuple[str | int, ...],
        right_path: tuple[str | int, ...],
    ) -> object:
        if left_path in replaced:
            assert replaced[left_path] == right_path
            return _at(right, right_path)
        extra = inserted.get(right_path, [])
        if isinstance(node, dict):
            result = {
                key: rebuild(child, (*left_path, key), (*right_path, key))
                for key, child in node.items()
                if (*left_path, key) not in gone
            }
            for key in extra:
                assert isinstance(key, str) and key not in result
                result[key] = _at(right, (*right_path, key))
            return result
        if isinstance(node, list):
            kept = [
                (index, child)
                for index, child in enumerate(node)
                if (*left_path, index) not in gone
            ]
            slots = set(extra)
            size = len(kept) + len(slots)
            assert all(isinstance(slot, int) and slot < size for slot in slots)
            free = iter(index for index in range(size) if index not in slots)
            result: list[object] = [None] * size
            for index, child in kept:
                position = next(free)
                result[position] = rebuild(
                    child, (*left_path, index), (*right_path, position)
                )
            for slot in slots:
                result[slot] = _at(right, (*right_path, slot))
            return result
        assert not extra
        return node

    return rebuild(left, (), ())


def _paths(value: object) -> set[tuple[str | int, ...]]:
    found: set[tuple[str | int, ...]] = set()
    stack: list[tuple[object, tuple[str | int, ...]]] = [(value, ())]
    while stack:
        node, path = stack.pop()
        found.add(path)
        if isinstance(node, dict):
            stack.extend((child, (*path, key)) for key, child in node.items())
        elif isinstance(node, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(node))
    return found


@_SETTINGS
@given(_VALUES)
def test_a_document_equals_itself(value: object) -> None:
    assert diff(_load(value), _load(value)).changes == []


@_SETTINGS
@given(_VALUES)
def test_a_value_equals_itself_without_spans(value: object) -> None:
    assert diff(Doc(value, "json"), Doc(value, "json")).changes == []


@_SETTINGS
@given(_PAIRS)
@example(_CROSSING_MATCH)
def test_swapping_sides_mirrors_every_change(pair: tuple[object, object]) -> None:
    left, right = pair
    forward = diff(_load(left), _load(right)).changes
    backward = diff(_load(right), _load(left)).changes
    assert Counter(_mirror(change) for change in forward) == Counter(backward)


@_SETTINGS
@given(_PAIRS)
def test_paths_are_unique_per_kind(pair: tuple[object, object]) -> None:
    left, right = pair
    changes = diff(_load(left), _load(right)).changes
    keys = [(change.kind, change.path) for change in changes]
    assert len(keys) == len(set(keys))
    targets = [change.moved_to for change in changes if change.kind == "moved"]
    assert len(targets) == len(set(targets))


@_SETTINGS
@given(_PAIRS)
def test_every_change_points_at_a_real_path(pair: tuple[object, object]) -> None:
    left, right = pair
    left_paths = _paths(left)
    right_paths = _paths(right)
    for change in diff(_load(left), _load(right)).changes:
        if change.kind == "added":
            assert change.path in right_paths and change.right_path == change.path
            assert change.right_span is not None and change.left_span is None
        elif change.kind == "removed":
            assert change.path in left_paths and change.right_path is None
            assert change.left_span is not None and change.right_span is None
        else:
            assert change.path in left_paths and change.right_path in right_paths
            assert change.left_span is not None and change.right_span is not None
            if change.kind == "moved":
                assert change.moved_to == change.right_path


@_SETTINGS
@given(_PAIRS)
def test_left_sided_changes_come_in_left_order(pair: tuple[object, object]) -> None:
    left, right = pair
    starts = [
        (change.left_span.line, change.left_span.column)
        for change in diff(_load(left), _load(right)).changes
        if change.left_span is not None
    ]
    assert starts == sorted(starts)


@_SETTINGS
@given(_VALUES)
def test_json_spans_cover_every_path(value: object) -> None:
    text = json.dumps(value, indent=2)
    assert set(load_document(text, "json").spans) == _paths(json.loads(text))
    compact = json.dumps(value, separators=(",", ":"))
    assert set(load_document(compact, "json").spans) == _paths(json.loads(compact))


@_SETTINGS
@given(_moved_pairs())
def test_a_unique_subtree_under_a_new_parent_is_moved(
    pair: tuple[object, object],
) -> None:
    left, right = pair
    # Other edits in "rest" may also move a value, so count only this one.
    moved = [
        c
        for c in diff(_load(left), _load(right)).changes
        if c.kind == "moved" and c.path[0] in {"from", "to"}
    ]
    assert len(moved) == 1
    target = moved[0].moved_to
    assert target is not None
    assert {moved[0].path[0], target[0]} == {"from", "to"}


@_SETTINGS
@given(_array_pairs())
@example(_CROSSING_MATCH)
def test_aligned_arrays_stay_within_both_lengths(pair: tuple[object, object]) -> None:
    left, right = pair
    result = diff(_load(left), _load(right))
    assert result.aligned
    assert isinstance(left, dict) and isinstance(right, dict)
    left_items = left["items"]
    right_items = right["items"]
    # Each item is either paired, costing at most one change for these
    # small items, or unpaired, costing one Added or Removed.
    assert len(result.changes) <= len(left_items) + len(right_items)
    assert (result.changes == []) == _same(left_items, right_items)


@_SETTINGS
@given(_PAIRS)
@example(_CROSSING_MATCH)
def test_applying_the_changes_rebuilds_the_right_document(
    pair: tuple[object, object],
) -> None:
    left, right = pair
    changes = diff(_load(left), _load(right)).changes
    assert _same(_apply(left, right, changes), right)
