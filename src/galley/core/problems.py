"""Structured syntax problems.

The codes here are stable ids. The sentences a person reads are chosen in
``galley.app``. A YAML indent scan reports every leading tab at once. A
JSON document reports every syntax error and repeated key: after
``json.loads`` fails, ``scan_json`` reads on past each error. Other YAML
failures still stop at the first problem, so those stay a single code.
Expat stops at the first XML error, so XML reports one ``xml_*`` code.
"""

import json
import re
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import KeyToken, ScalarToken

from galley.core.errors import JSONDuplicateKeyError, XMLSyntaxError
from galley.core.json_recover import Scan, Slip, scan_json

# A complete JSON string or one bracket. The unrolled string body stays
# linear, and strings are skipped so brackets inside them do not count.
_STRING_OR_BRACKET = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[][{}]', re.DOTALL)
_CLOSERS = {"[": "]", "{": "}"}
_NEWLINE = re.compile("\n")

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
    "json_stopped",
    "yaml_tab",
    "yaml_incomplete",
    "yaml_duplicate",
    "yaml_colon",
    "yaml_other",
    "xml_mismatched_tag",
    "xml_unclosed",
    "xml_bad_token",
    "xml_duplicate_attribute",
    "xml_undefined_entity",
    "xml_entity_declared",
    "xml_unbound_prefix",
    "xml_after_root",
    "xml_no_root",
    "xml_other",
]


@dataclass(frozen=True)
class Problem:
    """One located syntax problem. ``lineno`` and ``colno`` are 1-based.

    ``token`` is the bracket for a close problem, or the repeated key for
    a duplicate (empty when a YAML key is not a plain or quoted scalar).
    For XML it is the element, attribute, entity, or prefix named.
    ``colno`` is 0 when the parser gives no column. ``uncertain`` is True
    when the problem was found after the JSON scan guessed at a repair,
    so an earlier problem may have caused it.

    ``json_stopped`` is not a problem: it ends the list when the scan
    stopped at ``MAX_FINDINGS``, on the line where it stopped.

    Problems compare by code, line, and token, the parts a sentence says.
    """

    code: ProblemCode
    lineno: int
    token: str = ""
    colno: int = field(default=0, compare=False)
    uncertain: bool = field(default=False, compare=False)


def collect_problems(
    text: str,
    source: str,
    exc: BaseException | None = None,
) -> tuple[Problem, ...]:
    """Return problems to show before, or instead of, a raw parser message.

    YAML leading tabs are collected up front and the parser is not asked
    to continue. Repeated JSON keys give one problem per key and line. A
    JSON syntax error gives every problem ``scan_json`` finds, in text
    order. Any other failure contributes at most one problem.
    """
    if source.strip().casefold() == "yaml":
        tabs = tuple(Problem("yaml_tab", line) for line in leading_tab_lines(text))
        if tabs:
            return tabs
    if exc is None:
        return ()
    if isinstance(exc, JSONDuplicateKeyError):
        return duplicate_key_problems(exc)
    if isinstance(exc, json.JSONDecodeError):
        return json_problems(exc, text)
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
            found[place] = Problem(
                "json_duplicate", duplicate.lineno, duplicate.key, duplicate.colno
            )
    return tuple(found.values())


def json_problems(exc: json.JSONDecodeError, text: str) -> tuple[Problem, ...]:
    """Every problem in JSON ``text`` that ``json.loads`` rejected with ``exc``.

    The first error ``scan_json`` finds must be the one ``exc`` names. If
    it is not, only ``exc`` is reported, so the first error never changes.
    Problems are sorted by line and column. Repeats of one key on one
    line are one row, as in ``duplicate_key_problems``.
    """
    scan = scan_json(text)
    first = scan.slips[0] if scan.slips else None
    if first is None or (first.message, first.pos) != (exc.msg, exc.pos):
        return (_classify_json(exc, text),)
    return _scan_problems(scan, text)


