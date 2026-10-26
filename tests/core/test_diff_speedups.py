"""The #129 speed-ups give exactly what the code they replace gave.

``test_diff_identity.py`` pins whole Compare outputs. These tests pin each
shortcut against the slower path it skips: the summary fast path against
the preview walk, the empty-side opcodes against ``SequenceMatcher``, the
surrogate pre-check against the regex walk alone, and the paused garbage
collector against its own on/off state, nested, after an error, and across
two threads.
"""

import gc
import random
import re
import threading
from difflib import SequenceMatcher

import pytest

import galley.core.format as format_module
from galley.core import diff as diff_module
from galley.core.diff import SUMMARY_LIMIT, _opcodes, _preview
from galley.core.errors import InputError
from galley.core.format import _has_lone_surrogate, format_json
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


# Generous hang guards: each wait normally returns at once.
_WAIT_S = 10.0


def test_pause_gc_nests() -> None:
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
        assert not gc.isenabled()
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


def _overlapping_pauses() -> list[tuple[str, bool]]:
    """A starts, B starts, A ends, B ends; returns the GC state after each."""
    seen: list[tuple[str, bool]] = []
    a_started = threading.Event()
    b_started = threading.Event()
    a_may_end = threading.Event()
    a_ended = threading.Event()
    b_may_end = threading.Event()

    @pause_gc
    def held(started: threading.Event, may_end: threading.Event) -> None:
        started.set()
        assert may_end.wait(_WAIT_S)

    def run_a() -> None:
        held(a_started, a_may_end)
        a_ended.set()

    thread_a = threading.Thread(target=run_a)
    thread_b = threading.Thread(target=held, args=(b_started, b_may_end))
    thread_a.start()
    assert a_started.wait(_WAIT_S)
    seen.append(("A started", gc.isenabled()))
    thread_b.start()
    assert b_started.wait(_WAIT_S)
    seen.append(("B started", gc.isenabled()))
    a_may_end.set()
    assert a_ended.wait(_WAIT_S)
    thread_a.join(_WAIT_S)
    seen.append(("A ended", gc.isenabled()))
    b_may_end.set()
    thread_b.join(_WAIT_S)
    assert not thread_a.is_alive() and not thread_b.is_alive()
    seen.append(("B ended", gc.isenabled()))
    return seen


def test_overlapping_pauses_on_two_threads_restore_collection_once() -> None:
    assert gc.isenabled()
    assert _overlapping_pauses() == [
        ("A started", False),
        ("B started", False),
        ("A ended", False),
        ("B ended", True),
    ]


def test_overlapping_pauses_keep_a_disabled_collector_disabled() -> None:
    gc.disable()
    try:
        assert _overlapping_pauses() == [
            ("A started", False),
            ("B started", False),
            ("A ended", False),
            ("B ended", False),
        ]
    finally:
        gc.enable()


# Inputs for the lone-surrogate pre-check: an escaped lone surrogate, a raw
# one, an escaped pair, an escaped backslash before "ud800", and plain JSON.
_SURROGATE_INPUTS = (
    '{"a": "\\ud800"}',
    '{"a": "\\uDC00 x"}',
    '{"a": "\ud800"}',
    '{"a": "\\ud83d\\ude00"}',
    '{"a": "\\\\ud800"}',
    '{"a": [1, "plain", {"b": null}]}',
    '{"a": "\\u00e9"}',
)


def _format_outcome(text: str) -> str:
    try:
        return "output " + format_json(text)
    except InputError as error:
        return f"InputError {error.kind} {error.lineno}:{error.colno} {error.details}"
    except Exception as error:
        return f"{type(error).__name__} {error}"


@pytest.mark.parametrize("text", _SURROGATE_INPUTS)
def test_the_surrogate_pre_check_changes_no_result(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_check = (_has_lone_surrogate(text), _format_outcome(text))
    with monkeypatch.context() as patch:
        # A pattern that always matches makes the pre-check fall through,
        # so the original regex walk decides, as it did before #129.
        patch.setattr(format_module, "_RAW_SURROGATE_RE", re.compile(""))
        without_check = (_has_lone_surrogate(text), _format_outcome(text))
    assert with_check == without_check


def test_the_surrogate_inputs_cover_both_answers() -> None:
    answers = [_has_lone_surrogate(text) for text in _SURROGATE_INPUTS]
    assert answers == [True, True, True, False, False, False, False]
