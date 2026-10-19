"""Busy indicator, progress bar with Cancel, and the large-file warning."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
)

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import (
    CANCELED_STATUS_MS,
    CANCELLED_MESSAGE,
    ELAPSED_STATUS_MS,
    FORMATTING_MESSAGE,
    JSON_BUSY_BYTES,
    JSON_PROGRESS_BYTES,
    JSON_WARNING_BYTES,
    PROGRESS_BAR_REVEAL_MS,
    PROGRESS_WIDTH_PX,
    SPINNER_REVEAL_MS,
    SPINNER_WIDTH_PX,
    YAML_BUSY_BYTES,
    YAML_PROGRESS_BYTES,
    YAML_WARNING_BYTES,
    si_size,
)

_FORMATTED = '{\n  "a": 1\n}\n'


def test_si_size_uses_decimal_units() -> None:
    assert si_size(0) == "0 B"
    assert si_size(999) == "999 B"
    assert si_size(YAML_BUSY_BYTES) == "192 KB"
    assert si_size(YAML_WARNING_BYTES) == "352 KB"
    assert si_size(YAML_PROGRESS_BYTES) == "704 KB"
    assert si_size(1_200_000) == "1.2 MB"
    assert si_size(JSON_BUSY_BYTES) == "5 MB"
    assert si_size(JSON_PROGRESS_BYTES) == "20 MB"
    assert si_size(JSON_WARNING_BYTES) == "100 MB"


def test_fast_run_shows_no_indicator(qtbot, monkeypatch) -> None:
    clock = {"now": 50.0}
    monkeypatch.setattr("galley.app.main_window._monotonic", lambda: clock["now"])
    window = _show(qtbot)
    editor, output, status, spinner, bar, cancel, _button = _parts(window)
    editor.setPlainText('{"a": 1}')
    try:
        window._actions.format.trigger()
        assert not spinner.isVisible()
        assert not bar.isVisible()
        assert not cancel.isVisible()

        def _done() -> bool:
            assert not spinner.isVisible()
            assert not bar.isVisible()
            assert not cancel.isVisible()
            return output.toPlainText() == _FORMATTED

        qtbot.waitUntil(_done, timeout=15000)
        assert status.text() == "Valid JSON"
        assert not editor.isReadOnly()
    finally:
        window.shutdown_parse_worker()


def test_busy_indicator_appears_at_500_ms(qtbot, monkeypatch) -> None:
    window, clock = _held(qtbot, monkeypatch)
    editor, _output, status, spinner, bar, cancel, _button = _parts(window)
    editor.setPlainText('{"a": 1}')
    started = 80.0
    clock["now"] = started
    window._actions.format.trigger()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    clock["now"] = started + (SPINNER_REVEAL_MS - 1) / 1000
    window._apply_deferred_reveal()
    assert not spinner.isVisible()
    clock["now"] = started + SPINNER_REVEAL_MS / 1000
    window._apply_deferred_reveal()
    assert spinner.isVisible()
    assert spinner.width() == SPINNER_WIDTH_PX
    assert spinner.minimum() == 0 and spinner.maximum() == 0
    assert spinner.accessibleName() == "Formatting"
    assert not spinner.isTextVisible()
    assert not bar.isVisible()
    assert not cancel.isVisible()
    assert status.text() == FORMATTING_MESSAGE
    assert editor.isReadOnly()
    assert not window._actions.format.isEnabled()
    window.shutdown_parse_worker()


def test_progress_bar_and_cancel_appear_at_2_s(qtbot, monkeypatch) -> None:
    window, clock = _held(qtbot, monkeypatch)
    editor, _output, status, spinner, bar, cancel, _button = _parts(window)
    editor.setPlainText('{"a": 1}')
    started = 90.0
    clock["now"] = started
    window._actions.format.trigger()
    clock["now"] = started + (PROGRESS_BAR_REVEAL_MS - 1) / 1000
    window._apply_deferred_reveal()
    assert spinner.isVisible()
    assert not bar.isVisible()
    assert not cancel.isVisible()
    clock["now"] = started + PROGRESS_BAR_REVEAL_MS / 1000
    window._apply_deferred_reveal()
    assert bar.isVisible()
    assert not spinner.isVisible()
    assert cancel.isVisible()
    assert bar.width() == PROGRESS_WIDTH_PX
    assert bar.minimum() == 0 and bar.maximum() == 0
    assert not bar.isTextVisible()
    assert bar.accessibleName() == "Formatting"
    assert status.text() == FORMATTING_MESSAGE
    assert "%" not in status.text()
    clock["now"] = started + (ELAPSED_STATUS_MS - 1) / 1000
    window._apply_deferred_reveal()
    assert status.text() == FORMATTING_MESSAGE
    clock["now"] = started + 12
    window._apply_deferred_reveal()
    assert status.text() == "Formatting… 0:12"
    assert editor.isReadOnly()
    window.shutdown_parse_worker()


def test_size_limit_shows_the_indicator_immediately(qtbot, monkeypatch) -> None:
    window, clock = _held(qtbot, monkeypatch)
    editor, _output, status, spinner, bar, cancel, _button = _parts(window)
    _as_yaml(window)
    clock["now"] = 10.0
    editor.setPlainText("a" * (YAML_BUSY_BYTES - 1))
    window._actions.format.trigger()
    assert not spinner.isVisible()
    assert not bar.isVisible()
    assert not cancel.isVisible()
    assert _visible_warning(window) is None
    window._cancel_format()

    editor.setPlainText("a" * YAML_BUSY_BYTES)
    window._actions.format.trigger()
    assert spinner.isVisible()
    assert not bar.isVisible()
    assert not cancel.isVisible()
    assert status.text() == FORMATTING_MESSAGE
    assert _visible_warning(window) is None
    window._cancel_format()

    editor.setPlainText("a" * YAML_PROGRESS_BYTES)
    window._actions.format.trigger()
    warning = _visible_warning(window)
    assert warning is not None
    assert not window._formatting
    warning.defaultButton().click()
    qtbot.waitUntil(lambda: window._formatting)
    assert bar.isVisible()
    assert cancel.isVisible()
    assert not spinner.isVisible()
    assert status.text() == FORMATTING_MESSAGE
    window._cancel_format()

    _as_json(window)
    editor.setPlainText("x")
    editor.toPlainText = lambda: "x" * JSON_BUSY_BYTES  # type: ignore[method-assign]
    window._actions.format.trigger()
    assert spinner.isVisible()
    assert not bar.isVisible()
    assert _visible_warning(window) is None
    window._cancel_format()

    editor.toPlainText = lambda: "x" * JSON_PROGRESS_BYTES  # type: ignore[method-assign]
    window._actions.format.trigger()
    assert bar.isVisible()
    assert cancel.isVisible()
    assert not spinner.isVisible()
    assert _visible_warning(window) is None
    window.shutdown_parse_worker()


def test_cancel_discards_the_result_and_shows_the_status(qtbot, monkeypatch) -> None:
    window, clock = _held(qtbot, monkeypatch)
    editor, output, status, _spinner, bar, cancel, _button = _parts(window)
    editor.setPlainText('{"a": 1}')
    output.setPlainText("keep\n")
    started = 30.0
    clock["now"] = started
    window._actions.format.trigger()
    clock["now"] = started + PROGRESS_BAR_REVEAL_MS / 1000
    window._apply_deferred_reveal()
    assert cancel.isVisible()
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert not window._formatting
    assert output.toPlainText() == "keep\n"
    assert editor.toPlainText() == '{"a": 1}'
    assert not editor.isReadOnly()
    assert not output.isReadOnly()
    assert status.text() == CANCELLED_MESSAGE
    assert window._cancel_status_timer.isActive()
    assert window._cancel_status_timer.interval() == CANCELED_STATUS_MS
    assert not bar.isVisible()
    window._session.finished_job_id = 1
    window._accepted_job_id = 1
    window._session.succeeded.emit("CHANGED\n")
    assert output.toPlainText() == "keep\n"
    qtbot.waitUntil(lambda: status.text() == "Ready", timeout=5000)
    window._actions.format.trigger()
    clock["now"] = window._format_started_s + PROGRESS_BAR_REVEAL_MS / 1000
    window._apply_deferred_reveal()
    cancel.click()
    assert output.toPlainText() == "keep\n"
    assert status.text() == CANCELLED_MESSAGE
    window.shutdown_parse_worker()


def test_success_is_one_undo_step(qtbot) -> None:
    window = _show(qtbot)
    editor, output, status, _spinner, _bar, _cancel, _button = _parts(window)
    editor.setPlainText('{"a": 1}')
    output.setPlainText("old\n")
    try:
        window._actions.format.trigger()
        qtbot.waitUntil(lambda: output.toPlainText() == _FORMATTED, timeout=15000)
        assert status.text() == "Valid JSON"
        assert output.document().isUndoAvailable()
        output.undo()
        assert output.toPlainText() == "old\n"
        assert not output.document().isUndoAvailable()
        output.redo()
        assert output.toPlainText() == _FORMATTED
    finally:
        window.shutdown_parse_worker()


def test_older_result_does_not_overwrite_a_newer_one(qtbot, monkeypatch) -> None:
    window, _clock = _held(qtbot, monkeypatch)
    editor, output, _status, _spinner, _bar, _cancel, _button = _parts(window)
    editor.setPlainText('{"a": 1}')
    output.setPlainText("base\n")
    window._actions.format.trigger()
    window._formatting = True
    window._accepted_job_id = 2
    window._set_editors_locked(False)
    window._session.finished_job_id = 1
    window._session.succeeded.emit("OLD\n")
    assert output.toPlainText() == "base\n"
    window._session.finished_job_id = 2
    window._session.succeeded.emit("NEW\n")
    assert output.toPlainText() == "NEW\n"
    assert not window._formatting
    window._formatting = True
    window._accepted_job_id = 3
    window._session.finished_job_id = 2
    window._session.succeeded.emit("OLD\n")
    assert output.toPlainText() == "NEW\n"
    window._session.finished_job_id = 3
    window._session.succeeded.emit("NEWER\n")
    assert output.toPlainText() == "NEWER\n"
    window.shutdown_parse_worker()


def test_size_warning_asks_once_and_cancel_aborts(qtbot, monkeypatch) -> None:
    window, _clock = _held(qtbot, monkeypatch)
    editor, _output, _status, spinner, _bar, _cancel, _button = _parts(window)
    _as_yaml(window)
    editor.setPlainText("y" * (YAML_WARNING_BYTES - 1))
    window._actions.format.trigger()
    assert _visible_warning(window) is None
    assert spinner.isVisible()
    window._cancel_format()

    editor.setPlainText("y" * YAML_WARNING_BYTES)
    window._actions.format.trigger()
    warning = _visible_warning(window)
    assert warning is not None
    assert warning.windowModality() == Qt.WindowModality.WindowModal
    assert warning.text() == "This document is 352 KB."
    anyway = warning.defaultButton()
    assert anyway is not None
    assert anyway.text() == "Format anyway"
    assert warning.buttonRole(anyway) == QMessageBox.ButtonRole.AcceptRole
    cancel = next(button for button in warning.buttons() if button.text() == "Cancel")
    assert warning.buttonRole(cancel) == QMessageBox.ButtonRole.RejectRole
    assert not window._formatting
    cancel.click()
    qtbot.waitUntil(lambda: _visible_warning(window) is None)
    assert not window._formatting

    window._actions.format.trigger()
    again = _visible_warning(window)
    assert again is not None
    again.defaultButton().click()
    qtbot.waitUntil(lambda: window._formatting)
    assert _visible_warning(window) is None
    window._cancel_format()

    window._actions.format.trigger()
    assert _visible_warning(window) is None
    assert window._formatting
    window.shutdown_parse_worker()


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _held(qtbot, monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("galley.app.main_window._monotonic", lambda: clock["now"])
    window = _show(qtbot)
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    return window, clock


def _parts(window):
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    bar = window.findChild(QProgressBar, "writeProgress")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert isinstance(status, QLabel)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(bar, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    return editor, output, status, spinner, bar, cancel, button


def _as_yaml(window) -> None:
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    combo.setCurrentText("YAML")


def _as_json(window) -> None:
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    combo.setCurrentText("JSON")


def _visible_warning(window) -> QMessageBox | None:
    visible = [
        box
        for box in window.findChildren(QMessageBox)
        if box.isVisible() and box.objectName() == "sizeWarning"
    ]
    if not visible:
        return None
    assert len(visible) == 1
    return visible[0]
