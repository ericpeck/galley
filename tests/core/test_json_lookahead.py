"""The B6 lookahead: where each array closes (Erik, #195 and #210).

B6 reads a ``"key":`` inside an array as the object's key only when the
array never closes. The lookahead that decides it reads strings and
comments the way the scanner does, records only ``[`` positions, and runs
once per text. All data is generated here and made up.
"""

import json
import random
import tracemalloc
from array import array

import pytest

from galley.core import json_recover
from galley.core.problems import collect_problems

# Pieces the generated inputs are built from: brackets, closed and cut-off
# strings in both quotes, escapes, both comment kinds, line ends, and
# tuples, with junk such as f( that opens none (#212 B1), and a ( where an
# object's key goes, which opens none either (#212 B3).
_PIECES = (
    "[",
    "]",
    "{",
    "}",
    '"a"',
    '"b]"',
    '"q\\"}"',
    '"cut',
    '"bs\\',
    "'x'",
    "']'",
    "'it\\'s'",
    "'open",
    "'",
    '"',
    "\\",
    "// note ]",
    "/* ] */",
    "/* open [",
    "/",
    "*",
    "\n",
    "\r\n",
    "(",
    ")",
    "f(",
    "[(",
    ", (",
    '[{"k": 1, (',
    "}]",
    ", ",
    "1",
    ":",
    " ",
)
_SEED = 195
_INPUTS = 500
# The seeded inputs close 70 tuples with "]" or "}" and put 127 "(" where
# an object's key goes; at least this many of each.
_TUPLES_CLOSED = 40
_KEYS_SKIPPED = 100


def _rows(text: str) -> list[tuple[str, int, int, str, bool]]:
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(text)
    return [
        (item.code, item.lineno, item.colno, item.token, item.uncertain)
        for item in collect_problems(text, "JSON", caught.value)
    ]


def _decode_error(text: str) -> json.JSONDecodeError:
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(text)
    return caught.value


def _line_end(text: str, index: int) -> int:
    while index < len(text) and text[index] not in "\n\r":
        index += 1
    return index


def _string_body_end(text: str, index: int, quote: str) -> int:
    """Where the string body that opens at ``index`` stops.

    A backslash escapes any character but a line end. The body stops at
    ``quote``, at a line end, or at a backslash before a line end.
    """
    cursor = index + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\" and cursor + 1 < len(text) and text[cursor + 1] not in "\n\r":
            cursor += 2
        elif char not in quote + "\\\n\r":
            cursor += 1
        else:
            break
    return cursor


def _skip(text: str, index: int) -> int | None:
    """Past the string or comment at ``index``, or None if none starts there.

    Read the way the scanner reads them: a cut-off ``"`` string stops where
    its body stops, a ``'`` string or ``//`` comment that doesn't close runs
    to the line end, and ``/*`` runs to ``*/`` or the end.
    """
    char = text[index]
    if char == '"' or char == "'":
        cursor = _string_body_end(text, index, char)
        if cursor < len(text) and text[cursor] == char:
            return cursor + 1
        return cursor if char == '"' else _line_end(text, index)
    if text.startswith("//", index):
        return _line_end(text, index)
    if text.startswith("/*", index):
        close = text.find("*/", index + 2)
        return len(text) if close < 0 else close + 2
    return None


def _value_position(text: str, index: int, in_object: bool) -> bool:
    """True when the last non-blank character before ``index`` starts a value.

    That is ``:``, or there is none, or ``[``, ``(`` or ``,`` outside an
    object. A ``(`` there opens a Python tuple. After a comma inside an
    object a key comes next, so a ``(`` there is junk (#212 B3), and so is
    ``f(x``.
    """
    cursor = index - 1
    while cursor >= 0 and text[cursor] in " \t\n\r":
        cursor -= 1
    if cursor < 0 or text[cursor] == ":":
        return True
    return not in_object and text[cursor] in "[(,"


# An open "{" and an open tuple, where an open "[" holds its row.
_OBJECT = -1
_TUPLE = -2


