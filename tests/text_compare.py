"""Compare long texts without pytest's diff.

pytest explains a failed ``==`` on two strings with a difflib diff. difflib
is quadratic when many lines differ: two 2.4 MB documents ran for more than
5 minutes on the macOS runner. These report both lengths, the first offset
that differs, and a short excerpt on each side of it instead.

``tests/conftest.py`` uses the same report for any ``==`` on a str or bytes
over :data:`BIG_TEXT`.
"""

# The conftest hook reports a str or bytes longer than this instead of
# diffing it.
BIG_TEXT = 64 * 1024
# How much of each value the report shows on each side of the difference.
CONTEXT = 80


def is_big_text(value: object) -> bool:
    return isinstance(value, str | bytes) and len(value) > BIG_TEXT


def first_difference(actual: str | bytes, expected: str | bytes) -> int:
    """The offset of the first unit that differs, or the shorter length.

    A binary search over slice compares, which run in C, so 3 MB takes
    milliseconds.
    """
    low, high = 0, min(len(actual), len(expected))
    while low < high:
        middle = (low + high + 1) // 2
        if actual[low:middle] == expected[low:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def describe_difference(
    actual: str | bytes, expected: str | bytes, *, context: int = CONTEXT
) -> str:
    """One line: both lengths, the first differing offset, and an excerpt."""
    if type(actual) is not type(expected):
        return (
            f"texts differ: {type(actual).__name__} of length {len(actual):,} "
            f"vs {type(expected).__name__} of length {len(expected):,}"
        )
    unit = "bytes" if isinstance(actual, bytes) else "characters"
    offset = first_difference(actual, expected)
    start = max(offset - context, 0)
    end = offset + context
    return (
        f"texts differ: {len(actual):,} vs {len(expected):,} {unit}, first "
        f"difference at offset {offset:,}: actual {actual[start:end]!r}, "
        f"expected {expected[start:end]!r}"
    )


def assert_same_text(
    actual: str | bytes, expected: str | bytes, *, context: int = CONTEXT
) -> None:
    """Fail at once with a one-line report when the texts differ."""
    __tracebackhide__ = True
    if type(actual) is type(expected) and actual == expected:
        return
    raise AssertionError(describe_difference(actual, expected, context=context))
