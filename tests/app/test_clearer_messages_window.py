"""#195 and #91 C2 in the window: Problems rows, the Convert offer, status copy.

Inputs are the ones in #195; all data is made up. Every Problems row is
checked in light and dark mode, with the test setting the scheme.
"""

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from galley.app import main_window as main_window_module
from galley.app.editor import CodeEditor
from galley.app.escape_string import convert_python_too_large_message
from galley.app.main import create_main_window
from galley.app.main_window import si_size_up
from galley.app.parse_session import ParseSession
from galley.app.problem_text import place_text
from galley.app.problems_pane import ProblemsPane

_SCHEMES = ["light", "dark"]
_CANCELED = "Canceled. Showing the previous result."
_NOT_PYTHON = "This isn't valid Python, so it can't be converted."
_TOOLTIP = "Convert this Python dictionary to JSON or YAML"

_B9 = '{"name": "Ada", "active": True, "manager": None, "scores": (1, 2, 3)}'

_JSON_CASES = {
    "B6": (
        '{\n"items": [1, 2, 3,\n"done": true\n}',
        ["Missing ] for the [ opened on line 2."],
        [2],
    ),
    "B7": (
        '{\n"order": 7,\n"lines": [\n{"sku": "A-1", "qty": 2},\n{"sku": "B-7", "qty":',
        ["The text ends on line 5 before the JSON document finishes."],
        [5],
    ),
    "B9": (
        _B9,
        [
            "Use true instead of True on line 1.",
            "Use null instead of None on line 1.",
            "Use [ and ] instead of ( and ) for a list on line 1.",
        ],
        [1, 1, 1],
    ),
    "B9 False": ('{"done": False}', ["Use false instead of False on line 1."], [1]),
    "B9 tuple closed with ]": (
        '{"a": (1, 2]}',
        ["Use [ and ] instead of ( and ) for a list on line 1."],
        [1],
    ),
    "B7 after a key": (
        '{"a"',
        ["The text ends on line 1 before the JSON document finishes."],
        [1],
    ),
    "B10": (
        '{\n// the user\'s name\n"name": "Ada Example",\n"age": 36 /* years */\n}',
        [
            "JSON doesn't allow comments. Remove the comment on line 2.",
            "JSON doesn't allow comments. Remove the comment on line 4.",
        ],
        [2, 4],
    ),
}