def _reference_closers(
    text: str,
    tuples_closed: list[int] | None = None,
    keys_skipped: list[int] | None = None,
) -> list[tuple[int, int]]:
    """A slow walk, one character at a time, of what the lookahead records.

    Each ``[``, in text order, with the position of the ``]`` or ``}`` that
    closes it, or -1. A ``(`` in value position opens a tuple, which only
    ``)`` or a closer of its own closes. ``)`` closes only a tuple, and
    ``]`` or ``}`` closes the innermost open bracket of any kind (#212 B1).
    Each tuple a ``]`` or ``}`` closes is added to ``tuples_closed``, and
    each ``(`` after a comma inside an object to ``keys_skipped``.
    """
    found: list[list[int]] = []
    opened: list[int] = []
    index = 0
    while index < len(text):
        past = _skip(text, index)
        if past is not None:
            index = past
            continue
        char = text[index]
        if char == "[":
            opened.append(len(found))
            found.append([index, -1])
        elif char == "{":
            opened.append(_OBJECT)
        elif char == "(":
            in_object = bool(opened) and opened[-1] == _OBJECT
            if _value_position(text, index, in_object):
                opened.append(_TUPLE)
            elif (
                in_object
                and keys_skipped is not None
                and _value_position(text, index, in_object=False)
            ):
                keys_skipped.append(index)
        elif char == ")":
            if opened and opened[-1] == _TUPLE:
                opened.pop()
        elif char in "]}" and opened:
            row = opened.pop()
            if row >= 0:
                found[row][1] = index
            elif row == _TUPLE and tuples_closed is not None:
                tuples_closed.append(index)
        index += 1
    return [(start, end) for start, end in found]


def _generated() -> list[str]:
    pick = random.Random(_SEED)
    return [
        "".join(pick.choice(_PIECES) for _ in range(pick.randint(1, 30)))
        for _ in range(_INPUTS)
    ]


def test_the_reference_walker_reads_strings_and_comments() -> None:
    assert _reference_closers("[\"]\", '[' // [\n/* ] */ ]") == [(0, 23)]
    assert _reference_closers("[ 'cut ]\n]") == [(0, 9)]
    assert _reference_closers('[ "cut ]\n]') == [(0, 9)]
    assert _reference_closers("[{]") == [(0, -1)]
    # A tuple closed with "]" doesn't close the array around it (#195 B1).
    assert _reference_closers("[(1, 2], 3]") == [(0, 10)]
    assert _reference_closers("[f(x], 3]") == [(0, 4)]
    assert _reference_closers("[(1)]") == [(0, 4)]
    # A "(" where an object's key goes opens nothing (#212 B3).
    assert _reference_closers('[{"b": 1, (x}, 2]') == [(0, 16)]
    assert _reference_closers('[{"b": (1)}, 2]') == [(0, 14)]


def test_recorded_closers_match_a_slow_walk_of_the_text() -> None:
    """About 500 seeded inputs: the recorded closers equal the slow walk's."""
    tuples_closed: list[int] = []
    keys_skipped: list[int] = []
    for text in _generated():
        openers, closers = json_recover.array_closers(text)
        expected = _reference_closers(text, tuples_closed, keys_skipped)
        assert list(zip(openers, closers, strict=True)) == expected, repr(text)
    # The inputs include tuples closed with "]" or "}" (#212 B1), and a "("
    # where an object's key goes (#212 B3).
    assert len(tuples_closed) >= _TUPLES_CLOSED
    assert len(keys_skipped) >= _KEYS_SKIPPED


# Erik's two examples (#210): a "]" inside a string or a comment doesn't
# close the array, so B6 applies at the first colon and no stale
# missing-comma row follows.
@pytest.mark.parametrize(
    ("text", "rows"),
    [
        (
            '{"a": ["x" : 1, \']\', "y" : 2}',
            [
                ("missing_close", 1, 7, "[", False),
                ("single_quotes", 1, 17, "", False),
            ],
        ),
        (
            '{"a": ["x" : 1, // ]\n"y" : 2}',
            [
                ("missing_close", 1, 7, "[", False),
                ("json_comment", 1, 17, "", False),
            ],
        ),
    ],
    ids=["single quotes", "comment"],
)
def test_a_closer_in_a_string_or_comment_does_not_close_the_array(
    text: str, rows: list[tuple[str, int, int, str, bool]]
) -> None:
    assert _rows(text) == rows


# Erik's B1 (#212): the scanner closes a tuple with "]", so that "]"
# doesn't close the array around it. B6 applies at the key's colon.
@pytest.mark.parametrize(
    ("text", "rows"),
    [
        (
            '{"a": [(1, 2], "x": 1}',
            [
                ("missing_close", 1, 7, "[", False),
                ("python_tuple", 1, 8, "(", False),
            ],
        ),
        (
            '{"a": [1, "b": (2]}',
            [
                ("missing_close", 1, 7, "[", False),
                ("python_tuple", 1, 16, "(", False),
            ],
        ),
    ],
    ids=["tuple in the array", "tuple after the key"],
)
def test_a_tuple_closed_with_a_square_bracket_does_not_close_the_array(
    text: str, rows: list[tuple[str, int, int, str, bool]]
) -> None:
    assert _rows(text) == rows


