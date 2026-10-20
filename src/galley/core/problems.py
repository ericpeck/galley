"""Structured syntax problems.

The codes here are stable ids. The sentences a person reads are chosen in
``galley.app``. A YAML indent scan reports every leading tab at once.
Other parsers still stop at the first problem, so those stay a single code.
"""

import json
from dataclasses import dataclass
from typing import Literal

from ruamel.yaml.error import YAMLError

ProblemCode = Literal[
    "missing_comma",
    "unclosed_quote",
    "trailing_comma",
    "unexpected_close",
    "incomplete",
    "json_other",
    "yaml_tab",
    "yaml_incomplete",
    "yaml_duplicate",
    "yaml_colon",
    "yaml_other",
]


@dataclass(frozen=True)
class Problem:
    """One located syntax problem. ``lineno`` is 1-based."""

    code: ProblemCode
    lineno: int
    token: str = ""


def collect_problems(
    text: str,
    source: str,
    exc: BaseException | None = None,
) -> tuple[Problem, ...]:
    """Return problems to show before, or instead of, a raw parser message.

    YAML leading tabs are collected up front and the parser is not asked
    to continue. Any other failure contributes at most one problem.
    """
    if source.strip().casefold() == "yaml":
        tabs = tuple(Problem("yaml_tab", line) for line in leading_tab_lines(text))
        if tabs:
            return tabs
    if exc is None:
        return ()
    found = classify_failure(exc, text)
    if found is None:
        return ()
    return (found,)


def leading_tab_lines(text: str) -> list[int]:
    """Return 1-based lines whose indent contains a tab.

    A tab after the first non-space character is content, not indent.
    """
    found: list[int] = []
    for index, line in enumerate(_lines(text), start=1):
        if _indent_has_tab(line):
            found.append(index)
    return found


def classify_failure(exc: BaseException, text: str) -> Problem | None:
    """Map a parser exception to one problem, or ``None`` when it is not syntax."""
    if isinstance(exc, json.JSONDecodeError):
        return _classify_json(exc, text)
    if isinstance(exc, YAMLError):
        return _classify_yaml(exc, text)
    return None


def _classify_json(exc: json.JSONDecodeError, text: str) -> Problem:
    message = exc.msg
    if message.startswith("Unterminated string"):
        return Problem("unclosed_quote", exc.lineno)
    if message.startswith("Invalid control character") and _char_at(text, exc.pos) in {
        "\n",
        "\r",
    }:
        return Problem("unclosed_quote", exc.lineno)
    if message == "Expecting ',' delimiter":
        if _at_end(text, exc.pos):
            return Problem("incomplete", _end_line(text, exc.lineno))
        return Problem("missing_comma", _comma_line(text, exc.lineno, exc.colno))
    if _at_end(text, exc.pos):
        return Problem("incomplete", _end_line(text, exc.lineno))
    comma_at = _comma_before(text, exc.pos)
    if comma_at is not None:
        return Problem("trailing_comma", _line_of_index(text, comma_at))
    char = _char_at(text, exc.pos)
    if char in {"}", "]"}:
        return Problem("unexpected_close", exc.lineno, char)
    return Problem("json_other", max(exc.lineno, 1))


def _classify_yaml(exc: YAMLError, text: str) -> Problem:
    problem = getattr(exc, "problem", None)
    detail = problem.casefold() if isinstance(problem, str) else ""
    line, column = _yaml_location(exc)
    if "cannot start any token" in detail and (
        "tab" in detail or _indent_has_tab(_line_text(text, line))
    ):
        return Problem("yaml_tab", line)
    if "duplicate key" in detail:
        return Problem("yaml_duplicate", line)
    if _expects_delimiter(detail) and _at_line_start(text, line, column):
        line = _previous_nonblank(text, line)
    if "unexpected end of stream" in detail:
        return Problem("yaml_incomplete", line)
    if "expected ':'" in detail:
        return Problem("yaml_colon", line)
    return Problem("yaml_other", line)


def _expects_delimiter(detail: str) -> bool:
    """True when the parser stopped because a delimiter was still missing."""
    if "duplicate key" in detail:
        return False
    return (
        "unexpected end of stream" in detail
        or "stream end" in detail
        or "expected ':'" in detail
        or "expected ','" in detail
        or "expected ']'" in detail
        or "expected '}'" in detail
        or "did not find expected" in detail
    )


def _yaml_location(exc: YAMLError) -> tuple[int, int]:
    mark = getattr(exc, "problem_mark", None)
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        return line + 1, column + 1
    return 1, 1


def _comma_line(text: str, lineno: int, colno: int) -> int:
    """Point a missing comma at the previous line when the parser is at its start."""
    if _at_line_start(text, lineno, colno):
        return _previous_nonblank(text, lineno)
    return max(lineno, 1)


def _end_line(text: str, lineno: int) -> int:
    if _line_text(text, lineno).strip() == "":
        return _previous_nonblank(text, lineno)
    return max(lineno, 1)


def _at_end(text: str, pos: int) -> bool:
    return pos >= len(text)


def _char_at(text: str, pos: int) -> str:
    if pos < 0 or pos >= len(text):
        return ""
    return text[pos]


def _comma_before(text: str, pos: int) -> int | None:
    """Return a comma index when only whitespace sits between it and ``pos``."""
    index = pos - 1
    while index >= 0 and text[index] in " \t\r\n":
        index -= 1
    if index >= 0 and text[index] == ",":
        return index
    return None


def _at_line_start(text: str, lineno: int, colno: int) -> bool:
    prefix = _line_text(text, lineno)[: max(colno - 1, 0)]
    return prefix.strip() == ""


def _previous_nonblank(text: str, lineno: int) -> int:
    lines = _lines(text)
    index = min(max(lineno, 1), len(lines) + 1) - 2
    while index >= 0:
        if lines[index].strip() != "":
            return index + 1
        index -= 1
    return max(lineno, 1)


def _line_of_index(text: str, index: int) -> int:
    return text.count("\n", 0, max(index, 0)) + 1


def _line_text(text: str, lineno: int) -> str:
    lines = _lines(text)
    if lineno < 1 or lineno > len(lines):
        return ""
    return lines[lineno - 1]


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _indent_has_tab(line: str) -> bool:
    for char in line:
        if char == "\t":
            return True
        if char not in " \r":
            return False
    return False
