"""Minify writes compact JSON to Output and never changes Input."""

import io
import json
import sys
from contextlib import redirect_stdout

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
)
from tests.core.mask_rows import pretty_json, text_of_size
from tests.perf import time_runs

import galley.app.main_window as main_window_module
from galley.app.actions import (
    MINIFY_TOOLTIP,
    UNBUILT_ACTIONS,
    is_unbuilt,
    main,
    release_failure,
)
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import (
    MINIFIED_MESSAGE,
    MINIFY_BUSY_DESCRIPTION,
    MINIFY_BUSY_TOOLTIP,
    MINIFY_CANCELLED_MESSAGE,
    MINIFY_FAILED_MESSAGE,
    MINIFY_NOT_AVAILABLE,
    MINIFY_STATUS_MS,
    MINIFYING_MESSAGE,
)
from galley.app.parse_session import ParseSession
from galley.app.problems_pane import ProblemsPane
from galley.app.problems_settings import STATE_CLOSED, STATE_MINIMIZED, STATE_OPEN
from galley.core.format import format_document

_PRETTY = '{\n  "name": "Ann",\n  "tags": [\n    "a",\n    "b"\n  ],\n  "n": 1.5\n}\n'
_COMPACT = '{"name":"Ann","tags":["a","b"],"n":1.5}'
_INVALID = '{\n  "a": 1\n  "b": 2\n}\n'
_WAIT_MS = 15_000


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


def _minify(window) -> QAction:
    return window._actions.minify


def _button(window) -> QToolButton:
    for button in window.findChildren(QToolButton):
        if button.defaultAction() is _minify(window):
            return button
    raise AssertionError("no toolbar button for Minify")


def _run(qtbot, window) -> None:
    """Trigger Minify and wait for the job to finish."""
    _minify(window).trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)


# --- Built, copy and keys ---------------------------------------------------


def test_minify_is_built_and_off_the_release_lists(qtbot) -> None:
    window = _show(qtbot)
    action = _minify(window)
    actions = window._actions
    assert "Minify" not in UNBUILT_ACTIONS
    assert not is_unbuilt(action)
    assert action not in actions._stub_actions()
    assert action not in actions._not_yet_actions()
    assert action not in actions.stubs()
    message = release_failure()
    assert message is not None
    assert "Minify" not in message
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
        "Find and Replace….\n"
        "Build them or remove them from _not_yet_actions() in actions.py "
        "before tagging.\n"
    )
    host = _button(window).parentWidget()
    assert host is None or host.property("comingSoonHost") is not True


def test_label_tooltip_and_no_shortcut(qtbot) -> None:
    window = _show(qtbot)
    action = _minify(window)
    _editors(window)[0].setPlainText("{}")
    assert action.text() == "Minify"
    assert action.shortcut() == QKeySequence()
    assert action.shortcuts() == []
    assert MINIFY_TOOLTIP == "Remove all spaces and line breaks from the JSON"
    assert action.toolTip() == MINIFY_TOOLTIP
    assert _button(window).toolTip() == MINIFY_TOOLTIP
    assert action.isEnabled()
    assert _button(window).isEnabled()


def test_status_copy_is_exact() -> None:
    assert MINIFIED_MESSAGE == "Minified JSON."
    assert MINIFY_FAILED_MESSAGE == "Can't minify: the JSON has errors. See Problems."
    assert MINIFY_NOT_AVAILABLE == "Minify works on JSON."
    assert MINIFY_STATUS_MS == 4000


