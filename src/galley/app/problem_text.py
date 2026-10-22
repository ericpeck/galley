"""Plain-language sentences for syntax problems.

Core reports a code and a line. The words live here, with no column and
no character offset.
"""

import json
from typing import assert_never

from galley.app.banner import banner_text
from galley.core.errors import InputError
from galley.core.problems import Problem, ProblemCode

# Longer keys are cut to this many characters and end with "…".
KEY_DISPLAY_LIMIT = 40


def message_for(problem: Problem) -> str:
    """Return the sentence for ``problem``."""
    line = problem.lineno
    code: ProblemCode = problem.code
    if code == "missing_comma":
        return f"Missing comma at the end of line {line}."
    if code == "unclosed_quote":
        return f"Unclosed quote on line {line}."
    if code == "trailing_comma":
        return f"Trailing comma at the end of line {line}."
    if code == "unexpected_close":
        token = problem.token if problem.token else "}"
        return f"Unexpected {token} on line {line}."
    if code == "unquoted_key":
        return f"Put double quotes around the key on line {line}."
    if code == "single_quotes":
        return f"Use double quotes instead of single quotes on line {line}."
    if code == "missing_close":
        opener = problem.token if problem.token else "{"
        closer = "]" if opener == "[" else "}"
        return f"Missing {closer} for the {opener} opened on line {line}."
    if code == "incomplete":
        return f"The text ends on line {line} before the JSON document finishes."
    if code in ("json_duplicate", "yaml_duplicate"):
        return _duplicate_key_text(problem.token, line)
    if code == "json_other":
        return f"Couldn't read this JSON on line {line}."
    if code == "yaml_tab":
        return f"Line {line} starts with a tab. YAML needs spaces for indenting."
    if code == "yaml_incomplete":
        return f"The text ends on line {line} before the YAML document finishes."
    if code == "yaml_colon":
        return f"Missing colon after the key on line {line}."
    if code == "yaml_other":
        return f"Couldn't read this YAML on line {line}."
    assert_never(code)


def _duplicate_key_text(key: str, line: int) -> str:
    if not key:
        return f"Remove the repeated key on line {line}."
    return f"Remove the repeated key {quoted_key(key)} on line {line}."


def quoted_key(key: str) -> str:
    """Return ``key`` in double quotes, escaped the way JSON writes it.

    Characters that do not print, such as a zero-width space or a
    direction mark, are shown as ``\\uXXXX`` so the key reads as typed.
    """
    if len(key) > KEY_DISPLAY_LIMIT:
        key = key[:KEY_DISPLAY_LIMIT] + "…"
    quoted = json.dumps(key, ensure_ascii=False)
    return "".join(
        char if char.isprintable() else f"\\u{ord(char):04x}" for char in quoted
    )


def accessible_text(problem: Problem) -> str:
    """Return the screen-reader text for one problem row."""
    if problem.code == "yaml_tab":
        return (
            f"Line {problem.lineno}: Starts with a tab. "
            "YAML needs spaces for indenting."
        )
    return f"Line {problem.lineno}: {message_for(problem)}"


def problem_count_text(count: int) -> str:
    """Return the status-bar count. Zero is not a status."""
    noun = "problem" if count == 1 else "problems"
    return f"{count} {noun}"


class DisplayProblem:
    """One row in the Problems pane."""

    def __init__(self, lineno: int, message: str, accessible: str, code: str) -> None:
        self.lineno = lineno
        self.message = message
        self.accessible = accessible
        self.code = code


def display_problem(problem: Problem) -> DisplayProblem:
    """Build a pane row from a core problem."""
    return DisplayProblem(
        problem.lineno,
        message_for(problem),
        accessible_text(problem),
        problem.code,
    )


def display_input_error(error: InputError) -> DisplayProblem:
    """Build a pane row from a located input error.

    Those banners were already approved, including a column where the
    byte or the escape needs one.
    """
    message = banner_text(error)
    line = error.lineno if error.lineno is not None else 1
    return DisplayProblem(line, message, f"Line {line}: {message}", error.kind)
