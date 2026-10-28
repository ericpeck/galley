"""Every Ctrl+K chord runs from the keyboard wherever focus is (#95).

The keys go to the focused widget as real key events, so the editor's
ShortcutOverride and Qt's standard keys get their chance to take Ctrl+K
first. This runs on every platform, so Windows CI checks it too.

Tests run offscreen, and the offscreen platform uses Qt's Windows keyboard
scheme on every OS. X11's Ctrl+K (DeleteEndOfLine) never applies here.
"""

import warnings

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problems_pane import ProblemsPane

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier

# Command: second key of the Ctrl+K chord.
CHORDS = {
    "Unescape String": (Qt.Key.Key_E, CTRL),
    "Collapse all": (Qt.Key.Key_0, CTRL),
    "Expand all": (Qt.Key.Key_J, CTRL),
}

_VALID = '{"name": "galley", "owner": {"id": 7}, "tags": [1, 2]}'
_INVALID = '{"name": "galley",,}'


def _action(window, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _formatted(qtbot, text: str):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1100, 700)
    window.show()
    qtbot.waitExposed(window)
    source = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(source, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert isinstance(pane, ProblemsPane)
    source.setPlainText(text)
    format_action = _action(window, "Format")
    format_action.trigger()
    if text == _VALID:
        qtbot.waitUntil(lambda: output.toPlainText() != "", timeout=15000)
    else:
        qtbot.waitUntil(pane.isVisible, timeout=15000)
    qtbot.waitUntil(format_action.isEnabled, timeout=15000)
    return window, source, output, pane


def _probe(window) -> list[str]:
    """Record every action that fires, with the command's own work cut off.

    The test checks key dispatch only. Collapse all needs foldable Output,
    so all three are switched on.
    """
    fired: list[str] = []
    for action in window.findChildren(QAction):
        label = action.text() or action.objectName()
        if label in CHORDS:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    action.triggered.disconnect()
                except (RuntimeError, TypeError):
                    pass
            action.setEnabled(True)
        action.triggered.connect(lambda _=False, label=label: fired.append(label))
    return fired


@pytest.mark.parametrize("command", list(CHORDS))
@pytest.mark.parametrize("target", ["input", "output", "problems"])
def test_ctrl_k_chord_fires_from_the_focused_widget(qtbot, target, command) -> None:
    text = _INVALID if target == "problems" else _VALID
    window, source, output, pane = _formatted(qtbot, text)
    focus: QWidget = {
        "input": source,
        "output": output,
        "problems": pane.list_widget(),
    }[target]
    for editor in (source, output):
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        editor.setTextCursor(cursor)
    texts = (source.toPlainText(), output.toPlainText())
    fired = _probe(window)
    window.activateWindow()
    focus.setFocus()
    qtbot.waitUntil(focus.hasFocus, timeout=2000)

    key, modifiers = CHORDS[command]
    qtbot.keyClick(focus, Qt.Key.Key_K, CTRL)
    qtbot.keyClick(focus, key, modifiers)

    assert fired == [command]
    # The caret is at the start of a non-empty line, so a Ctrl+K taken as
    # Delete to end of line, or a typed k, would change the text.
    assert (source.toPlainText(), output.toPlainText()) == texts


@pytest.mark.parametrize("target", ["input", "output", "problems"])
def test_old_close_others_chord_does_nothing(qtbot, target) -> None:
    """Tabs are removed, so Ctrl+K, Ctrl+Shift+W runs nothing and types nothing."""
    text = _INVALID if target == "problems" else _VALID
    window, source, output, pane = _formatted(qtbot, text)
    focus: QWidget = {
        "input": source,
        "output": output,
        "problems": pane.list_widget(),
    }[target]
    for editor in (source, output):
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        editor.setTextCursor(cursor)
    texts = (source.toPlainText(), output.toPlainText())
    fired: list[str] = []
    for action in window.findChildren(QAction):
        label = action.text() or action.objectName()
        action.triggered.connect(lambda _=False, label=label: fired.append(label))
    window.activateWindow()
    focus.setFocus()
    qtbot.waitUntil(focus.hasFocus, timeout=2000)

    qtbot.keyClick(focus, Qt.Key.Key_K, CTRL)
    qtbot.keyClick(focus, Qt.Key.Key_W, CTRL | SHIFT)

    assert fired == []
    assert (source.toPlainText(), output.toPlainText()) == texts


@pytest.mark.parametrize("target", ["input", "output"])
def test_collapse_and_expand_all_chords_fold_output(qtbot, target) -> None:
    window, source, output, _pane = _formatted(qtbot, _VALID)
    focus = source if target == "input" else output
    texts = (source.toPlainText(), output.toPlainText())
    window.activateWindow()
    focus.setFocus()
    qtbot.waitUntil(focus.hasFocus, timeout=2000)
    assert output.folded_lines() == set()

    qtbot.keyClick(focus, Qt.Key.Key_K, CTRL)
    qtbot.keyClick(focus, Qt.Key.Key_0, CTRL)
    collapsed = output.folded_lines()
    # Collapse all folds every block below the root: "owner" on line 3 and
    # "tags" on line 6.
    assert collapsed == {3, 6}

    qtbot.keyClick(focus, Qt.Key.Key_K, CTRL)
    qtbot.keyClick(focus, Qt.Key.Key_J, CTRL)
    assert output.folded_lines() == set()
    assert (source.toPlainText(), output.toPlainText()) == texts
