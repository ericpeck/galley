"""Structured syntax problems.

The codes here are stable ids. The sentences a person reads are chosen in
``galley.app``. A YAML indent scan reports every leading tab at once, and
a JSON document reports every repeated key at once. Other parsers still
stop at the first problem, so those stay a single code.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import KeyToken, ScalarToken

from galley.core.errors import JSONDuplicateKeyError

# A complete JSON string or one bracket. The unrolled string body stays
# linear, and strings are skipped so brackets inside them do not count.
_STRING_OR_BRACKET = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[][{}]', re.DOTALL)
_CLOSERS = {"[": "]", "{": "}"}

ProblemCode = Literal[
    "missing_comma",
    "unclosed_quote",
    "trailing_comma",
    "unexpected_close",
    "unquoted_key",
    "single_quotes",
    "missing_close",
    "incomplete",
    "json_duplicate",
    "json_other",
    "yaml_tab",
    "yaml_incomplete",
    "yaml_duplicate",
    "yaml_colon",
    "yaml_other",
]


@dataclass(frozen=True)
class Problem:
    """One located syntax problem. ``lineno`` is 1-based.

    ``token`` is the bracket for a close problem, or the repeated key for
    a duplicate (empty when a YAML key is not a plain or quoted scalar).
    """

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
    to continue. Repeated JSON keys give one problem per key and line. Any
    other failure contributes at most one problem.
    """
    if source.strip().casefold() == "yaml":
        tabs = tuple(Problem("yaml_tab", line) for line in leading_tab_lines(text))
        if tabs:
            return tabs
    if exc is None:
        return ()
    if isinstance(exc, JSONDuplicateKeyError):
        return duplicate_key_problems(exc)
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


def duplicate_key_problems(exc: JSONDuplicateKeyError) -> tuple[Problem, ...]:
    """One problem per repeated key, in text order.

    Repeats of one key on one line are one row, because the line and the
    words would be the same.
    """
    found: dict[tuple[int, str], Problem] = {}
    for duplicate in exc.duplicates:
        place = (duplicate.lineno, duplicate.key)
        if place not in found:
            found[place] = Problem("json_duplicate", duplicate.lineno, duplicate.key)
    return tuple(found.values())


def classify_failure(exc: BaseException, text: str) -> Problem | None:
    """Map a parser exception to one problem, or ``None`` when it is not syntax."""
    if isinstance(exc, JSONDuplicateKeyError):
        return duplicate_key_problems(exc)[0]
    if isinstance(exc, json.JSONDecodeError):
        return _classify_json(exc, text)
    if isinstance(exc, YAMLError):
        return _classify_yaml(exc, text)
    return None


def _classify_json(exc: json.JSONDecodeError, text: str) -> Problem:
    message = exc.msg
    char = _char_at(text, exc.pos)
    if message.startswith("Unterminated string"):
        return Problem("unclosed_quote", exc.lineno)
    if message.startswith("Invalid control character") and char in {"\n", "\r"}:
        return Problem("unclosed_quote", exc.lineno)
    if char == "'" and message in {
        "Expecting value",
        "Expecting property name enclosed in double quotes",
    }:
        return Problem("single_quotes", exc.lineno)
    if message == "Expecting property name enclosed in double quotes" and _word_start(
        char
    ):
        return Problem("unquoted_key", exc.lineno)
    if message == "Expecting ',' delimiter":
        if _at_end(text, exc.pos) or char in {"}", "]"}:
            return _missing_close(text, exc.pos, exc.lineno)
        return Problem("missing_comma", _comma_line(text, exc.lineno, exc.colno))
    if _at_end(text, exc.pos):
        return _missing_close(text, exc.pos, exc.lineno)
    comma_at = _comma_before(text, exc.pos)
    if comma_at is not None:
        return Problem("trailing_comma", _line_of_index(text, comma_at))
    if char in {"}", "]"}:
        return Problem("unexpected_close", exc.lineno, char)
    return Problem("json_other", max(exc.lineno, 1))


def _missing_close(text: str, pos: int, lineno: int) -> Problem:
    """Name the innermost bracket still open at ``pos``.

    The parser accepted everything before ``pos``, so one pass over that
    prefix finds the open brackets. With none open, the text just ends.
    """
    opened = _innermost_open(text, pos)
    if opened is None:
        return Problem("incomplete", _end_line(text, lineno))
    return Problem("missing_close", _line_of_index(text, opened), text[opened])


def _innermost_open(text: str, end: int) -> int | None:
    stack: list[int] = []
    for match in _STRING_OR_BRACKET.finditer(text, 0, end):
        token = match.group()
        if token in _CLOSERS:
            stack.append(match.start())
        elif token in {"]", "}"} and stack:
            stack.pop()
    return stack[-1] if stack else None


def _word_start(char: str) -> bool:
    """True for a character that can start a bare key such as ``name``."""
    return char.isalnum() or char in {"_", "$"}


def _classify_yaml(exc: YAMLError, text: str) -> Problem:
    problem = getattr(exc, "problem", None)
    detail = problem.casefold() if isinstance(problem, str) else ""
    line, column = _yaml_location(exc)
    if "cannot start any token" in detail and (
        "tab" in detail or _indent_has_tab(_line_text(text, line))
    ):
        return Problem("yaml_tab", line)
    if "duplicate key" in detail:
        return Problem("yaml_duplicate", line, _yaml_key_at(text, line, column))
    if _expects_delimiter(detail) and _at_line_start(text, line, column):
        line = _previous_nonblank(text, line)
    if "unexpected end of stream" in detail:
        return Problem("yaml_incomplete", line)
    if "expected ':'" in detail:
        return Problem("yaml_colon", line)
    return Problem("yaml_other", line)


def _yaml_key_at(text: str, line: int, column: int) -> str:
    """Return the scalar key that starts at ``line`` and ``column``, or "".

    The loader names a repeated key only inside its message, unescaped, so
    the key is read back from the tokens instead. Only a scalar right after
    a key token counts, so a mark on a value never names that value. This
    runs only after the loader reported a repeat, and stops at that place.
    """
    target = (line, column)
    previous: object = None
    try:
        for token in YAML(typ="safe", pure=True).scan(text):
            mark = token.start_mark
            place = (mark.line + 1, mark.column + 1)
            if place > target:
                return ""
            if (
                place == target
                and isinstance(token, ScalarToken)
                and isinstance(previous, KeyToken)
                and isinstance(token.value, str)
            ):
                return token.value
            previous = token
    except YAMLError:
        return ""
    return ""


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
