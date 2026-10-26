"""The #129 speed-ups give exactly what the code they replace gave.

``test_diff_identity.py`` pins whole Compare outputs. These tests pin each
shortcut against the slower path it skips: the summary fast path against
the preview walk, the empty-side opcodes against ``SequenceMatcher``, and
the paused garbage collector against its own on/off state.
"""

import gc
import random
from difflib import SequenceMatcher

import pytest

from galley.core import diff as diff_module
from galley.core.diff import SUMMARY_LIMIT, _opcodes, _preview
from galley.core.gc_pause import pause_gc

_POOL = (
    None,
    True,
    False,
    0,
    -7,
    10**30,
    1.0,
    -0.0,
    1e300,
    float("inf"),
    float("-inf"),
    float("nan"),
    "",
    "é",
    'q"\\',
    "\n\t",
    "\ud800",
    "😀",
)


def _flat(rng: random.Random) -> object:
    def item() -> object:
        if rng.random() < 0.2:
            return "x" * rng.randint(0, 120)
        return rng.choice(_POOL)

    size = rng.randint(0, 40)
    if rng.random() < 0.5:
        return [item() for _ in range(size)]
    keys = ("a", "名", 'k"', "")
    return {rng.choice(keys) + "x" * rng.randint(0, 90): item() for _ in range(size)}


def _walked(value: object, limit: int, monkeypatch: pytest.MonkeyPatch) -> str:
    with monkeypatch.context() as patch:
        patch.setattr(diff_module, "_is_small_and_flat", lambda value, limit: False)
        return _preview(value, limit)


@pytest.mark.parametrize("limit", [SUMMARY_LIMIT, 1, 5, 200])
def test_the_summary_fast_path_writes_what_the_walk_writes(
    limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(limit)
    for _ in range(2_000):
        value = _flat(rng)
        assert _preview(value, limit) == _walked(value, limit, monkeypatch)


@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        [(1, 2)],
        {1: "a"},
        [10**5000],
        {"a": [1]},
        [b"x"],
        list(range(40)),
    ],
)
def test_values_the_fast_path_does_not_take_still_preview_the_same(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _preview(value, SUMMARY_LIMIT) == _walked(value, SUMMARY_LIMIT, monkeypatch)


@pytest.mark.parametrize(
    ("left", "right"), [([], []), ([], [1, 2]), ([3], []), ([1, 2, 3], [])]
)
def test_empty_side_opcodes_match_sequence_matcher(
    left: list[int], right: list[int]
) -> None:
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    assert _opcodes(left, right) == matcher.get_opcodes()


def test_pause_gc_turns_collection_back_on() -> None:
    seen: list[bool] = []

    @pause_gc
    def inner() -> None:
        seen.append(gc.isenabled())

    @pause_gc
    def outer() -> None:
        inner()
        seen.append(gc.isenabled())

    assert gc.isenabled()
    outer()
    assert seen == [False, False]
    assert gc.isenabled()


def test_pause_gc_turns_collection_back_on_after_an_error() -> None:
    @pause_gc
    def fails() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        fails()
    assert gc.isenabled()


def test_pause_gc_leaves_a_disabled_collector_disabled() -> None:
    gc.disable()
    try:
        pause_gc(lambda: None)()
        assert not gc.isenabled()
    finally:
        gc.enable()
