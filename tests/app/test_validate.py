"""Validate checks Input with Format's checks and never changes Output."""

import io
import sys
from contextlib import redirect_stdout

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QWidget,
)
from tests.core.mask_rows import pretty_json, text_of_size
from tests.perf import time_runs

import galley.app.main_window as main_window_module
from galley.app.actions import (
    UNBUILT_ACTIONS,
    VALIDATE_TOOLTIP,
    is_unbuilt,
    main,
    release_failure,
)
from galley.app.appearance import MODE_SYSTEM, application_appearance
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import (
    VALID_MESSAGE,
    VALIDATE_BUSY_DESCRIPTION,
    VALIDATE_BUSY_TOOLTIP,
    VALIDATE_CANCELLED_MESSAGE,
    VALIDATE_EMPTY,
    VALIDATE_NOT_AVAILABLE,
    VALIDATE_STATUS_MS,
    VALIDATING_MESSAGE,
)
from galley.app.parse_session import ParseSession
from galley.app.problems_pane import ProblemsPane
from galley.app.problems_settings import STATE_CLOSED, STATE_MINIMIZED, STATE_OPEN
from galley.app.theme import TEXT_MIN_CONTRAST, contrast_ratio
from galley.core.format import validate_document

_PRETTY = '{\n  "name": "Ann",\n  "tags": [\n    "a",\n    "b"\n  ],\n  "n": 1.5\n}\n'
_INVALID = '{\n  "a": 1\n  "b": 2\n}\n'
_REPEATED = '{\n  "a": 1,\n  "a": 2,\n  "b": [1, 2,]\n}\n'
_VALID = {
    "JSON": _PRETTY,
    "YAML": "name: Ann\ntags:\n  - a\n  - b\n",
    "XML": "<r>\n  <a>1</a>\n</r>\n",
}
_INVALID_BY_FORMAT = {
    "JSON": _REPEATED,
    "YAML": "a: 1\na: 2\nb: [1, 2\n",
    "XML": "<r>\n  <a>\n  </b>\n</r>",
}
_WAIT_MS = 15_000
_WIDE_PX = 1600


@pytest.fixture(autouse=True)
def back_to_system(qapp):
    """Leave the next test on System, whatever this one picked."""
    yield
    appearance = application_appearance(qapp)
    if appearance is not None:
        appearance.set_mode(MODE_SYSTEM, save=False)
        QApplication.processEvents()


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _child(window, kind, name: str):
    widget = window.findChild(kind, name)
    assert isinstance(widget, kind), name
    return widget


def _editors(window) -> tuple[CodeEditor, CodeEditor]:
    return (
        _child(window, CodeEditor, "inputEditor"),
        _child(window, CodeEditor, "outputEditor"),
    )


def _status(window) -> str:
    return _child(window, QLabel, "statusMessage").text()


def _icon(window) -> QLabel:
    return _child(window, QLabel, "statusIcon")


def _validate(window) -> QAction:
    return window._actions.validate


def _button(window) -> QToolButton:
    for button in window.findChildren(QToolButton):
        if button.defaultAction() is _validate(window):
            return button
    raise AssertionError("no toolbar button for Validate")


def _labelled_button(qtbot, window) -> QToolButton:
    """Validate's button with its label showing, whatever the runner's font.

    The Windows runner's wider fallback font sheds the label at the default
    window size. The icon-only tooltip has its own test.
    """
    window.resize(_WIDE_PX, window.height())
    button = _button(window)
    qtbot.waitUntil(
        lambda: button.toolButtonStyle() != Qt.ToolButtonStyle.ToolButtonIconOnly,
        timeout=5000,
    )
    return button


def _input_format(window) -> QComboBox:
    return _child(window, QComboBox, "inputFormat")


def _run(qtbot, window, action: QAction | None = None) -> None:
    """Trigger Validate (or ``action``) and wait for the job to finish."""
    (action or _validate(window)).trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)


def _output_state(output: CodeEditor) -> tuple[object, ...]:
    document = output.document()
    return (
        output.toPlainText(),
        document.revision(),
        document.availableUndoSteps(),
        document.availableRedoSteps(),
        document.isModified(),
    )


