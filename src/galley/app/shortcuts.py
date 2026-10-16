"""Keyboard shortcuts for the Galley shell.

Standard commands use ``QKeySequence.StandardKey`` so Qt maps Command on
macOS to Control on Windows and Linux.

Other commands use portable shortcut text. In that format ``Ctrl`` means
Command on macOS and Control on Windows and Linux.

Word Wrap is portable ``Ctrl+Alt+W`` (⌥⌘W on macOS, Ctrl+Alt+W elsewhere).
Close Others is portable ``Ctrl+Alt+Shift+W`` (⇧⌥⌘W on macOS,
Ctrl+Alt+Shift+W elsewhere). The two sequences are different.

The spec also lists a Windows Close Others binding of Ctrl+Alt+W and a
proposed Windows Word Wrap binding of Alt+Z. Those strings are a later swap
in this module: Ctrl+Alt+W is already Word Wrap under the portable mapping,
and Alt+Z is still marked as a proposal. One table keeps every shortcut
distinct without a per-platform branch.
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