# Input, row sentence, place label, status.
_CONVERT_CASES = {
    "E7": (
        "{'name': 'Ada', 'age': 36",
        "Missing } for the { opened on line 1.",
        "Line 1, Col 1",
        _NOT_PYTHON,
    ),
    "E8": (
        "config = {'debug': True}",
        'Remove "config =" on line 1. '
        "Convert from Python reads a value, not an assignment.",
        "Line 1, Col 1",
        "1 problem",
    ),
    "E8 import": (
        "import os",
        "Convert from Python reads a value, not a statement, on line 1.",
        "Line 1, Col 1",
        "1 problem",
    ),
    "E8 two statements": (
        "x = 1; y = 2",
        "Convert from Python reads a value, not a statement, on line 1.",
        "Line 1, Col 1",
        "1 problem",
    ),
    "E8 attribute": (
        "a.b = 1",
        "Convert from Python reads a value, not a statement, on line 1.",
        "Line 1, Col 1",
        "1 problem",
    ),
    "E8 two values": (
        "{'a': 1}\n{'b': 2}",
        "Convert from Python reads one value. Remove the extra value on line 2.",
        "Line 2, Col 1",
        "1 problem",
    ),
    "E8 only comments": (
        "# just a comment",
        "There's no value to convert. The text has only comments.",
        "Line 1, Col 1",
        "1 problem",
    ),
    "E8 other": (
        "{'owner': <Customer object at 0x10>}",
        _NOT_PYTHON,
        "Line 1, Col 11",
        _NOT_PYTHON,
    ),
    "E9": (
        '{"name": "Ada", "active": true, "manager": null}',
        "This looks like JSON, not Python. Use Format instead.",
        "Line 1, Col 27",
        "1 problem",
    ),
    "E10": (
        "{'ratio': float('nan')}",
        "NaN and Infinity can't be converted to JSON.",
        "Line 1, Col 11",
        "1 problem",
    ),
    "E11": (
        "{'z': 3+4j}",
        "Complex numbers can't be converted.",
        "Line 1, Col 7",
        "1 problem",
    ),
    "E11 arithmetic": (
        "{'z': 9 ** 9}",
        "Arithmetic can't be converted. Write the result instead.",
        "Line 1, Col 7",
        "1 problem",
    ),
}


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1280, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _input(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _output(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _pane(window) -> ProblemsPane:
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    return pane


def _offer(window) -> QPushButton:
    button = window.findChild(QPushButton, "convertPythonButton")
    assert isinstance(button, QPushButton)
    return button


def _state(window) -> str:
    return window._status.state_text()


def _run(qtbot, window, command: str) -> None:
    _action(window, command).trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=15000)


def _format(qtbot, window, text: str) -> None:
    _input(window).setPlainText(text)
    _run(qtbot, window, "Format")


def _hold_jobs(monkeypatch, window) -> None:
    """Every job starts and then waits, so the test can cancel it."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]


# --- Broken JSON rows ---------------------------------------------------------


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize("case", list(_JSON_CASES))
def test_broken_json_gets_one_correct_row_per_problem(
    qtbot, install_scheme, scheme: str, case: str
) -> None:
    text, messages, lines = _JSON_CASES[case]
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, text)
    pane = _pane(window)
    assert pane.messages() == messages
    assert pane.lines() == lines
    assert _input(window).problem_lines() == set(lines)
    noun = "problem" if len(messages) == 1 else "problems"
    assert _state(window) == f"{len(messages)} {noun}"


# Erik's two examples (#195 C3): a "]" inside a string or a comment doesn't
# close the array. Two sure rows, and no stale missing-comma row after them.
_CLOSER_IN_TEXT = {
    "single quotes": (
        '{"a": ["x" : 1, \']\', "y" : 2}',
        "Use double quotes instead of single quotes on line 1.",
    ),
    "comment": (
        '{"a": ["x" : 1, // ]\n"y" : 2}',
        "JSON doesn't allow comments. Remove the comment on line 1.",
    ),
}


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize("case", list(_CLOSER_IN_TEXT))
def test_a_closer_in_a_string_or_comment_gives_two_sure_rows(
    qtbot, install_scheme, scheme: str, case: str
) -> None:
    text, second = _CLOSER_IN_TEXT[case]
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, text)
    rows = _pane(window).shown()
    messages = ["Missing ] for the [ opened on line 1.", second]
    # No row has the uncertain marker.
    assert [row.full_message() for row in rows] == messages
    assert [row.uncertain for row in rows] == [False, False]
    assert [row.lineno for row in rows] == [1, 1]
    assert _state(window) == "2 problems"


# --- The Convert from Python offer (B9) ---------------------------------------


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize(
    "text",
    [_B9, '{"done": False}', '{"scores": (1, 2)}', "{'name': 'Ada'}"],
    ids=["True and None", "False", "tuple", "single quotes"],
)
def test_python_rows_offer_convert_from_python(
    qtbot, install_scheme, scheme: str, text: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, text)
    button = _offer(window)
    assert button.isVisibleTo(window)
    # A labeled button with the </> icon, never icon-only (Bill).
    assert button.text() == "Convert from Python"
    assert not button.icon().isNull()
    assert button.accessibleName() == "Convert from Python"
    assert button.toolTip() == _TOOLTIP


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_a_plain_json_error_offers_nothing(qtbot, install_scheme, scheme) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, '{"name": "Ada"\n"age": 36}')
    assert _pane(window).messages() == ["Missing comma at the end of line 1."]
    assert not _offer(window).isVisibleTo(window)


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize("command", ["Minify", "Validate"])
def test_minify_or_validate_on_python_rows_offers_nothing(
    qtbot, install_scheme, scheme: str, command: str
) -> None:
    """Only Format offers Convert from Python (Bill, #195 B9)."""
    window = _show(qtbot)
    install_scheme(scheme)
    _input(window).setPlainText(_B9)
    _run(qtbot, window, command)
    assert "Use true instead of True on line 1." in _pane(window).messages()
    assert not _offer(window).isVisibleTo(window)


def test_the_offer_is_after_the_footers_last_control(qtbot) -> None:
    window = _show(qtbot)
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(cancel, QPushButton)
    assert cancel.nextInFocusChain() is _offer(window)


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_the_offer_goes_away_on_an_input_edit(qtbot, install_scheme, scheme) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, _B9)
    assert _offer(window).isVisibleTo(window)
    _input(window).insertPlainText(" ")
    assert not _offer(window).isVisibleTo(window)


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_the_offer_goes_away_on_a_format(
    qtbot, install_scheme, monkeypatch, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, _B9)
    assert _offer(window).isVisibleTo(window)
    _hold_jobs(monkeypatch, window)
    _action(window, "Format").trigger()
    assert window._formatting
    assert not _offer(window).isVisibleTo(window)


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_the_offer_runs_convert_and_goes_away(qtbot, install_scheme, scheme) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, _B9)
    _offer(window).click()
    assert not _offer(window).isVisibleTo(window)
    assert '"active": true' in _output(window).toPlainText()
    assert _pane(window).messages() == []
    assert _state(window).startswith("Converted to JSON. ")


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_the_offer_goes_away_when_convert_fails(
    qtbot, install_scheme, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, "{'name': 'Ada' 'age': 36}")
    assert _offer(window).isVisibleTo(window)
    _offer(window).click()
    assert not _offer(window).isVisibleTo(window)
    assert _pane(window).messages() == [_NOT_PYTHON]


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_the_offer_goes_away_when_convert_is_too_large(
    qtbot, install_scheme, monkeypatch, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format(qtbot, window, _B9)
    assert _offer(window).isVisibleTo(window)
    monkeypatch.setattr(main_window_module, "PYTHON_TO_JSON_BYTES", 10)
    _offer(window).click()
    assert not _offer(window).isVisibleTo(window)
    size = si_size_up(len(_B9.encode("utf-8")))
    assert _state(window) == convert_python_too_large_message(size)


# --- Convert from Python errors are Problems rows (D5) ------------------------


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize("case", list(_CONVERT_CASES))
def test_a_convert_error_is_a_problems_row_with_a_marker(
    qtbot, install_scheme, scheme: str, case: str
) -> None:
    text, message, place, status = _CONVERT_CASES[case]
    window = _show(qtbot)
    install_scheme(scheme)
    _output(window).setPlainText("previous\n")
    _input(window).setPlainText(text)
    _action(window, "Convert from Python").trigger()
    pane = _pane(window)
    assert pane.messages() == [message]
    (row,) = pane.shown()
    assert place_text(row.lineno, row.colno) == place
    assert _input(window).problem_lines() == {row.lineno}
    assert _state(window) == status
    assert _output(window).toPlainText() == "previous\n"


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_a_good_convert_clears_the_convert_error(qtbot, install_scheme, scheme) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _input(window).setPlainText("{'z': 3+4j}")
    convert = _action(window, "Convert from Python")
    convert.trigger()
    assert _pane(window).messages() == ["Complex numbers can't be converted."]
    _input(window).setPlainText("{'z': 7}")
    convert.trigger()
    assert _pane(window).messages() == []
    assert _input(window).problem_lines() == set()
    assert _state(window) == "Converted to JSON."


# --- Status copy (D9) ---------------------------------------------------------


@pytest.mark.parametrize("scheme", _SCHEMES)
@pytest.mark.parametrize("target", ["JSON", "YAML"])
def test_convert_says_converted_then_the_notes(
    qtbot, install_scheme, scheme: str, target: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    picker = window.findChild(QComboBox, "outputFormat")
    assert isinstance(picker, QComboBox)
    picker.setCurrentText(target)
    _input(window).setPlainText("{'nums': (1, 2), 3: 'x'}")
    _action(window, "Convert from Python").trigger()
    assert _state(window) == (
        f"Converted to {target}. 1 tuple was converted to a list. "
        "1 dictionary key was converted to a string."
    )


@pytest.mark.parametrize("command", ["Format", "Minify"])
def test_a_canceled_format_or_minify_shows_the_previous_result(
    qtbot, monkeypatch, command: str
) -> None:
    window = _show(qtbot)
    _output(window).setPlainText("previous\n")
    _hold_jobs(monkeypatch, window)
    _input(window).setPlainText('{"name": "Ada"}')
    _action(window, command).trigger()
    assert window._formatting
    window._cancel_format()
    assert _state(window) == _CANCELED
    assert _output(window).toPlainText() == "previous\n"


def test_a_canceled_validate_keeps_its_own_words(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _hold_jobs(monkeypatch, window)
    _input(window).setPlainText('{"name": "Ada"}')
    _action(window, "Validate").trigger()
    assert window._formatting
    window._cancel_format()
    assert _state(window) == "Validating canceled."


def test_a_cancel_keeps_the_stale_tag_of_the_previous_result(
    qtbot, monkeypatch
) -> None:
    window = _show(qtbot)
    _format(qtbot, window, '{"name": "Ada"}')
    _input(window).insertPlainText(" ")
    tag = window.findChild(QLabel, "outputStaleTag")
    assert isinstance(tag, QLabel)
    assert tag.isVisibleTo(window)
    _hold_jobs(monkeypatch, window)
    _action(window, "Format").trigger()
    window._cancel_format()
    assert _state(window) == _CANCELED
    assert tag.isVisibleTo(window)