def _scan_problems(scan: Scan, text: str) -> tuple[Problem, ...]:
    lines = _Lines(text)
    found: list[Problem] = [_slip_problem(slip, text, lines) for slip in scan.slips]
    for repeat in scan.repeats:
        found.append(
            Problem(
                "json_duplicate",
                lines.line_of(repeat.pos),
                repeat.key,
                lines.column(repeat.pos),
                repeat.after_guess,
            )
        )
    unique: dict[tuple[str, int, int, str], Problem] = {}
    for problem in sorted(found, key=lambda item: (item.lineno, item.colno)):
        column = 0 if problem.code == "json_duplicate" else problem.colno
        place = (problem.code, problem.lineno, column, problem.token)
        unique.setdefault(place, problem)
    ordered = tuple(unique.values())
    if scan.stopped_at is None:
        return ordered
    where = scan.stopped_at
    stopped = Problem("json_stopped", lines.line_of(where), "", lines.column(where))
    return (*ordered, stopped)


def classify_failure(exc: BaseException, text: str) -> Problem | None:
    """Map a parser exception to one problem, or ``None`` when it is not syntax."""
    if isinstance(exc, JSONDuplicateKeyError):
        return duplicate_key_problems(exc)[0]
    if isinstance(exc, json.JSONDecodeError):
        return _classify_json(exc, text)
    if isinstance(exc, YAMLError):
        return _classify_yaml(exc, text)
    if isinstance(exc, XMLSyntaxError):
        return Problem(exc.code, exc.lineno, exc.token, exc.colno)
    return None


def _classify_json(exc: json.JSONDecodeError, text: str) -> Problem:
    quote = text.rfind('"', 0, exc.pos) if exc.pos > 0 else -1
    return _json_problem(
        text,
        _Lines(text),
        exc.msg,
        exc.pos,
        lambda: _innermost_open(text, exc.pos),
        quote if quote >= 0 else exc.pos,
    )


def _slip_problem(slip: Slip, text: str, lines: "_Lines") -> Problem:
    return _json_problem(
        text,
        lines,
        slip.message,
        slip.pos,
        lambda: slip.opened,
        slip.start,
        uncertain=slip.after_guess,
    )


def _json_problem(
    text: str,
    lines: "_Lines",
    message: str,
    pos: int,
    innermost: Callable[[], int | None],
    quote: int,
    *,
    uncertain: bool = False,
) -> Problem:
    """Name the problem behind ``json.loads``'s ``message`` at ``pos``.

    ``innermost`` returns the innermost bracket open at ``pos``. ``quote``
    is where the string with an error opened.
    """
    char = _char_at(text, pos)
    lineno = lines.line_of(pos)

    def at(code: ProblemCode, index: int, token: str = "") -> Problem:
        return Problem(
            code, lines.line_of(index), token, lines.column(index), uncertain
        )

    if message.startswith("Unterminated string"):
        return at("unclosed_quote", pos)
    if message.startswith("Invalid control character") and char in {"\n", "\r"}:
        # The quote opened on this line, since the line break is the error.
        column = lines.column(quote) if lines.line_of(quote) == lineno else 0
        return Problem("unclosed_quote", lineno, "", column, uncertain)
    if char == "'" and message in {
        "Expecting value",
        "Expecting property name enclosed in double quotes",
    }:
        return at("single_quotes", pos)
    if message == "Expecting property name enclosed in double quotes" and _word_start(
        char
    ):
        return at("unquoted_key", pos)
    if message == "Expecting ',' delimiter":
        if _at_end(text, pos) or char in {"}", "]"}:
            return _missing_close(lines, pos, innermost, uncertain)
        line = _comma_line(lines, pos)
        return Problem(
            "missing_comma", line, "", _comma_column(lines, pos, line), uncertain
        )
    if _at_end(text, pos):
        return _missing_close(lines, pos, innermost, uncertain)
    comma_at = _comma_before(text, pos)
    if comma_at is not None:
        return at("trailing_comma", comma_at)
    if char in {"}", "]"}:
        return at("unexpected_close", pos, char)
    return Problem("json_other", max(lineno, 1), "", lines.column(pos), uncertain)


