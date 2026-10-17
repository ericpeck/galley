"""Menu and toolbar actions for the shell.

Shortcut-bearing commands that Qt already knows use standard keys. The
remaining spec shortcuts come from :mod:`galley.app.shortcuts`.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QWidget

from galley.app.shortcuts import (
    ACTUAL_SIZE,
    CLOSE_OTHERS,
    ESCAPE_AS_STRING,
    FORMAT,
    SHOW_WHITESPACE,
    UNESCAPE_STRING,
    WORD_WRAP,
    apply_standard,
)


def _action(
    parent: QWidget,
    text: str,
    *,
    standard: QKeySequence.StandardKey | None = None,
    shortcut: QKeySequence | None = None,
    checkable: bool = False,
    checked: bool = False,
    enabled: bool = True,
    role: QAction.MenuRole | None = None,
) -> QAction:
    item = QAction(text, parent)
    item.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
    if standard is not None:
        apply_standard(item, standard)
    elif shortcut is not None:
        item.setShortcut(shortcut)
    item.setCheckable(checkable)
    if checkable:
        item.setChecked(checked)
    item.setEnabled(enabled)
    if role is not None:
        item.setMenuRole(role)
    return item


class ShellActions:
    """Every command the shell exposes. Behavior is wired by the window."""

    def __init__(self, parent: QWidget) -> None:
        self.about = _action(
            parent,
            "About Galley",
            role=QAction.MenuRole.AboutRole,
        )
        self.preferences = _action(
            parent,
            "Preferences…",
            standard=QKeySequence.StandardKey.Preferences,
            role=QAction.MenuRole.PreferencesRole,
        )
        self.quit = _action(
            parent,
            "Quit",
            standard=QKeySequence.StandardKey.Quit,
            role=QAction.MenuRole.QuitRole,
        )
        self.open_file = _action(
            parent,
            "Open file…",
            standard=QKeySequence.StandardKey.Open,
        )

        self.undo = _action(parent, "Undo", standard=QKeySequence.StandardKey.Undo)
        self.redo = _action(parent, "Redo", standard=QKeySequence.StandardKey.Redo)
        self.cut = _action(parent, "Cut", standard=QKeySequence.StandardKey.Cut)
        self.copy = _action(parent, "Copy", standard=QKeySequence.StandardKey.Copy)
        self.paste = _action(parent, "Paste", standard=QKeySequence.StandardKey.Paste)
        self.select_all = _action(
            parent,
            "Select All",
            standard=QKeySequence.StandardKey.SelectAll,
        )
        self.find = _action(parent, "Find…", standard=QKeySequence.StandardKey.Find)
        self.find_replace = _action(
            parent,
            "Find and Replace…",
            standard=QKeySequence.StandardKey.Replace,
        )
        self.escape_string = _action(
            parent, "Escape as String", shortcut=ESCAPE_AS_STRING
        )
        self.unescape_string = _action(
            parent, "Unescape String", shortcut=UNESCAPE_STRING
        )

        self.bigger = _action(
            parent, "Bigger", standard=QKeySequence.StandardKey.ZoomIn
        )
        self.smaller = _action(
            parent, "Smaller", standard=QKeySequence.StandardKey.ZoomOut
        )
        self.actual_size = _action(parent, "Actual Size", shortcut=ACTUAL_SIZE)
        self.word_wrap = _action(
            parent,
            "Word Wrap",
            shortcut=WORD_WRAP,
            checkable=True,
            checked=True,
        )
        self.show_whitespace = _action(
            parent,
            "Show Whitespace",
            shortcut=SHOW_WHITESPACE,
            checkable=True,
        )
        self.guides_off = _action(parent, "Off", checkable=True)
        self.guides_lines = _action(parent, "Lines", checkable=True)
        self.guides_depth = _action(
            parent,
            "Coloured by depth",
            checkable=True,
            checked=True,
        )
        self.indent_guides = self._exclusive_guides(parent)
        self.full_screen = _action(
            parent,
            "Enter Full Screen",
            standard=QKeySequence.StandardKey.FullScreen,
            checkable=True,
        )

        self.pop_out = _action(parent, "Pop out into window")
        self.show_window = _action(parent, "Show window", enabled=False)
        self.bring_back = _action(parent, "Bring back", enabled=False)

        self.rename = _action(parent, "Rename")
        self.duplicate = _action(parent, "Duplicate")
        self.close_tab = _action(
            parent, "Close", standard=QKeySequence.StandardKey.Close
        )
        self.close_others = _action(parent, "Close others", shortcut=CLOSE_OTHERS)
        self.close_tabs_right = _action(parent, "Close tabs to the right")
        self.new_tab = _action(parent, "New tab")

        self.outdent = _action(parent, "Outdent")
        self.indent = _action(parent, "Indent")
        self.sort_keys = _action(parent, "Sort keys")
        self.minify = _action(parent, "Minify")
        self.expand_all = _action(parent, "Expand all")
        self.collapse_all = _action(parent, "Collapse all")
        self.validate = _action(parent, "Validate")
        self.format = _action(
            parent,
            "Format",
            shortcut=FORMAT,
            enabled=False,
        )

    def _exclusive_guides(self, parent: QWidget) -> QActionGroup:
        group = QActionGroup(parent)
        group.setExclusive(True)
        group.addAction(self.guides_off)
        group.addAction(self.guides_lines)
        group.addAction(self.guides_depth)
        return group

    def stubs(self) -> list[QAction]:
        """Commands whose behavior is not part of the shell."""
        return [
            self.about,
            self.preferences,
            self.open_file,
            self.find,
            self.find_replace,
            self.escape_string,
            self.unescape_string,
            self.guides_off,
            self.guides_lines,
            self.guides_depth,
            self.pop_out,
            self.show_window,
            self.bring_back,
            self.rename,
            self.duplicate,
            self.close_tab,
            self.close_others,
            self.close_tabs_right,
            self.new_tab,
            self.outdent,
            self.indent,
            self.sort_keys,
            self.minify,
            self.expand_all,
            self.collapse_all,
            self.validate,
        ]