# --- Built, copy and keys ---------------------------------------------------


def test_validate_is_built_and_off_the_release_lists(qtbot) -> None:
    window = _show(qtbot)
    action = _validate(window)
    actions = window._actions
    assert "Validate" not in UNBUILT_ACTIONS
    assert not is_unbuilt(action)
    assert action not in actions._stub_actions()
    assert action not in actions._not_yet_actions()
    assert action not in actions.stubs()
    message = release_failure()
    assert message is not None
    assert "Validate" not in message
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert main() == 1
    assert printed.getvalue() == (
        'Release blocked: these actions still show "Coming soon": '
        "Open Right Document…, Compare, Next Change, Previous Change, "
        "Link Scrolling.\n"
        "Build them or remove them from UNBUILT_ACTIONS in actions.py "
        "before tagging.\n"
        'Release blocked: these actions still show "Not yet": '
        "Find and Replace…, Output Options.\n"
        "Build them or remove them from _not_yet_actions() in actions.py "
        "before tagging.\n"
    )
    host = _button(window).parentWidget()
    assert host is None or host.property("comingSoonHost") is not True


def test_label_tooltip_description_and_no_shortcut_or_menu_item(qtbot) -> None:
    window = _show(qtbot)
    action = _validate(window)
    _editors(window)[0].setPlainText("{}")
    assert action.text() == "Validate"
    assert action.shortcut() == QKeySequence()
    assert action.shortcuts() == []
    assert VALIDATE_TOOLTIP == "Check Input for errors without changing Output."
    assert action.toolTip() == VALIDATE_TOOLTIP
    button = _labelled_button(qtbot, window)
    assert button.toolTip() == VALIDATE_TOOLTIP
    assert button.accessibleDescription() == VALIDATE_TOOLTIP
    assert action.isEnabled()
    assert button.isEnabled()
    # Every menu and submenu. PySide 6.9 invalidates QAction.menu() wrappers.
    for menu in window.findChildren(QMenu):
        assert action not in menu.actions(), menu.objectName()


def test_an_icon_only_validate_button_is_named_validate(qtbot) -> None:
    window = _show(qtbot)
    _editors(window)[0].setPlainText("{}")
    button = _button(window)
    width = window.width()
    while (
        button.toolButtonStyle() != Qt.ToolButtonStyle.ToolButtonIconOnly
        and width > window.minimumWidth()
    ):
        width -= 20
        window.resize(width, window.height())
        QApplication.processEvents()
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert button.toolTip() == "Validate"
    assert button.accessibleName() == "Validate"
    assert button.accessibleDescription() == VALIDATE_TOOLTIP


def test_status_copy_is_exact() -> None:
    assert [VALID_MESSAGE.format(label=label) for label in ("JSON", "YAML", "XML")] == [
        "Valid JSON.",
        "Valid YAML.",
        "Valid XML.",
    ]
    assert VALIDATE_STATUS_MS == 4000
    assert VALIDATE_NOT_AVAILABLE == "Validate works on JSON, YAML and XML."
    assert VALIDATE_EMPTY == "Input is empty."
    assert VALIDATING_MESSAGE == "Validating…"
    assert VALIDATE_CANCELLED_MESSAGE == "Validating canceled."


