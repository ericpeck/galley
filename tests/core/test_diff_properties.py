"""Property checks for the structural diff (#65 PR 1).

The generators stay small (few leaves, a three-letter key alphabet) so
the two sides share keys often and the suite stays fast on Windows CI.
"""

import json
from collections import Counter
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from galley.core.diff import Change, Doc, diff, load_document

_SETTINGS = settings(max_examples=150, deadline=None)

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


_PAIRS = st.one_of(st.tuples(_VALUES, _VALUES), _moved_pairs())


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
        return replace(swapped, kind="removed")
    if change.kind == "removed":
        return replace(swapped, kind="added")
    if change.kind == "moved":
        assert change.moved_to is not None and change.moved_to_text is not None
        return replace(
            swapped,
            path=change.moved_to,
            path_text=change.moved_to_text,
            moved_to=change.path,
            moved_to_text=change.path_text,
        )
    return swapped


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
    assert diff(_load(value), _load(value)) == []


@_SETTINGS
@given(_VALUES)
def test_a_value_equals_itself_without_spans(value: object) -> None:
    assert diff(Doc(value, "json"), Doc(value, "json")) == []


@_SETTINGS
@given(_PAIRS)
def test_swapping_sides_mirrors_every_change(pair: tuple[object, object]) -> None:
    left, right = pair
    forward = diff(_load(left), _load(right))
    backward = diff(_load(right), _load(left))
    assert Counter(_mirror(change) for change in forward) == Counter(backward)


@_SETTINGS
@given(_PAIRS)
def test_paths_are_unique_per_kind(pair: tuple[object, object]) -> None:
    left, right = pair
    changes = diff(_load(left), _load(right))
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
    for change in diff(_load(left), _load(right)):
        if change.kind == "added":
            assert change.path in right_paths
            assert change.right_span is not None and change.left_span is None
        elif change.kind == "removed":
            assert change.path in left_paths
            assert change.left_span is not None and change.right_span is None
        elif change.kind == "moved":
            assert change.path in left_paths and change.moved_to in right_paths
            assert change.moved_to is not None
            assert change.path[:-1] != change.moved_to[:-1]
        else:
            assert change.path in left_paths and change.path in right_paths


@_SETTINGS
@given(_PAIRS)
def test_left_sided_changes_come_in_left_order(pair: tuple[object, object]) -> None:
    left, right = pair
    starts = [
        (change.left_span.line, change.left_span.column)
        for change in diff(_load(left), _load(right))
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
        for c in diff(_load(left), _load(right))
        if c.kind == "moved" and c.path[0] in {"from", "to"}
    ]
    assert len(moved) == 1
    target = moved[0].moved_to
    assert target is not None
    assert {moved[0].path[0], target[0]} == {"from", "to"}