# Erik's B3 (#212): a "(" where an object's key goes is junk to the
# scanner, so it opens no tuple and the "}" closes the object. The rows
# are main's: the array closes later, so B6 doesn't apply.
@pytest.mark.parametrize(
    ("text", "rows"),
    [
        (
            '{"a": [{"b": 1, (x}, "k": 2]}',
            [
                ("trailing_comma", 1, 15, "", False),
                ("missing_comma", 1, 25, "", False),
            ],
        ),
        (
            '{"a": [{"b": 1, (}, "k": 2]}',
            [
                ("trailing_comma", 1, 15, "", False),
                ("missing_comma", 1, 24, "", False),
            ],
        ),
    ],
    ids=["junk after the paren", "paren alone"],
)
def test_a_paren_in_key_position_opens_no_tuple(
    text: str, rows: list[tuple[str, int, int, str, bool]]
) -> None:
    assert _rows(text) == rows


# --- Memory and speed (perf) --------------------------------------------------

# About 4 MB of "[], {}, " pieces in one array, after one B6 colon.
_BRACKET_PIECES = 500_000


@pytest.mark.perf
def test_the_lookahead_keeps_memory_under_four_times_the_text(perf_budget) -> None:
    """Peak extra memory stays at most 4 times the text's size (Erik, #195).

    A dict of every bracket the lookahead passed peaked near 20 times.
    """
    text = '{"a": ["x" : 1, ' + "[], {}, " * _BRACKET_PIECES + "0]}"
    assert 3_900_000 < len(text) < 4_100_000
    error = _decode_error(text)

    tracemalloc.start()
    try:
        found = collect_problems(text, "JSON", error)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert found[0].code == "missing_comma"
    assert peak <= 4 * len(text), f"peak {peak / 1e6:.1f} MB"

    # CI medians at a87d5cf: 2.3 s on macOS and 2.7 s on Windows, so the
    # nightly strict run keeps about 1.9 times headroom.
    perf_budget.within(
        5.0,
        lambda: collect_problems(text, "JSON", error),
        fixture="4 MB of [], {} pieces after one B6 colon",
    )


def _nested(depth: int, closed: bool) -> str:
    level = '{"a": ["x" : 1, ' + "0, " * 300
    return level * depth + ("0]}" * depth if closed else "")


@pytest.mark.perf
@pytest.mark.parametrize("closed", [True, False], ids=["closed", "unclosed"])
def test_900_nested_arrays_are_read_once(perf_budget, closed: bool) -> None:
    """Each level has a B6 colon; no level reads the rest of the text again."""
    text = _nested(900, closed)
    assert 800_000 < len(text) < 900_000
    error = _decode_error(text)
    perf_budget.within(
        1.0,
        lambda: collect_problems(text, "JSON", error),
        fixture=f"900 nested arrays, {'closed' if closed else 'unclosed'}",
    )


@pytest.mark.perf
def test_sibling_arrays_that_never_close_are_read_once(perf_budget) -> None:
    """Each array's lookahead would read to the end of the object (#210)."""
    text = "{" + '"a": ["x" : 1, ' * 15_000 + "0}"
    assert 200_000 < len(text) < 250_000
    error = _decode_error(text)
    perf_budget.within(
        0.25,
        lambda: collect_problems(text, "JSON", error),
        fixture="15 000 sibling arrays that never close",
    )


@pytest.mark.perf
def test_an_array_missing_from_the_table_is_read_once(perf_budget, monkeypatch) -> None:
    """A "[" the table doesn't have falls back to its own lookahead (Erik).

    The fallback runs once for the array, not once per B6 colon, or the
    #210 rescan comes back. The empty table puts every array outside it.
    """
    monkeypatch.setattr(
        json_recover,
        "array_closers",
        lambda _text: (array("l"), array("l")),
        raising=False,
    )
    text = '{"a": [' + '"x" : 1, ' * 15_000 + "0]}"
    assert 120_000 < len(text) < 150_000
    error = _decode_error(text)
    perf_budget.within(
        0.25,
        lambda: collect_problems(text, "JSON", error),
        fixture="15 000 B6 colons in an array missing from the table",
    )
