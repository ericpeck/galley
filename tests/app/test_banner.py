"""Banner wording for the three located input errors.

Core reports a kind and structured details. These tests lock the sentences
Bill approved, including the depth number taken from ``MAX_DEPTH``.
"""

from pathlib import Path

from galley.app import banner
from galley.app.banner import banner_text
from galley.core import MAX_DEPTH
from galley.core.errors import InputError
from galley.core.format import format_json, format_yaml

BANNER_PATH = Path(banner.__file__)


def test_invalid_utf8_banner_names_line_and_column() -> None:
    error = InputError(
        "invalid_utf8",
        lineno=1,
        colno=8,
        details={"offset": 7},
    )
    assert banner_text(error) == (
        "This file isn't valid UTF-8, so Galley can't read it. "
        "The first bad byte is at line 1, column 8. "
        "Save the file as UTF-8 and open it again."
    )


def test_invalid_utf8_banner_falls_back_to_the_grouped_byte_offset() -> None:
    error = InputError(
        "invalid_utf8",
        lineno=None,
        colno=None,
        details={"offset": 1234},
    )
    assert banner_text(error) == (
        "This file isn't valid UTF-8, so Galley can't read it. "
        "The first bad byte is at byte 1,234. "
        "Save the file as UTF-8 and open it again."
    )


def test_too_deep_banner_reads_its_number_from_max_depth() -> None:
    error = InputError(
        "too_deep",
        lineno=65,
        colno=1,
        details={"depth": MAX_DEPTH + 1},
    )
    assert banner_text(error) == (
        "This document is nested more than "
        f"{MAX_DEPTH} levels deep, which is more than Galley can "
        "format safely. The limit was reached at line 65."
    )
    source = BANNER_PATH.read_text(encoding="utf-8")
    assert "MAX_DEPTH" in source
    assert "512" not in source
    assert "64" not in source


def test_bad_escape_banner_shows_the_escape_from_details() -> None:
    error = InputError(
        "bad_escape",
        lineno=1,
        colno=8,
        details={"escape": "\\uD800"},
    )
    assert banner_text(error) == (
        "Line 1, column 8 has an invalid character escape (\\uD800) "
        "that can't be saved as UTF-8."
    )


def test_banner_text_follows_errors_raised_by_the_formatters() -> None:
    try:
        format_json(b'{"a": "\xff"}')
    except InputError as error:
        utf8 = error
    else:
        raise AssertionError("invalid UTF-8 was accepted")
    assert banner_text(utf8) == (
        "This file isn't valid UTF-8, so Galley can't read it. "
        "The first bad byte is at line 1, column 8. "
        "Save the file as UTF-8 and open it again."
    )

    try:
        format_yaml(_over_deep_yaml())
    except InputError as error:
        deep = error
    else:
        raise AssertionError("over-deep YAML was accepted")
    assert banner_text(deep) == (
        "This document is nested more than "
        f"{MAX_DEPTH} levels deep, which is more than Galley can "
        f"format safely. The limit was reached at line {deep.lineno}."
    )

    try:
        format_json('{"a": "\\ud800"}')
    except InputError as error:
        escape = error
    else:
        raise AssertionError("lone surrogate was accepted")
    assert banner_text(escape) == (
        "Line 1, column 8 has an invalid character escape (\\ud800) "
        "that can't be saved as UTF-8."
    )


def _over_deep_yaml() -> str:
    depth = MAX_DEPTH + 1
    lines = [f"{'  ' * level}k:" for level in range(depth)]
    lines.append(f"{'  ' * depth}1")
    return "\n".join(lines) + "\n"