def _missing_close(
    lines: "_Lines",
    pos: int,
    innermost: Callable[[], int | None],
    uncertain: bool,
) -> Problem:
    """Name the innermost bracket still open at ``pos``.

    With none open, the text just ends.
    """
    opened = innermost()
    if opened is None:
        line = _end_line(lines, lines.line_of(pos))
        column = len(lines.line_text(line).rstrip()) + 1
        return Problem("incomplete", line, "", column, uncertain)
    return Problem(
        "missing_close",
        lines.line_of(opened),
        lines.text[opened],
        lines.column(opened),
        uncertain,
    )


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
    """One problem at the parser's ``problem_mark``, with its 1-based column.

    The column is 0 when the error has no mark (a ``ReaderError``), and when
    the problem moves back to the previous non-blank line: the parser's
    column belongs to the line it stopped on, so none is made up for the
    earlier one.
    """
    problem = getattr(exc, "problem", None)
    detail = problem.casefold() if isinstance(problem, str) else ""
    line, column = _yaml_location(exc)
    colno = column if _yaml_marked(exc) else 0
    lines = _Lines(text)
    if "cannot start any token" in detail and (
        "tab" in detail or _indent_has_tab(lines.line_text(line))
    ):
        return Problem("yaml_tab", line, "", colno)
    if "duplicate key" in detail:
        key = _yaml_key_at(text, line, column)
        return Problem("yaml_duplicate", line, key, colno)
    if _expects_delimiter(detail) and _at_line_start(lines, line, column):
        line = _previous_nonblank(lines, line)
        colno = 0
    if "unexpected end of stream" in detail:
        return Problem("yaml_incomplete", line, "", colno)
    if "expected ':'" in detail:
        return Problem("yaml_colon", line, "", colno)
    return Problem("yaml_other", line, "", colno)


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


def _yaml_marked(exc: YAMLError) -> bool:
    """True when the error has a ``problem_mark`` with a line and column."""
    mark = getattr(exc, "problem_mark", None)
    return isinstance(getattr(mark, "line", None), int) and isinstance(
        getattr(mark, "column", None), int
    )


def _yaml_location(exc: YAMLError) -> tuple[int, int]:
    mark = getattr(exc, "problem_mark", None)
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        return line + 1, column + 1
    return 1, 1


def _comma_line(lines: "_Lines", pos: int) -> int:
    """Point a missing comma at the previous line when the parser is at its start."""
    lineno = lines.line_of(pos)
    if _at_line_start(lines, lineno, lines.column(pos)):
        return _previous_nonblank(lines, lineno)
    return max(lineno, 1)


def _comma_column(lines: "_Lines", pos: int, line: int) -> int:
    """The column just after the value that needs the comma."""
    index = pos - 1
    while index >= 0 and lines.text[index] in " \t\r\n":
        index -= 1
    if index >= 0 and lines.line_of(index) == line:
        return lines.column(index) + 1
    return len(lines.line_text(line).rstrip()) + 1


def _end_line(lines: "_Lines", lineno: int) -> int:
    if lines.line_text(lineno).strip() == "":
        return _previous_nonblank(lines, lineno)
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


def _at_line_start(lines: "_Lines", lineno: int, colno: int) -> bool:
    prefix = lines.line_text(lineno)[: max(colno - 1, 0)]
    return prefix.strip() == ""


def _previous_nonblank(lines: "_Lines", lineno: int) -> int:
    index = min(max(lineno, 1), lines.count() + 1) - 1
    while index >= 1:
        if lines.line_text(index).strip() != "":
            return index
        index -= 1
    return max(lineno, 1)


class _Lines:
    """Where each line of ``text`` starts, so a lookup is a binary search.

    Lines end at ``\n``, as ``json`` counts them. A ``\r`` before it stays
    in the line's text.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self._breaks = [match.start() for match in _NEWLINE.finditer(text)]

    def count(self) -> int:
        return len(self._breaks) + 1

    def line_of(self, index: int) -> int:
        return bisect_left(self._breaks, max(index, 0)) + 1

    def start(self, lineno: int) -> int:
        return 0 if lineno <= 1 else self._breaks[lineno - 2] + 1

    def column(self, index: int) -> int:
        return max(index, 0) - self.start(self.line_of(index)) + 1

    def line_text(self, lineno: int) -> str:
        if lineno < 1 or lineno > self.count():
            return ""
        end = (
            self._breaks[lineno - 1] if lineno <= len(self._breaks) else len(self.text)
        )
        return self.text[self.start(lineno) : end]


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _indent_has_tab(line: str) -> bool:
    for char in line:
        if char == "\t":
            return True
        if char not in " \r":
            return False
    return False
