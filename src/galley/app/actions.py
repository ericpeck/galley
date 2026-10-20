"""Menu and toolbar actions for the shell.

Shortcut-bearing commands that Qt already knows use standard keys. The
remaining spec shortcuts come from :mod:`galley.app.shortcuts`.

Unbuilt roadmap commands stay visible and disabled. :data:`UNBUILT_ACTIONS`
is the only on/off list: delete a name when that command ships.
:func:`main` fails while that list is non-empty.
"""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLayout, QWidget

from galley.app.shortcuts import (
    ACTUAL_SIZE,
    CLOSE_OTHERS,
    ESCAPE_AS_STRING,
    FORMAT,
    SHOW_PROBLEMS,
    SHOW_WHITESPACE,
    STACK_PANES,
    UNESCAPE_STRING,
    WORD_WRAP,
    apply_standard,
)

COMING_SOON = "Coming soon"

# Menu order: menu bar, then the toolbar, then the tab menu.
# Delete a name here to ship that command. Do not keep a second list.
UNBUILT_ACTIONS: tuple[str, ...] = (
    "Preferences",
    "Open",
    "Find",
    "Indent",
    "Sort keys",
    "Minify",
    "Expand",
    "Validate",
    "Tabs",
)

# ShellActions attributes for each roadmap name. Shipping a command does
# not edit this map; it only deletes the name from UNBUILT_ACTIONS.
_ACTION_ATTRS: dict[str, tuple[str, ...]] = {
    "Preferences": ("preferences",),
    "Open": ("open_file",),
    "Find": ("find",),
    "Indent": ("indent",),
    "Sort keys": ("sort_keys",),
    "Minify": ("minify",),
    "Expand": ("expand_all",),
    "Validate": ("validate",),
    "Tabs": (
        "rename",
        "duplicate",
        "close_tab",
        "close_others",
        "close_tabs_right",
        "new_tab",
    ),
}


def release_failure(actions: Sequence[str] | None = None) -> str | None:
    """Return the two-line release block, or None when ``actions`` is empty.

    Names are printed in the order given. Pass None to read
    :data:`UNBUILT_ACTIONS`.
    """
    names = UNBUILT_ACTIONS if actions is None else actions
    if not names:
        return None
    listed = ", ".join(names)
    return (
        'Release blocked: these actions still show "Coming soon": '
        f"{listed}.\n"
        "Build them or remove them from UNBUILT_ACTIONS in actions.py "
        "before tagging."
    )


def main() -> int:
    """Fail while :data:`UNBUILT_ACTIONS` is non-empty. Return 0 when it is empty."""
    failure = release_failure()
    if failure is None:
        return 0
    print(failure)
    return 1


def is_unbuilt(action: QAction) -> bool:
    """Return True when ``action`` is still on :data:`UNBUILT_ACTIONS`."""
    return action.property("unbuiltName") is not None


def _owning_layout(control: QWidget) -> tuple[QLayout, int] | None:
    """Return the layout item that currently holds ``control``."""
    parent = control.parentWidget()
    if parent is None or parent.layout() is None:
        return None
    return _layout_index(parent.layout(), control)


def _layout_index(layout: QLayout, control: QWidget) -> tuple[QLayout, int] | None:
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is control:
            return layout, index
        child = item.layout()
        if child is not None:
            found = _layout_index(child, control)
            if found is not None:
                return found
    return None


def coming_soon_wrapper(control: QWidget, action: QAction) -> QWidget:
    """Return ``control``, or an enabled host when ``action`` is unbuilt.

    Qt does not show a tooltip for a disabled widget on hover. The host
    stays enabled and carries ``Coming soon``. A control that is already
    in a layout is replaced by that host.
    """
    if not is_unbuilt(action):
        return control
    control.setToolTip(COMING_SOON)
    control.setAccessibleDescription(COMING_SOON)
    control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    control.setEnabled(False)
    control.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
    # Reparenting a control that already has a layout crashes Qt on macOS
    # and Windows. Those controls keep the tooltip themselves. A control
    # with no layout yet gets an enabled host so hover can show it.
    if _owning_layout(control) is not None:
        return control
    host = QWidget()
    host.setProperty("comingSoonHost", True)
    host.setToolTip(COMING_SOON)
    host.setAccessibleDescription(COMING_SOON)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.addWidget(control)
    return host


def _mark_coming_soon(action: QAction, name: str) -> None:
    action.setProperty("unbuiltName", name)
    action.setToolTip(COMING_SOON)
    action.setEnabled(False)


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
        self.about.setObjectName("aboutGalley")
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
        self.show_problems = _action(
            parent,
            "Show Problems",
            shortcut=SHOW_PROBLEMS,
            checkable=True,
            checked=True,
        )
        self.stack_panes = _action(
            parent,
            "Stack Panes",
            shortcut=STACK_PANES,
            checkable=True,
        )
        self.stack_panes.setObjectName("stackPanes")

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
        self.convert_python = _action(
            parent,
            "Convert from Python",
            enabled=False,
        )
        self._apply_unbuilt()

    def _apply_unbuilt(self) -> None:
        for name in UNBUILT_ACTIONS:
            for attr in _ACTION_ATTRS[name]:
                _mark_coming_soon(getattr(self, attr), name)

    def _unbuilt_qactions(self) -> list[QAction]:
        actions: list[QAction] = []
        for name in UNBUILT_ACTIONS:
            for attr in _ACTION_ATTRS[name]:
                actions.append(getattr(self, attr))
        return actions

    def _exclusive_guides(self, parent: QWidget) -> QActionGroup:
        group = QActionGroup(parent)
        group.setExclusive(True)
        group.addAction(self.guides_off)
        group.addAction(self.guides_lines)
        group.addAction(self.guides_depth)
        return group

    def stubs(self) -> list[QAction]:
        """Commands whose behavior is not part of the shell.

        Names still listed in :data:`UNBUILT_ACTIONS` are disabled instead,
        so triggering them does nothing.
        """
        blocked = {id(action) for action in self._unbuilt_qactions()}
        return [action for action in self._stub_actions() if id(action) not in blocked]

    def _stub_actions(self) -> list[QAction]:
        return [
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


if __name__ == "__main__":
    raise SystemExit(main())
