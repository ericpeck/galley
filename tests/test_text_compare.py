"""Big-text compares fail fast and name the first offset that differs."""

import time

import pytest
from tests.text_compare import BIG_TEXT, assert_same_text, first_difference

# Every line differs after the split, so pytest's own diff of these would be
# quadratic: minutes, not milliseconds.
_LINE = "0123456789abcde\n"
_SIZE = 3_000_000
_SPLIT = 2_000_000
# A hang guard, not a budget: the report takes milliseconds.
_FAST_S = 1.0


def _pair(kind: type) -> tuple[str | bytes, str | bytes, int]:
    """Two 3 MB values, and the offset of their first difference."""
    expected = _LINE * (_SIZE // len(_LINE))
    actual = expected[:_SPLIT] + expected[_SPLIT:].replace("a", "A")
    offset = expected.index("a", _SPLIT)
    if kind is bytes:
        return actual.encode(), expected.encode(), offset
    return actual, expected, offset


@pytest.mark.parametrize("kind", [str, bytes])
def test_assert_same_text_fails_fast_at_the_first_difference(kind: type) -> None:
    actual, expected, offset = _pair(kind)
    started = time.perf_counter()
    with pytest.raises(AssertionError) as caught:
        assert_same_text(actual, expected)
    elapsed = time.perf_counter() - started
    message = str(caught.value)
    assert elapsed < _FAST_S
    assert f"offset {offset:,}" in message
    assert f"{_SIZE:,} vs {_SIZE:,}" in message
    assert "\n" not in message
    assert repr(expected[offset - 80 : offset + 80]) in message
    assert repr(actual[offset - 80 : offset + 80]) in message


@pytest.mark.parametrize("kind", [str, bytes])
def test_a_failing_assert_on_big_text_skips_pytests_diff(kind: type) -> None:
    """The conftest hook answers through a real assert, and difflib never runs."""
    actual, expected, offset = _pair(kind)
    started = time.perf_counter()
    with pytest.raises(AssertionError) as caught:
        assert actual == expected
    elapsed = time.perf_counter() - started
    assert elapsed < _FAST_S
    assert f"first difference at offset {offset:,}" in str(caught.value)


def test_text_up_to_64_kb_keeps_pytests_diff() -> None:
    expected = "x" * BIG_TEXT
    actual = "x" * (BIG_TEXT - 1) + "y"
    with pytest.raises(AssertionError) as caught:
        assert actual == expected
    assert "texts differ" not in str(caught.value)
    with pytest.raises(AssertionError) as caught:
        assert actual + "x" == expected + "x"
    assert "texts differ" in str(caught.value)


@pytest.mark.parametrize(
    ("actual", "expected", "offset"),
    [
        ("abc", "abd", 2),
        ("abc", "xbc", 0),
        ("ab", "abc", 2),
        (b"ab\x00", b"ab\x01", 2),
    ],
)
def test_first_difference(
    actual: str | bytes, expected: str | bytes, offset: int
) -> None:
    assert first_difference(actual, expected) == offset


def test_equal_texts_pass_and_mixed_types_are_named() -> None:
    assert_same_text("same", "same")
    assert_same_text(b"same", b"same")
    with pytest.raises(AssertionError, match="str of length 4 vs bytes of length 4"):
        assert_same_text("same", b"same")
