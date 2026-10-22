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

Fold and Unfold differ by platform. On Windows and Linux they are
``Ctrl+Shift+[`` and ``Ctrl+Shift+]``, as in VS Code. macOS keeps ⌥⌘[ and
⌥⌘] (portable ``Ctrl+Alt+[``), because ⇧⌘[ and ⇧⌘] switch tabs there.
Collapse all is the chord ``Ctrl+K, Ctrl+0`` (⌘K ⌘0) and Expand all
``Ctrl+K, Ctrl+J`` (⌘K ⌘J). Actual Size is ``Ctrl+0`` on its own, so the
chord does not clash with it.
"""

import sys

from PySide6.QtGui import QAction, QKeySequence

_PORTABLE = QKeySequence.SequenceFormat.PortableText
# ⇧⌘[ and ⇧⌘] switch tabs on macOS, so only macOS keeps ⌥⌘.
_FOLD_MODIFIERS = "Ctrl+Alt+" if sys.platform == "darwin" else "Ctrl+Shift+"

WORD_WRAP = QKeySequence("Alt+Z", _PORTABLE)
CLOSE_OTHERS = QKeySequence("Ctrl+K, Ctrl+Shift+W", _PORTABLE)
ESCAPE_AS_STRING = QKeySequence("Ctrl+Shift+E", _PORTABLE)
UNESCAPE_STRING = QKeySequence("Ctrl+K, Ctrl+E", _PORTABLE)
SHOW_WHITESPACE = QKeySequence("Ctrl+Shift+8", _PORTABLE)
ACTUAL_SIZE = QKeySequence("Ctrl+0", _PORTABLE)
FORMAT = QKeySequence("Ctrl+Return", _PORTABLE)
SHOW_PROBLEMS = QKeySequence("Ctrl+Shift+M", _PORTABLE)
STACK_PANES = QKeySequence("Ctrl+\\", _PORTABLE)
FOLD = QKeySequence(_FOLD_MODIFIERS + "[", _PORTABLE)
UNFOLD = QKeySequence(_FOLD_MODIFIERS + "]", _PORTABLE)
COLLAPSE_ALL = QKeySequence("Ctrl+K, Ctrl+0", _PORTABLE)
EXPAND_ALL = QKeySequence("Ctrl+K, Ctrl+J", _PORTABLE)


def native_chord_text(sequence: QKeySequence) -> str:
    """``sequence`` as the platform shows it, chord keys apart: ``⌘K ⌘0``."""
    return " ".join(
        QKeySequence(sequence[index]).toString(QKeySequence.SequenceFormat.NativeText)
        for index in range(sequence.count())
    )


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
