"""No shortcut uses Ctrl+Alt on Windows or Linux (#95).

Windows treats AltGr as Ctrl+Alt. A German layout types ``[``, ``]``, ``{``,
``}``, ``@``, ``\\`` and ``€`` with AltGr, so a Ctrl+Alt shortcut would fire
while someone types. Ctrl+Alt is ⌘⌥ on macOS, which has no AltGr.
"""

import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window

CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier

# Fold and Unfold keep ⌥⌘[ and ⌥⌘] on macOS, because ⇧⌘[ and ⇧⌘] switch
# tabs there. Find and Replace is ⌥⌘F on macOS and Ctrl+H elsewhere.
MAC_CTRL_ALT = {"Fold", "Unfold", "Find and Replace…"}

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

# Probe actions for the control test: (shortcut, the key press that hits it).
CONTROL_PROBES = [("Ctrl+Alt+E", Qt.Key.Key_E), ("Ctrl+Alt+[", "[")]


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _altgr(qtbot, widget, key: Qt.Key | str) -> None:
    """Press ``key`` with Ctrl+Alt, as Windows reports an AltGr press.

    qtbot.keyClick goes through Qt's shortcut matching before the widget
    sees the key. A one-character str also sets that character as the text.
    """
    qtbot.keyClick(widget, key, CTRL_ALT)


def _char_key(char: str) -> Qt.Key | str | None:
    """The press that types ``char``. QTest cannot send € as text."""
    if char == "µ":
        return Qt.Key.Key_micro
    return char if char.isascii() else None


def _fired(window) -> list[str]:
    fired: list[str] = []
    for action in window.findChildren(QAction):
        label = action.text() or action.objectName()
        action.triggered.connect(lambda _=False, label=label: fired.append(label))
    return fired


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


# PySide names 0xB5 by its deprecated Key_mu alias when it sends Key_micro.
@pytest.mark.filterwarnings("ignore:Enum value .*Key_mu:DeprecationWarning")
@pytest.mark.parametrize(("key", "char"), GERMAN_ALTGR)
def test_altgr_types_into_input_without_firing_a_command(qtbot, key, char) -> None:
    if sys.platform == "darwin" and char in "[]":
        pytest.skip("macOS has no AltGr; ⌥⌘[ and ⌥⌘] are Fold and Unfold")
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    qtbot.waitUntil(editor.hasFocus)
    fired = _fired(window)

    # Windows may report the physical key or the character's key code.
    # QTest types the physical key's own character, such as 8 or e.
    _altgr(qtbot, editor, key)
    assert fired == []
    assert len(editor.toPlainText()) == 1
    char_key = _char_key(char)
    if char_key is not None:
        _altgr(qtbot, editor, char_key)
        assert fired == []
        assert editor.toPlainText()[1:] == char


@pytest.mark.parametrize(("shortcut", "key"), CONTROL_PROBES)
def test_a_ctrl_alt_shortcut_does_fire_through_the_same_press(
    qtbot, shortcut, key
) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    qtbot.waitUntil(editor.hasFocus)
    probe = QAction("Probe", window)
    probe.setShortcut(QKeySequence(shortcut, QKeySequence.SequenceFormat.PortableText))
    window.addAction(probe)
    fired = _fired(window)

    _altgr(qtbot, editor, key)

    assert fired == ["Probe"]
    assert editor.toPlainText() == ""