def test_minify_is_greyed_out_while_input_is_empty_like_format(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    action = _minify(window)
    assert not action.isEnabled()
    assert not window._actions.format.isEnabled()
    assert action.toolTip() == MINIFY_TOOLTIP
    source.setPlainText("{}")
    assert action.isEnabled()
    source.setPlainText("  \n")
    assert not action.isEnabled()


# --- Valid JSON ---------------------------------------------------------------


def test_minify_writes_compact_json_and_keeps_input(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(_PRETTY)
    source.document().clearUndoRedoStacks()

    _run(qtbot, window)

    assert output.toPlainText() == _COMPACT
    assert source.toPlainText() == _PRETTY
    assert not source.document().isUndoAvailable()
    assert _status(window) == MINIFIED_MESSAGE
    output.undo()
    assert output.toPlainText() == "previous\n"
    output.redo()
    assert output.toPlainText() == _COMPACT


def test_minified_status_shows_for_four_seconds(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    _run(qtbot, window)
    timer = window._minify_status_timer
    assert timer.isActive()
    assert timer.interval() == 4000
    timer.stop()
    timer.timeout.emit()
    assert _status(window) == "Ready"


def test_minify_clears_earlier_problems(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    pane = _child(window, ProblemsPane, "problemsPane")
    source.setPlainText(_INVALID)
    _run(qtbot, window)
    assert pane.count() == 1
    source.setPlainText(_PRETTY)
    _run(qtbot, window)
    assert pane.count() == 0
    assert output.toPlainText() == _COMPACT


def test_minify_writes_input_key_order_with_sort_keys_on(qtbot) -> None:
    """Bill: Sort keys is for Format; Minify keeps Input's key order."""
    window = _show(qtbot)
    source, output = _editors(window)
    window._actions.sort_keys.setChecked(True)
    source.setPlainText('{"b": 1, "a": 2}')
    _run(qtbot, window)
    assert output.toPlainText() == '{"b":1,"a":2}'
    assert _status(window) == MINIFIED_MESSAGE


def test_minify_shows_json_in_output(qtbot) -> None:
    """Bill: Output as YAML switches to JSON, as picking JSON by hand does.

    The window's remembered choice, the colors and Copy As all follow it.
    """
    window = _show(qtbot)
    source, output = _editors(window)
    picker = _child(window, QComboBox, "outputFormat")
    picker.setCurrentText("YAML")
    output.setPlainText("email: pat@example.com\nn: 1\n")
    assert window._last_value_output == "YAML"
    assert output.syntax_mode() == "YAML"
    source.setPlainText('{\n  "email": "pat@example.com",\n  "n": 1\n}\n')
    _run(qtbot, window)
    assert picker.currentText() == "JSON"
    assert window._output_pane.format_name() == "JSON"
    assert window._last_value_output == "JSON"
    assert output.syntax_mode() == "JSON"
    assert output.toPlainText() == '{"email":"pat@example.com","n":1}'
    assert _status(window) == MINIFIED_MESSAGE

    window._actions.copy_as_masked.trigger()
    masked = QApplication.clipboard().text()
    assert json.loads(masked) == {"email": "•••", "n": 1}

    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    _child(window, QComboBox, "inputFormat").setCurrentText("JSON")
    assert picker.currentText() == "JSON"


def test_failed_minify_leaves_the_output_picker_alone(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    picker = _child(window, QComboBox, "outputFormat")
    picker.setCurrentText("YAML")
    source.setPlainText(_INVALID)
    _run(qtbot, window)
    assert picker.currentText() == "YAML"


def test_minify_matches_format_then_compact(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    text = '{"unicode": "café 日本語", "emoji": "😀 \\ud83d\\ude00", "q": "\\"\\\\"}'
    source.setPlainText(text)
    _run(qtbot, window)
    assert output.toPlainText() == format_document(text, "json", "json", minify=True)
    assert output.toPlainText() == (
        '{"unicode":"café 日本語","emoji":"😀 😀","q":"\\"\\\\"}'
    )


# --- Invalid JSON -------------------------------------------------------------


def test_invalid_json_opens_problems_and_leaves_output(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(_INVALID)

    _run(qtbot, window)

    pane = _child(window, ProblemsPane, "problemsPane")
    assert pane.messages() == ["Missing comma at the end of line 2."]
    assert pane.isVisible()
    assert window._problems_state == STATE_OPEN
    assert _status(window) == MINIFY_FAILED_MESSAGE
    assert output.toPlainText() == "previous\n"
    assert source.toPlainText() == _INVALID

    timer = window._minify_status_timer
    assert timer.isActive()
    assert timer.interval() == 4000
    timer.stop()
    timer.timeout.emit()
    assert _status(window) == "1 problem"


@pytest.mark.parametrize("state", [STATE_CLOSED, STATE_MINIMIZED])
def test_invalid_json_opens_a_closed_or_minimized_problems_pane(qtbot, state) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    window._set_problems_state(state)
    source.setPlainText(_INVALID)
    _run(qtbot, window)
    pane = _child(window, ProblemsPane, "problemsPane")
    assert window._problems_state == STATE_OPEN
    assert pane.isVisible()
    assert window._actions.show_problems.isChecked()
    assert _status(window) == MINIFY_FAILED_MESSAGE


def test_repeated_keys_are_problems_like_format(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText('{\n  "a": 1,\n  "a": 2\n}\n')
    _run(qtbot, window)
    pane = _child(window, ProblemsPane, "problemsPane")
    assert pane.count() >= 1
    assert any('"a"' in message for message in pane.messages())
    assert _status(window) == MINIFY_FAILED_MESSAGE
    assert output.toPlainText() == "previous\n"


# --- Other formats -------------------------------------------------------------


@pytest.mark.parametrize("label", ["YAML", "XML", "HTML"])
def test_minify_is_greyed_out_for_other_formats(qtbot, label: str) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText("a: 1\n")
    _child(window, QComboBox, "inputFormat").setCurrentText(label)
    action = _minify(window)
    button = _button(window)
    assert not action.isEnabled()
    assert not button.isEnabled()
    assert action.toolTip() == MINIFY_NOT_AVAILABLE
    assert button.toolTip() == MINIFY_NOT_AVAILABLE
    assert button.accessibleDescription() == MINIFY_NOT_AVAILABLE

    _child(window, QComboBox, "inputFormat").setCurrentText("JSON")
    assert action.isEnabled()
    assert action.toolTip() == MINIFY_TOOLTIP
    assert button.toolTip() == MINIFY_TOOLTIP
    assert button.accessibleDescription() == ""


def test_an_opened_yaml_file_greys_out_minify(qtbot, tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    window = _show(qtbot)
    source, _output = _editors(window)
    window.open_path(path)
    qtbot.waitUntil(
        lambda: window._loading_path is None and source.toPlainText() == "a: 1\n"
    )
    assert _child(window, QComboBox, "inputFormat").currentText() == "YAML"
    assert not _minify(window).isEnabled()
    assert _button(window).toolTip() == MINIFY_NOT_AVAILABLE


def test_minify_ignores_a_trigger_for_other_formats(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    source.setPlainText("a: 1\n")
    output.setPlainText("previous\n")
    window._minify()
    assert not window._formatting
    assert output.toPlainText() == "previous\n"


# --- Format's size path ---------------------------------------------------------


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


def test_minify_asks_formats_size_question_in_minify_words(qtbot, monkeypatch) -> None:
    """Bill: the same sheet and the same answer as Format, Minify words."""
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_PRETTY)
    _minify(window).trigger()
    box = _size_box(window)
    assert box.text() == f"Minify this {len(_PRETTY.encode())} B document?"
    assert box.informativeText() == (
        "Large documents can take a while to minify. You can cancel while it runs."
    )
    anyway = _button_named(box, "Minify anyway")
    _button_named(box, "Cancel")
    assert box.defaultButton() is anyway
    assert not window._formatting
    anyway.click()
    qtbot.waitUntil(lambda: output.toPlainText() == _COMPACT, timeout=_WAIT_MS)
    assert _status(window) == MINIFIED_MESSAGE


def test_minify_size_question_defaults_to_formats_default(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, _ = _editors(window)
    source.setPlainText(_PRETTY)
    window._actions.format.trigger()
    format_box = _size_box(window)
    format_default = format_box.buttonRole(format_box.defaultButton())
    _button_named(format_box, "Cancel").click()
    qtbot.waitUntil(lambda: window.findChild(QMessageBox, "sizeWarning") is None)

    _minify(window).trigger()
    minify_box = _size_box(window)
    assert minify_box.buttonRole(minify_box.defaultButton()) == format_default


def test_cancel_on_the_size_question_leaves_both_panes(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(_PRETTY)
    _minify(window).trigger()
    _button_named(_size_box(window), "Cancel").click()
    qtbot.waitUntil(lambda: window._size_warning is None)
    assert not window._formatting
    assert source.toPlainText() == _PRETTY
    assert output.toPlainText() == "previous\n"
    assert not window._size_warning_accepted


@pytest.mark.parametrize("first", ["Minify", "Format"])
def test_one_accepted_size_answer_covers_format_and_minify(
    qtbot, monkeypatch, first: str
) -> None:
    monkeypatch.setattr(main_window_module, "_warning_bytes", lambda source: 10)
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_PRETTY)
    actions = window._actions
    first_action, then = (
        (actions.minify, actions.format)
        if first == "Minify"
        else (actions.format, actions.minify)
    )
    first_action.trigger()
    box = _size_box(window)
    box.defaultButton().click()
    qtbot.waitUntil(lambda: output.toPlainText() != "", timeout=_WAIT_MS)
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)
    qtbot.waitUntil(lambda: window.findChild(QMessageBox, "sizeWarning") is None)

    then.trigger()
    assert window.findChild(QMessageBox, "sizeWarning") is None
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)
    expected = (
        _COMPACT if then is actions.minify else format_document(_PRETTY, "json", "json")
    )
    assert output.toPlainText() == expected


# --- Busy state ------------------------------------------------------------------


def _hold(monkeypatch, window) -> None:
    """Keep the job running without sending it to the worker, and look busy."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        main_window_module, "_busy_progress_bytes", lambda source, text: (1, 10**9)
    )


def test_a_busy_minify_shows_on_minify_not_format(qtbot, monkeypatch) -> None:
    """Bill: over 5 MB the busy state is Minify's and the status says so."""
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, output = _editors(window)
    source.setPlainText(_PRETTY)
    format_button = _child(window, QPushButton, "formatButton")
    idle_text = format_button.text()

    _minify(window).trigger()

    assert window._formatting
    assert _status(window) == MINIFYING_MESSAGE == "Minifying…"
    assert format_button.text() == idle_text
    assert format_button.text() != "Formatting…"
    assert not format_button.isEnabled()
    assert format_button.cursor().shape() != Qt.CursorShape.BusyCursor
    button = _button(window)
    assert button.text() == "Minify"
    assert button.cursor().shape() == Qt.CursorShape.BusyCursor
    assert button.toolTip() == MINIFY_BUSY_TOOLTIP
    assert button.accessibleDescription() == MINIFY_BUSY_DESCRIPTION
    assert not _minify(window).isEnabled()
    assert not window._actions.format.isEnabled()

    sent: list[object] = []
    window._session.submit = lambda *args, **kwargs: sent.append(args)  # type: ignore[method-assign]
    window._minify()
    window._format()
    assert sent == []

    window._cancel_format()
    assert not window._formatting
    assert button.cursor().shape() != Qt.CursorShape.BusyCursor
    assert button.toolTip() == MINIFY_TOOLTIP
    assert button.accessibleDescription() == ""
    assert _minify(window).isEnabled()
    assert format_button.isEnabled()
    assert output.toPlainText() == ""


def test_a_long_minify_counts_up_in_its_own_words(qtbot, monkeypatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(main_window_module, "_monotonic", lambda: clock["now"])
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    _minify(window).trigger()
    clock["now"] += 12.0
    window._apply_deferred_reveal()
    assert _status(window) == "Minifying… 0:12"
    window._cancel_format()


def test_esc_cancels_a_minify_like_format(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, output = _editors(window)
    output.setPlainText("keep\n")
    source.setPlainText(_PRETTY)
    _minify(window).trigger()
    window._apply_deferred_reveal()
    assert window._formatting
    qtbot.keyClick(source, Qt.Key.Key_Escape)
    assert not window._formatting
    assert output.toPlainText() == "keep\n"
    assert source.toPlainText() == _PRETTY
    assert not source.isReadOnly()
    assert MINIFY_CANCELLED_MESSAGE == "Minifying canceled."
    assert _status(window) == MINIFY_CANCELLED_MESSAGE
    assert window._cancel_status_timer.isActive()


def test_a_format_job_keeps_formats_busy_face(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, _output = _editors(window)
    source.setPlainText(_PRETTY)
    window._actions.format.trigger()
    format_button = _child(window, QPushButton, "formatButton")
    assert format_button.text() == "Formatting…"
    assert _status(window) == "Formatting…"
    button = _button(window)
    assert button.cursor().shape() != Qt.CursorShape.BusyCursor
    assert button.accessibleDescription() == ""
    assert not _minify(window).isEnabled()
    window._cancel_format()
    assert _status(window) == "Formatting canceled."


def test_minify_runs_on_the_worker_like_format(qtbot, monkeypatch) -> None:
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
    assert sent[0]["minify"] is True
    assert sent[0]["args"][1:] == ("JSON", "JSON")


# --- Perf ------------------------------------------------------------------------

_FIXTURE_BYTES = 2_400_000


@pytest.mark.perf
def test_minify_on_2_4_mb_is_timed(qtbot) -> None:
    """Time Minify on the 2.4 MB pretty JSON fixture, core and window.

    Provisional: the medians are printed with no budget. Infra sets the
    budget under #152. Core is what the worker runs. Window is the trigger
    until Output holds the compact text.
    """
    text, _rows = text_of_size(_FIXTURE_BYTES, pretty_json)
    size = len(text.encode("utf-8"))
    assert _FIXTURE_BYTES <= size < 2_500_000
    expected = format_document(text, "json", "json", minify=True)

    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(text)

    def clear() -> None:
        output.setPlainText("")

    def window_minify() -> None:
        _minify(window).trigger()
        qtbot.waitUntil(
            lambda: not window._formatting and output.toPlainText() == expected,
            timeout=60_000,
        )

    timings = {
        "core": time_runs(lambda: format_document(text, "json", "json", minify=True)),
        "window": time_runs(window_minify, setup=clear),
    }
    lines = [
        f"Minify timings on {size:,} B pretty JSON, fake customer rows "
        f"({sys.platform}; provisional, no budget yet, #152)"
    ]
    for label, timing in timings.items():
        samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
        lines.append(f"  {label}: median {timing.median * 1000:.1f} ms ({samples})")
    print("\n".join(lines), flush=True)
    assert output.toPlainText() == expected
