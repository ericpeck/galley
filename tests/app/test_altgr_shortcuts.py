"""No shortcut uses Ctrl+Alt on Windows or Linux (#95).

Windows treats AltGr as Ctrl+Alt. A German layout types ``[``, ``]``, ``{``,
``}``, ``@``, ``\\`` and ``€`` with AltGr, so a Ctrl+Alt shortcut would fire
while someone types. Ctrl+Alt is ⌘⌥ on macOS, which has no AltGr.
"""

import sys

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window

CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier

# Fold and Unfold keep ⌥⌘[ and ⌥⌘] on macOS, because ⇧⌘[ and ⇧⌘] switch
# tabs there.
MAC_CTRL_ALT = {"Fold", "Unfold"}

# German layout: (physical key, character AltGr types on it).
GERMAN_ALTGR = [
    (Qt.Key.Key_7, "{"),
    (Qt.Key.Key_8, "["),
    (Qt.Key.Key_9, "]"),
    (Qt.Key.Key_0, "}"),
    (Qt.Key.Key_Q, "@"),
    (Qt.Key.Key_ssharp, "\\"),
    (Qt.Key.Key_E, "€"),
    (Qt.Key.Key_Plus, "~"),
    (Qt.Key.Key_Less, "|"),
    (Qt.Key.Key_M, "µ"),
]


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _altgr(widget, key: Qt.Key, char: str) -> None:
    # QTest.sendKeyEvent passes a one-character text through as a C char,
    # which mangles € and µ. sendEvent still runs the shortcut map first.
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(kind, key, CTRL_ALT, char))


def _ctrl_alt_owners(window) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for action in window.findChildren(QAction):
        for sequence in action.shortcuts():
            for index in range(sequence.count()):
                modifiers = sequence[index].keyboardModifiers()
                if modifiers & CTRL_ALT == CTRL_ALT:
                    label = action.text() or action.objectName()
                    rendered = sequence.toString(
                        QKeySequence.SequenceFormat.PortableText
                    )
                    owners.setdefault(label, []).append(rendered)
                    break
    return owners


def test_no_shortcut_uses_ctrl_alt_on_windows_or_linux(qtbot) -> None:
    window = _show(qtbot)
    owners = _ctrl_alt_owners(window)
    if sys.platform == "darwin":
        assert set(owners) <= MAC_CTRL_ALT, owners
    else:
        assert owners == {}


@pytest.mark.parametrize(("key", "char"), GERMAN_ALTGR)
def test_altgr_types_into_input_without_firing_a_command(qtbot, key, char) -> None:
    if sys.platform == "darwin" and char in "[]":
        pytest.skip("macOS has no AltGr; ⌥⌘[ and ⌥⌘] are Fold and Unfold")
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    qtbot.waitUntil(editor.hasFocus)
    fired: list[str] = []
    for action in window.findChildren(QAction):
        label = action.text() or action.objectName()
        action.triggered.connect(lambda _=False, label=label: fired.append(label))

    # Windows may report the physical key or the character's key code.
    _altgr(editor, key, char)
    _altgr(editor, QKeySequence(char)[0].key(), char)

    assert fired == []
    assert editor.toPlainText() == char * 2
