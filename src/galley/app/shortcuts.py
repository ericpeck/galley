"""Keyboard shortcuts for the Galley shell.

Standard commands use ``QKeySequence.StandardKey`` so Qt maps Command on
macOS to Control on Windows and Linux.

Other commands use portable shortcut text. In that format ``Ctrl`` means
Command on macOS and Control on Windows and Linux.

Windows treats AltGr as Ctrl+Alt, so no shortcut uses Ctrl+Alt on Windows or
Linux. On a German layout AltGr types ``[``, ``]``, ``{``, ``}``, ``@``, ``\\``
and ``€``, and a Ctrl+Alt shortcut would fire while someone types those.
Ctrl+Alt is fine on macOS, where it is ⌘⌥ and there is no AltGr.

Word Wrap is portable ``Alt+Z`` (⌥Z on macOS, Alt+Z on Windows), as in
VS Code. Qt maps Alt to Option on macOS, so one sequence covers both.
Close Others is the chord ``Ctrl+K, Ctrl+Shift+W`` (⌘K ⇧⌘W on macOS) and
Unescape String is ``Ctrl+K, Ctrl+E`` (⌘K ⌘E). Unescape pairs with Escape as
String, portable ``Ctrl+Shift+E`` (⇧⌘E). Show Problems is portable
``Ctrl+Shift+M`` (⇧⌘M on macOS, Ctrl+Shift+M on Windows). Stack Panes is
portable ``Ctrl+\\`` (⌘\\ on macOS, Ctrl+\\ on Windows).
"""

from PySide6.QtGui import QAction, QKeySequence

_PORTABLE = QKeySequence.SequenceFormat.PortableText

WORD_WRAP = QKeySequence("Alt+Z", _PORTABLE)
CLOSE_OTHERS = QKeySequence("Ctrl+K, Ctrl+Shift+W", _PORTABLE)
ESCAPE_AS_STRING = QKeySequence("Ctrl+Shift+E", _PORTABLE)
UNESCAPE_STRING = QKeySequence("Ctrl+K, Ctrl+E", _PORTABLE)
SHOW_WHITESPACE = QKeySequence("Ctrl+Shift+8", _PORTABLE)
ACTUAL_SIZE = QKeySequence("Ctrl+0", _PORTABLE)
FORMAT = QKeySequence("Ctrl+Return", _PORTABLE)
SHOW_PROBLEMS = QKeySequence("Ctrl+Shift+M", _PORTABLE)
STACK_PANES = QKeySequence("Ctrl+\\", _PORTABLE)


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
