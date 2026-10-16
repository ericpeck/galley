"""Keyboard shortcuts for the Galley shell.

Standard commands use ``QKeySequence.StandardKey`` so Qt maps Command on
macOS to Control on Windows and Linux.

Other commands use portable shortcut text. In that format ``Ctrl`` means
Command on macOS and Control on Windows and Linux.

Word Wrap is portable ``Ctrl+Alt+W`` (⌥⌘W on macOS, Ctrl+Alt+W on Windows).
Close Others is portable ``Ctrl+Alt+Shift+W`` (⇧⌥⌘W on macOS,
Ctrl+Alt+Shift+W on Windows). The two sequences are different, so Word Wrap
keeps Ctrl+Alt+W.
"""

from PySide6.QtGui import QAction, QKeySequence

_PORTABLE = QKeySequence.SequenceFormat.PortableText

WORD_WRAP = QKeySequence("Ctrl+Alt+W", _PORTABLE)
CLOSE_OTHERS = QKeySequence("Ctrl+Alt+Shift+W", _PORTABLE)
ESCAPE_AS_STRING = QKeySequence("Ctrl+Shift+E", _PORTABLE)
UNESCAPE_STRING = QKeySequence("Ctrl+Alt+Shift+E", _PORTABLE)
SHOW_WHITESPACE = QKeySequence("Ctrl+Shift+8", _PORTABLE)
ACTUAL_SIZE = QKeySequence("Ctrl+0", _PORTABLE)
FORMAT = QKeySequence("Ctrl+Return", _PORTABLE)


def apply_standard(action: QAction, key: QKeySequence.StandardKey) -> None:
    """Assign every platform binding Qt provides for ``key``.

    Call this after a ``QApplication`` exists so the bindings match the
    platform the process is running on.
    """
    bindings = [
        sequence for sequence in QKeySequence.keyBindings(key) if not sequence.isEmpty()
    ]
    if bindings:
        action.setShortcuts(bindings)
        return
    action.setShortcut(QKeySequence(key))