def test_validate_is_greyed_out_while_input_is_empty_like_format(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    action = _validate(window)
    button = _labelled_button(qtbot, window)
    assert not action.isEnabled()
    assert not window._actions.format.isEnabled()
    assert action.toolTip() == VALIDATE_EMPTY
    assert button.toolTip() == VALIDATE_EMPTY
    assert button.accessibleDescription() == VALIDATE_EMPTY
    source.setPlainText("{}")
    assert action.isEnabled()
    assert action.toolTip() == VALIDATE_TOOLTIP
    assert button.toolTip() == VALIDATE_TOOLTIP
    assert button.accessibleDescription() == VALIDATE_TOOLTIP
    source.setPlainText("  \n")
    assert not action.isEnabled()
    assert not window._actions.format.isEnabled()
    assert button.toolTip() == VALIDATE_EMPTY


def test_validate_is_greyed_out_for_html(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText("<p>a</p>\n")
    _input_format(window).setCurrentText("HTML")
    action = _validate(window)
    button = _labelled_button(qtbot, window)
    assert not action.isEnabled()
    assert not button.isEnabled()
    assert action.toolTip() == VALIDATE_NOT_AVAILABLE
    assert button.toolTip() == VALIDATE_NOT_AVAILABLE
    assert button.accessibleDescription() == VALIDATE_NOT_AVAILABLE
    window._validate()
    assert not window._formatting

    for label in ("JSON", "YAML", "XML"):
        _input_format(window).setCurrentText(label)
        assert action.isEnabled(), label
        assert button.toolTip() == VALIDATE_TOOLTIP
        assert button.accessibleDescription() == VALIDATE_TOOLTIP


# --- Valid Input ----------------------------------------------------------------


@pytest.mark.parametrize("label", ["JSON", "YAML", "XML"])
def test_valid_input_says_so_with_a_check_and_leaves_output(qtbot, label) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    picker = _child(window, QComboBox, "outputFormat")
    _input_format(window).setCurrentText(label)
    picked = picker.currentText()
    output.setPlainText("previous\n")
    output.insertPlainText("typed")
    before = _output_state(output)
    source.setPlainText(_VALID[label])
    source.document().clearUndoRedoStacks()

    _run(qtbot, window)

    expected = f"Valid {label}."
    assert _status(window) == expected
    assert _icon(window).isVisible()
    assert window._status.last_announcement() == expected
    assert _output_state(output) == before
    assert picker.currentText() == picked
    assert source.toPlainText() == _VALID[label]
    assert not source.document().isUndoAvailable()
    assert not output.isReadOnly() and not source.isReadOnly()


def test_valid_status_shows_for_four_seconds_then_ready(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    _run(qtbot, window)
    timer = window._validate_status_timer
    assert timer.isActive()
    assert timer.interval() == 4000
    timer.stop()
    timer.timeout.emit()
    assert _status(window) == "Ready"
    assert not _icon(window).isVisible()


@pytest.mark.parametrize("state", [STATE_OPEN, STATE_CLOSED, STATE_MINIMIZED])
def test_valid_input_clears_problems_and_keeps_the_pane_state(qtbot, state) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    pane = _child(window, ProblemsPane, "problemsPane")
    window._set_problems_state(state)
    source.setPlainText(_INVALID)
    _run(qtbot, window)
    assert pane.count() == 1
    assert source.problem_lines() == {2}
    source.setPlainText(_PRETTY)
    _run(qtbot, window)
    assert pane.count() == 0
    assert source.problem_lines() == set()
    assert window._problems_state == state
    assert _status(window) == "Valid JSON."


def test_validate_never_marks_output_stale(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_PRETTY)
    _run(qtbot, window, window._actions.format)
    formatted = _output_state(output)
    _run(qtbot, window)
    assert _output_state(output) == formatted
    labels = [
        widget.text()
        for widget in window._output_pane.findChildren(QLabel)
        if widget.isVisible()
    ]
    assert not any("Stale" in text for text in labels)


def test_validate_checks_input_whatever_pane_has_focus(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText(_PRETTY)
    source.setPlainText(_INVALID)
    output.setFocus()
    qtbot.waitUntil(output.hasFocus)
    _run(qtbot, window)
    pane = _child(window, ProblemsPane, "problemsPane")
    assert pane.messages() == ["Missing comma at the end of line 2."]
    assert output.toPlainText() == _PRETTY


# --- Invalid Input --------------------------------------------------------------


def _problem_view(window) -> tuple[object, ...]:
    source, _output = _editors(window)
    pane = _child(window, ProblemsPane, "problemsPane")
    return (
        pane.messages(),
        [(row.lineno, row.colno) for row in pane.shown()],
        source.problem_lines(),
        _status(window),
        pane.isVisible(),
        window._problems_state,
        window._status.last_announcement(),
    )


@pytest.mark.parametrize("label", ["JSON", "YAML", "XML"])
@pytest.mark.parametrize("state", [STATE_OPEN, STATE_CLOSED])
def test_invalid_input_shows_formats_problems(qtbot, label, state) -> None:
    """Rows, places, gutter markers, status and announcement match Format's."""
    window = _show(qtbot)
    source, output = _editors(window)
    window._set_problems_state(state)
    _input_format(window).setCurrentText(label)
    source.setPlainText(_INVALID_BY_FORMAT[label])
    output.setPlainText("previous\n")
    before = _output_state(output)
    _run(qtbot, window, window._actions.format)
    formatted = _problem_view(window)
    assert formatted[0], "Format found no problems"
    window._clear_problems()
    window._status.set_state("Ready")
    window._status.announce("")

    _run(qtbot, window)

    assert _problem_view(window) == formatted
    assert _output_state(output) == before
    assert not _icon(window).isVisible()


def test_repeated_keys_are_problems_like_format(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText('{\n  "a": 1,\n  "a": 2\n}\n')
    _run(qtbot, window)
    pane = _child(window, ProblemsPane, "problemsPane")
    assert pane.count() == 1
    assert any('"a"' in message for message in pane.messages())
    assert _status(window) == "1 problem"
    assert output.toPlainText() == "previous\n"


# --- Output and Sort keys pickers ----------------------------------------------


def test_validate_ignores_the_output_and_sort_keys_pickers(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    sent: list[dict[str, object]] = []
    submit = window._session.submit

    def spy(*args: object, **kwargs: object) -> None:
        sent.append({"args": args, **kwargs})
        submit(*args, **kwargs)

    monkeypatch.setattr(window._session, "submit", spy)
    _input_format(window).setCurrentText("YAML")
    picker = _child(window, QComboBox, "outputFormat")
    picker.setCurrentText("JSON")
    window._actions.sort_keys.setChecked(True)
    # Valid YAML that Format can't write as JSON: the key is not a string.
    source.setPlainText("? [1, 2]\n: pair\n")
    output.setPlainText("previous\n")
    _run(qtbot, window)
    assert sent[0]["args"][1:] == ("YAML", "YAML")
    assert sent[0]["validate"] is True
    assert sent[0].get("sort_keys", False) is False
    assert _status(window) == "Valid YAML."
    assert picker.currentText() == "JSON"
    assert window._actions.sort_keys.isChecked()
    assert output.toPlainText() == "previous\n"


# --- Format's size path -----------------------------------------------------------


def _size_box(window) -> QMessageBox:
    box = window.findChild(QMessageBox, "sizeWarning")
    assert isinstance(box, QMessageBox)
    return box


def _button_named(box: QMessageBox, text: str) -> QPushButton:
    matches = [button for button in box.buttons() if button.text() == text]
    assert len(matches) == 1, text
    button = matches[0]
    assert isinstance(button, QPushButton)
    return button


def test_validate_asks_formats_size_question_in_its_words(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(_PRETTY)
    _validate(window).trigger()
    box = _size_box(window)
    assert box.text() == f"Validate this {len(_PRETTY.encode())} B document?"
    assert box.informativeText() == (
        "Large documents can take a while to validate. You can cancel while it runs."
    )
    anyway = _button_named(box, "Validate anyway")
    _button_named(box, "Cancel")
    assert box.defaultButton() is anyway
    assert not window._formatting
    anyway.click()
    qtbot.waitUntil(lambda: _status(window) == "Valid JSON.", timeout=_WAIT_MS)
    assert output.toPlainText() == "previous\n"


def test_validate_size_question_defaults_to_formats_default(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, _ = _editors(window)
    source.setPlainText(_PRETTY)
    window._actions.format.trigger()
    format_box = _size_box(window)
    format_default = format_box.buttonRole(format_box.defaultButton())
    _button_named(format_box, "Cancel").click()
    qtbot.waitUntil(lambda: window.findChild(QMessageBox, "sizeWarning") is None)

    _validate(window).trigger()
    validate_box = _size_box(window)
    assert validate_box.buttonRole(validate_box.defaultButton()) == format_default


def test_cancel_on_the_size_question_leaves_both_panes(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(_PRETTY)
    _validate(window).trigger()
    _button_named(_size_box(window), "Cancel").click()
    qtbot.waitUntil(lambda: window._size_warning is None)
    assert not window._formatting
    assert source.toPlainText() == _PRETTY
    assert output.toPlainText() == "previous\n"
    assert not window._size_warning_accepted


def test_validate_keeps_formats_hard_size_limit(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "_format_limit_bytes", lambda source: 10)
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(_PRETTY)
    _run(qtbot, window, window._actions.format)
    refused = _status(window)
    assert refused.startswith("This document is too large to format")
    window._status.set_state("Ready")
    _validate(window).trigger()
    assert not window._formatting
    assert _status(window) == refused
    assert output.toPlainText() == "previous\n"


# --- Busy state ------------------------------------------------------------------


def _hold(monkeypatch, window) -> None:
    """Keep the job running without sending it to the worker, and look busy."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        main_window_module, "_busy_progress_bytes", lambda source, text: (1, 10**9)
    )


def test_a_busy_validate_shows_on_validate_not_format(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, output = _editors(window)
    output.setPlainText("keep\n")
    source.setPlainText(_PRETTY)
    format_button = _child(window, QPushButton, "formatButton")
    idle_text = format_button.text()

    _validate(window).trigger()

    assert window._formatting
    assert _status(window) == VALIDATING_MESSAGE
    assert format_button.text() == idle_text
    assert not format_button.isEnabled()
    assert format_button.cursor().shape() != Qt.CursorShape.BusyCursor
    button = _labelled_button(qtbot, window)
    assert button.text() == "Validate"
    assert button.cursor().shape() == Qt.CursorShape.BusyCursor
    assert button.toolTip() == VALIDATE_BUSY_TOOLTIP
    assert button.accessibleDescription() == VALIDATE_BUSY_DESCRIPTION
    assert not _validate(window).isEnabled()
    assert not window._actions.format.isEnabled()
    assert not window._actions.minify.isEnabled()

    sent: list[object] = []
    window._session.submit = lambda *args, **kwargs: sent.append(args)  # type: ignore[method-assign]
    window._validate()
    window._format()
    window._minify()
    assert sent == []

    window._cancel_format()
    assert not window._formatting
    assert button.cursor().shape() != Qt.CursorShape.BusyCursor
    assert button.toolTip() == VALIDATE_TOOLTIP
    assert button.accessibleDescription() == VALIDATE_TOOLTIP
    assert _validate(window).isEnabled()
    assert format_button.isEnabled()
    assert output.toPlainText() == "keep\n"


def test_a_long_validate_counts_up_in_its_own_words(qtbot, monkeypatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(main_window_module, "_monotonic", lambda: clock["now"])
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    _validate(window).trigger()
    clock["now"] += 12.0
    window._apply_deferred_reveal()
    assert _status(window) == "Validating… 0:12"
    window._cancel_format()


def test_esc_cancels_a_validate_for_three_seconds(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, output = _editors(window)
    output.setPlainText("keep\n")
    before = _output_state(output)
    source.setPlainText(_PRETTY)
    _validate(window).trigger()
    window._apply_deferred_reveal()
    assert window._formatting
    qtbot.keyClick(source, Qt.Key.Key_Escape)
    assert not window._formatting
    assert _output_state(output) == before
    assert not source.isReadOnly()
    assert _status(window) == VALIDATE_CANCELLED_MESSAGE
    timer = window._cancel_status_timer
    assert timer.isActive()
    assert timer.interval() == 3000


def test_validate_runs_on_the_worker_like_format(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    sent: list[dict[str, object]] = []
    submit = window._session.submit

    def spy(*args: object, **kwargs: object) -> None:
        sent.append({"args": args, **kwargs})
        submit(*args, **kwargs)

    monkeypatch.setattr(window._session, "submit", spy)
    source.setPlainText(_PRETTY)
    _run(qtbot, window)
    assert len(sent) == 1
    assert sent[0]["validate"] is True
    assert sent[0]["args"] == (_PRETTY, "JSON", "JSON")


# --- The check mark in light and dark ---------------------------------------------


def _pick(qtbot, window, label: str) -> None:
    menu = _child(window, QMenu, "menuAppearance")
    actions = {action.text(): action for action in menu.actions()}
    actions[label].trigger()
    want_dark = label == "Dark"
    qtbot.waitUntil(
        lambda: (
            (QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128)
            == want_dark
        ),
        timeout=5000,
    )
    QApplication.processEvents()


def _ink(label: QLabel) -> QColor:
    """The check mark's full-strength stroke color."""
    image = label.pixmap().toImage()
    inks = {
        image.pixelColor(x, y).rgb()
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() == 255
    }
    assert inks, "the check mark draws no solid pixels"
    background = _surface(label)
    return max(
        (QColor(ink) for ink in inks),
        key=lambda color: contrast_ratio(color, background),
    )


def _surface(widget: QWidget) -> QColor:
    return widget.palette().color(QPalette.ColorRole.Window)


def test_the_check_mark_meets_text_contrast_in_light_and_dark(qtbot) -> None:
    """The ✓ is drawn in the status text's palette role, redrawn per scheme."""
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    seen: dict[str, str] = {}
    for label in ("Light", "Dark"):
        _pick(qtbot, window, label)
        _run(qtbot, window)
        icon = _icon(window)
        assert icon.isVisible()
        ink = _ink(icon)
        ratio = contrast_ratio(ink, _surface(icon))
        assert ratio >= TEXT_MIN_CONTRAST, f"{label} check mark: {ratio:.2f}"
        muted = QApplication.palette().color(QPalette.ColorRole.PlaceholderText)
        assert ink.name() == muted.name(), label
        seen[label] = ink.name()
    assert seen["Light"] != seen["Dark"]


def test_the_check_mark_follows_a_scheme_change_while_shown(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    _pick(qtbot, window, "Light")
    _run(qtbot, window)
    light = _ink(_icon(window)).name()
    _pick(qtbot, window, "Dark")
    qtbot.waitUntil(lambda: _ink(_icon(window)).name() != light, timeout=5000)
    ratio = contrast_ratio(_ink(_icon(window)), _surface(_icon(window)))
    assert ratio >= TEXT_MIN_CONTRAST


# --- Perf ------------------------------------------------------------------------

_FIXTURE_BYTES = 2_400_000


@pytest.mark.perf
def test_validate_on_2_4_mb_is_timed(qtbot) -> None:
    """Time Validate on the 2.4 MB pretty JSON fixture, core and window.

    Provisional: the medians are printed with no budget. Infra sets the
    budget under #152. Core is what the worker runs. Window is the trigger
    until the status says the JSON is valid. Output is never touched.
    """
    text, _rows = text_of_size(_FIXTURE_BYTES, pretty_json)
    size = len(text.encode("utf-8"))
    assert _FIXTURE_BYTES <= size < 2_500_000

    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    before = _output_state(output)
    source.setPlainText(text)

    def reset() -> None:
        window._status.set_state("Ready")

    def window_validate() -> None:
        _validate(window).trigger()
        qtbot.waitUntil(
            lambda: not window._formatting and _status(window) == "Valid JSON.",
            timeout=60_000,
        )

    timings = {
        "core": time_runs(lambda: validate_document(text, "json")),
        "window": time_runs(window_validate, setup=reset),
    }
    lines = [
        f"Validate timings on {size:,} B pretty JSON, fake customer rows "
        f"({sys.platform}; provisional, no budget yet, #152)"
    ]
    for label, timing in timings.items():
        samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
        lines.append(f"  {label}: median {timing.median * 1000:.1f} ms ({samples})")
    print("\n".join(lines), flush=True)
    assert _output_state(output) == before
