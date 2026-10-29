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
Unescape String is the chord ``Ctrl+K, Ctrl+E`` (⌘K ⌘E on macOS). It
pairs with Escape as String, portable ``Ctrl+Shift+E`` (⇧⌘E). Show Problems
is portable ``Ctrl+Shift+M`` (⇧⌘M on macOS, Ctrl+Shift+M on Windows). Stack
Panes is portable ``Ctrl+\\`` (⌘\\ on macOS, Ctrl+\\ on Windows), with
``Ctrl+Shift+L`` (⇧⌘L) as a second key: ``\\`` needs AltGr on German
layouts. The menu shows the first.

Fold and Unfold differ by platform. On Windows and Linux they are
``Ctrl+Shift+[`` and ``Ctrl+Shift+]``, as in VS Code. macOS keeps ⌥⌘[ and
⌥⌘] (portable ``Ctrl+Alt+[``), because ⇧⌘[ and ⇧⌘] switch tabs there.
Collapse all is the chord ``Ctrl+K, Ctrl+0`` (⌘K ⌘0) and Expand all
``Ctrl+K, Ctrl+J`` (⌘K ⌘J). Actual Size is ``Ctrl+0`` on its own, so the
chord does not clash with it.

Indent is portable ``Ctrl+]`` and Outdent ``Ctrl+[`` (⌘] and ⌘[ on macOS,
Ctrl+] and Ctrl+[ on Windows), as in VS Code. Fold and Unfold add Alt on
macOS and Shift elsewhere, so neither clashes.

Find and Replace differs by platform and is not Qt's standard Replace key,
which is ⌘H on macOS, the system Hide. macOS uses ⌥⌘F (portable
``Ctrl+Alt+F``), as the Edit menu shows. Windows and Linux use ``Ctrl+H``,
which keeps Ctrl+Alt off them.

The find bars' Regex and Whole word toggles work only while a find bar has
focus. macOS uses ⌥⌘R and ⌥⌘W (portable ``Ctrl+Alt+R`` and ``Ctrl+Alt+W``).
Windows and Linux use ``Alt+R`` and ``Alt+W``, which keeps Ctrl+Alt off
them. No menu title has a mnemonic, so Alt+R and Alt+W open no menu.

Next Problem is F8 and Previous Problem Shift+F8 on every platform (Bill's
#78 spec). On macOS F8 is Play/Pause unless Fn is held, so macOS lists ⌘'
and ⇧⌘' first, which the menu shows, and keeps F8 and ⇧F8 as second keys.

Compare is portable ``Ctrl+Shift+D`` (⇧⌘D on macOS, Ctrl+Shift+D on
Windows). Next Change is F7 and Previous Change Shift+F7 on every platform,
never F8, which is Next Problem's. F7 is a media key on Mac keyboards, so
macOS lists ⌥⌘↓ and ⌥⌘↑ (portable ``Ctrl+Alt+Down`` and ``Ctrl+Alt+Up``)
first, which the menu shows. Windows and Linux get F7 alone, which keeps
Ctrl+Alt off them.
"""

import sys

from PySide6.QtGui import QAction, QKeySequence

_PORTABLE = QKeySequence.SequenceFormat.PortableText
# ⇧⌘[ and ⇧⌘] switch tabs on macOS, so only macOS keeps ⌥⌘.
_FOLD_MODIFIERS = "Ctrl+Alt+" if sys.platform == "darwin" else "Ctrl+Shift+"

WORD_WRAP = QKeySequence("Alt+Z", _PORTABLE)
ESCAPE_AS_STRING = QKeySequence("Ctrl+Shift+E", _PORTABLE)
UNESCAPE_STRING = QKeySequence("Ctrl+K, Ctrl+E", _PORTABLE)
SHOW_WHITESPACE = QKeySequence("Ctrl+Shift+8", _PORTABLE)
ACTUAL_SIZE = QKeySequence("Ctrl+0", _PORTABLE)
FORMAT = QKeySequence("Ctrl+Return", _PORTABLE)
SHOW_PROBLEMS = QKeySequence("Ctrl+Shift+M", _PORTABLE)
STACK_PANES = QKeySequence("Ctrl+\\", _PORTABLE)
STACK_PANES_ALT = QKeySequence("Ctrl+Shift+L", _PORTABLE)
FOLD = QKeySequence(_FOLD_MODIFIERS + "[", _PORTABLE)
UNFOLD = QKeySequence(_FOLD_MODIFIERS + "]", _PORTABLE)
COLLAPSE_ALL = QKeySequence("Ctrl+K, Ctrl+0", _PORTABLE)
INDENT = QKeySequence("Ctrl+]", _PORTABLE)
OUTDENT = QKeySequence("Ctrl+[", _PORTABLE)
EXPAND_ALL = QKeySequence("Ctrl+K, Ctrl+J", _PORTABLE)
FIND_AND_REPLACE = QKeySequence(
    "Ctrl+Alt+F" if sys.platform == "darwin" else "Ctrl+H", _PORTABLE
)
_FIND_TOGGLE_MODIFIERS = "Ctrl+Alt+" if sys.platform == "darwin" else "Alt+"
TOGGLE_REGEX = QKeySequence(_FIND_TOGGLE_MODIFIERS + "R", _PORTABLE)
TOGGLE_WHOLE_WORD = QKeySequence(_FIND_TOGGLE_MODIFIERS + "W", _PORTABLE)
# The first key of each list is the one the menu shows.
_PROBLEM_KEYS = ("Ctrl+'", "F8") if sys.platform == "darwin" else ("F8",)
NEXT_PROBLEM = tuple(QKeySequence(key, _PORTABLE) for key in _PROBLEM_KEYS)
PREVIOUS_PROBLEM = tuple(
    QKeySequence(f"Shift+{key}", _PORTABLE) for key in _PROBLEM_KEYS
)
COMPARE = QKeySequence("Ctrl+Shift+D", _PORTABLE)
_NEXT_CHANGE_KEYS = ("Ctrl+Alt+Down", "F7") if sys.platform == "darwin" else ("F7",)
_PREVIOUS_CHANGE_KEYS = (
    ("Ctrl+Alt+Up", "Shift+F7") if sys.platform == "darwin" else ("Shift+F7",)
)
NEXT_CHANGE = tuple(QKeySequence(key, _PORTABLE) for key in _NEXT_CHANGE_KEYS)
PREVIOUS_CHANGE = tuple(QKeySequence(key, _PORTABLE) for key in _PREVIOUS_CHANGE_KEYS)


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
