"""Plain-language sentences for syntax problems.

Core reports a code, a line, and a column. The words live here. A
sentence names the line only; a pane row shows the column beside it.
"""

import json
from typing import assert_never

from galley.app.banner import banner_text
from galley.core.errors import InputError
from galley.core.problems import Problem, ProblemCode

# Longer keys are cut to this many characters and end with "…".
KEY_DISPLAY_LIMIT = 40

# After the scan guessed at a repair, a later problem may come from the guess.
UNCERTAIN_SUFFIX = "(may be caused by an earlier error)"
STOPPED_TEXT = "Galley stopped checking after this point."


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
    if code == "json_stopped":
        return STOPPED_TEXT
    if code == "yaml_tab":
        return f"Line {line} starts with a tab. YAML needs spaces for indenting."
    if code == "yaml_incomplete":
        return f"The text ends on line {line} before the YAML document finishes."
    if code == "yaml_colon":
        return f"Missing colon after the key on line {line}."
    if code == "yaml_other":
        return f"Couldn't read this YAML on line {line}."
    if code.startswith("xml_"):
        return _xml_text(problem)
    assert_never(code)


def _xml_text(problem: Problem) -> str:
    """The sentence for an ``xml_*`` problem (#63, Bill's B4 wording)."""
    line = problem.lineno
    name = _xml_name(problem.token)
    code = problem.code
    if code == "xml_mismatched_tag":
        closing = _xml_name(problem.found)
        return f"Line {line} closes </{closing}>, but <{name}> is still open."
    if code == "xml_unclosed":
        return f"<{name}> opened on line {line} is never closed."
    if code == "xml_unfinished_tag":
        return f"The tag on line {line} isn't finished."
    if code == "xml_entity_declared":
        return f"Galley doesn't load DTD entities. Remove the <!ENTITY> on line {line}."
    if code == "xml_undefined_entity":
        return f"Unknown entity &{name}; on line {line}."
    if code == "xml_duplicate_attribute":
        return f"Attribute {name} appears twice in this tag."
    if code == "xml_unbound_prefix":
        return f"Prefix {name} isn't declared. Add an xmlns:{name} attribute."
    if code == "xml_after_root":
        return (
            f"Text after the root element on line {line}. XML allows one root element."
        )
    if code == "xml_no_root":
        return "There's no root element."
    if code == "xml_bad_token":
        return (
            f"Unexpected character on line {line}. "
            "Write < as &lt; and & as &amp; in text."
        )
    return f"Couldn't read this XML on line {line}."


def _xml_name(name: str) -> str:
    """An element, attribute, entity, or prefix name, cut like a long key."""
    if len(name) > KEY_DISPLAY_LIMIT:
        return name[:KEY_DISPLAY_LIMIT] + "…"
    return name


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
    message = message_for(problem)
    if problem.uncertain:
        message = f"{message} {UNCERTAIN_SUFFIX}"
    return f"Line {problem.lineno}: {message}"


def problem_count_text(count: int) -> str:
    """Return the status-bar count. Zero is not a status."""
    noun = "problem" if count == 1 else "problems"
    return f"{count} {noun}"


def place_text(lineno: int, colno: int) -> str:
    """``Line N, Col M``, or ``Line N`` when the column is not known."""
    if colno > 0:
        return f"Line {lineno}, Col {colno}"
    return f"Line {lineno}"


def shown_count_text(shown: int, total: int) -> str:
    """The note under a cut list: ``Showing the first 100 of 240 problems.``"""
    return f"Showing the first {shown} of {total} problems."


def announcement_text(count: int, first: "DisplayProblem") -> str:
    """What a screen reader hears after a check that found problems."""
    message = first.message.rstrip(".")
    return f"{problem_count_text(count)}. First: {message}, line {first.lineno}"


class DisplayProblem:
    """One row in the Problems pane.

    ``colno`` is 0 when the column is not known. An ``uncertain`` row may
    come from an earlier problem and shows ``UNCERTAIN_SUFFIX``. A
    ``json_stopped`` row is a note that ends the list, not a problem.
    """

    def __init__(
        self,
        lineno: int,
        message: str,
        accessible: str,
        code: str,
        colno: int = 0,
        uncertain: bool = False,
    ) -> None:
        self.lineno = lineno
        self.message = message
        self.accessible = accessible
        self.code = code
        self.colno = colno
        self.uncertain = uncertain

    def is_note(self) -> bool:
        return self.code == "json_stopped"

    def full_message(self) -> str:
        """The message with the uncertain suffix, as the row shows it."""
        if self.uncertain:
            return f"{self.message} {UNCERTAIN_SUFFIX}"
        return self.message


def display_problem(problem: Problem) -> DisplayProblem:
    """Build a pane row from a core problem."""
    return DisplayProblem(
        problem.lineno,
        message_for(problem),
        accessible_text(problem),
        problem.code,
        problem.colno,
        problem.uncertain,
    )


def display_input_error(error: InputError) -> DisplayProblem:
    """Build a pane row from a located input error.

    Those banners were already approved, including a column where the
    byte or the escape needs one.
    """
    message = banner_text(error)
    line = error.lineno if error.lineno is not None else 1
    column = error.colno if error.colno is not None else 0
    return DisplayProblem(line, message, f"Line {line}: {message}", error.kind, column)
