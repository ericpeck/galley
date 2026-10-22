"""Sort keys toolbar toggle (#59): sorts the next Format's output only."""

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QToolButton

from galley.app.actions import (
    COMING_SOON,
    UNBUILT_ACTIONS,
    is_unbuilt,
    main,
    release_failure,
)
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import NOT_YET_MESSAGE, SORTED_KEYS_SUFFIX
from galley.app.parse_session import ParseSession
from galley.core.format import format_json

_SPAWN_TIMEOUT_MS = 20000
_UNSORTED = '{"b": 1, "a": {"d": 2, "c": [3, 1]}}'


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _parts(window) -> tuple[CodeEditor, CodeEditor, QLabel]:
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert isinstance(status, QLabel)
    return editor, output, status


def _format_and_wait(qtbot, window, output: CodeEditor) -> None:
    output.setPlainText("")
    window._actions.format.trigger()
    qtbot.waitUntil(
        lambda: output.toPlainText() != "" and not window._formatting,
        timeout=_SPAWN_TIMEOUT_MS,
    )


def _sort_button(window) -> QToolButton:
    buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is window._actions.sort_keys
    ]
    assert len(buttons) == 1
    return buttons[0]


def test_sort_keys_is_built_and_off_by_default(qtbot) -> None:
    window = _show(qtbot)
    action = window._actions.sort_keys
    assert "Sort keys" not in UNBUILT_ACTIONS
    assert not is_unbuilt(action)
    assert action.isEnabled()
    assert action.isCheckable()
    assert not action.isChecked()
    assert action.toolTip() != COMING_SOON
    assert action not in window._actions.stubs()
    button = _sort_button(window)
    assert button.isEnabled()
    assert button.toolTip() != COMING_SOON
    host = button.parentWidget()
    assert host is not None
    assert host.property("comingSoonHost") is not True


def test_toolbar_button_shows_the_checked_state(qtbot) -> None:
    window = _show(qtbot)
    button = _sort_button(window)
    assert button.isCheckable()
    assert not button.isChecked()
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert window._actions.sort_keys.isChecked()
    assert button.isChecked()
    window._actions.sort_keys.setChecked(False)
    assert not button.isChecked()


def test_toggling_does_not_change_output_or_say_not_yet(qtbot) -> None:
    window = _show(qtbot)
    editor, output, status = _parts(window)
    editor.setPlainText(_UNSORTED)
    output.setPlainText("kept")
    window._actions.sort_keys.trigger()
    assert window._actions.sort_keys.isChecked()
    assert output.toPlainText() == "kept"
    assert editor.toPlainText() == _UNSORTED
    assert window.statusBar().currentMessage() != NOT_YET_MESSAGE
    assert status.text() != NOT_YET_MESSAGE


def test_format_sorts_output_and_says_so_when_on(qtbot) -> None:
    window = _show(qtbot)
    editor, output, status = _parts(window)
    editor.setPlainText(_UNSORTED)
    window._actions.sort_keys.setChecked(True)
    _format_and_wait(qtbot, window, output)
    assert output.toPlainText() == format_json(_UNSORTED, sort_keys=True)
    assert list(json.loads(output.toPlainText())) == ["a", "b"]
    assert status.text() == f"Valid JSON {SORTED_KEYS_SUFFIX}"
    assert status.text() == "Valid JSON · keys sorted A–Z"
    assert editor.toPlainText() == _UNSORTED

    window._actions.sort_keys.setChecked(False)
    _format_and_wait(qtbot, window, output)
    assert output.toPlainText() == format_json(_UNSORTED)
    assert list(json.loads(output.toPlainText())) == ["b", "a"]
    assert status.text() == "Valid JSON"
    assert editor.toPlainText() == _UNSORTED


def test_duplicate_keys_still_report_when_sorting(qtbot) -> None:
    window = _show(qtbot)
    editor, output, status = _parts(window)
    editor.setPlainText('{"b": 1, "a": 2, "b": 3}')
    window._actions.sort_keys.setChecked(True)
    window._actions.format.trigger()
    qtbot.waitUntil(lambda: status.text() == "1 problem", timeout=_SPAWN_TIMEOUT_MS)
    assert SORTED_KEYS_SUFFIX not in status.text()


def test_session_cancel_works_for_a_sorted_job(qtbot, tmp_path: Path) -> None:
    destination = tmp_path / "out.json"
    original = b'{"keep": true}\n'
    destination.write_bytes(original)
    session = ParseSession()
    session.start()
    results: list[str] = []
    session.succeeded.connect(results.append)
    try:
        qtbot.waitUntil(
            lambda: session.process is not None and session.process.pid is not None,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        blob = json.dumps({"z": "x" * 80000, "a": 1})
        session.submit(
            blob,
            "json",
            "json",
            destination=str(destination),
            chunk_size=4096,
            chunk_pause_s=0.2,
            sort_keys=True,
        )
        qtbot.waitUntil(
            lambda: session.total > 0 and 0 < session.written < session.total,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        session.cancel()
        qtbot.waitUntil(lambda: session.reaped_exitcode is not None, timeout=10000)
        assert destination.read_bytes() == original
        assert sorted(tmp_path.glob(".galley-*")) == []
        session.submit(_UNSORTED, "json", "json", sort_keys=True)
        qtbot.waitUntil(lambda: bool(results), timeout=_SPAWN_TIMEOUT_MS)
        assert results == [format_json(_UNSORTED, sort_keys=True)]
    finally:
        session.shutdown()


def test_release_check_no_longer_lists_sort_keys(capsys) -> None:
    message = release_failure()
    assert message is not None
    assert "Sort keys" not in message
    assert main() == 1
    assert "Sort keys" not in capsys.readouterr().out
