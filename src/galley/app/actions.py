"""Menu and toolbar actions for the shell.

Shortcut-bearing commands that Qt already knows use standard keys. The
remaining spec shortcuts come from :mod:`galley.app.shortcuts`.

Unbuilt roadmap commands stay visible and disabled. :data:`UNBUILT_ACTIONS`
is the Coming soon list: delete a name when that command ships.
:meth:`ShellActions._not_yet_actions` is the Not yet list: delete a command
there when it ships. :func:`main` fails while either list is non-empty.
"""

import os
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLayout, QWidget

from galley.app.copy_as import COPY_AS_LABELS
from galley.app.shortcuts import (
    ACTUAL_SIZE,
    CLOSE_OTHERS,
    COLLAPSE_ALL,
    ESCAPE_AS_STRING,
    EXPAND_ALL,
    FOLD,
    FORMAT,
    NEXT_PROBLEM,
    PREVIOUS_PROBLEM,
    SHOW_PROBLEMS,
    SHOW_WHITESPACE,
    STACK_PANES,
    STACK_PANES_ALT,
    UNESCAPE_STRING,
    UNFOLD,
    WORD_WRAP,
    apply_standard,
)
from galley.core.mask import CopyAsMode

COMING_SOON = "Coming soon"
# A command that doesn't apply to the Input format is greyed out with this
# tooltip and status tip (B2 on #63), for example "Not available for XML".
NOT_AVAILABLE = "Not available for {label}"

# Menu order: menu bar, then the toolbar, then the tab menu.
# Delete a name here to ship that command. Do not keep a second list.
UNBUILT_ACTIONS: tuple[str, ...] = (
    "Guides",
    "Pop out",
    "Indent",
    "Minify",
    "Validate",
    "Tabs",
)

# ShellActions attributes for each roadmap name. Shipping a command does
# not edit this map; it only deletes the name from UNBUILT_ACTIONS.
_ACTION_ATTRS: dict[str, tuple[str, ...]] = {
    "Open": ("open_file",),
    "Find": ("find",),
    # Indent Guides: the ✓ stays on Off, the real state, until guides ship.
    "Guides": ("guides_off", "guides_lines", "guides_depth"),
    # The Window menu and the tab strip's Pop out ship together (#91 D8).
    "Pop out": ("pop_out", "show_window", "bring_back"),
    "Indent": ("indent",),
    "Sort keys": ("sort_keys",),
    "Minify": ("minify",),
    # Collapse all shares this entry, so the release line stays "Expand".
    "Expand": ("expand_all", "collapse_all"),
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
    """Return the two-line Coming soon block, or None when ``actions`` is empty.

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


def not_yet_failure(actions: Sequence[str] | None = None) -> str | None:
    """Return the two-line Not yet block, or None when ``actions`` is empty.

    Names are printed in the order given. Pass None to read the menu labels
    of :meth:`ShellActions._not_yet_actions`, which is menu order.
    """
    names = _not_yet_menu_names() if actions is None else actions
    if not names:
        return None
    listed = ", ".join(names)
    return (
        'Release blocked: these actions still show "Not yet": '
        f"{listed}.\n"
        "Build them or remove them from _not_yet_actions() in actions.py "
        "before tagging."
    )


def main() -> int:
    """Fail while either unfinished list is non-empty. Return 0 when both are empty.

    Coming soon names come from :data:`UNBUILT_ACTIONS`. Not yet names are the
    menu labels from :meth:`ShellActions._not_yet_actions`, in menu order.
    """
    blocks = [
        block for block in (release_failure(), not_yet_failure()) if block is not None
    ]
    if not blocks:
        return 0
    print("\n".join(blocks))
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


def not_available_text(label: str) -> str:
    """The tooltip of a command that doesn't apply to format ``label``."""
    return NOT_AVAILABLE.format(label=label)


def set_not_available(action: QAction, reason: str) -> None:
    """Show why ``action`` is greyed out, or "" for its usual tips.

    The reason is the tooltip and the status tip, which the macOS menu bar
    shows instead of a tooltip. The action's own tips come back with "".
    The caller enables and disables the action.
    """
    if action.property("readyToolTip") is None:
        tip = action.toolTip()
        action.setProperty("readyToolTip", "" if tip == action.text() else tip)
        action.setProperty("readyStatusTip", action.statusTip())
    action.setProperty("notAvailable", reason)
    if reason:
        action.setToolTip(reason)
        action.setStatusTip(reason)
        return
    action.setToolTip(action.property("readyToolTip"))
    action.setStatusTip(action.property("readyStatusTip"))


def not_available_reason(action: QAction) -> str:
    """Why ``action`` is greyed out for this document, or ""."""
    reason = action.property("notAvailable")
    return reason if isinstance(reason, str) else ""


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
        # Edit › Copy As and the Output right-click menu (#66). Each one
        # copies from Output, so all three stay disabled until it has text.
        self.copy_as_original = _action(
            parent, COPY_AS_LABELS["original"], enabled=False
        )
        self.copy_as_masked = _action(parent, COPY_AS_LABELS["masked"], enabled=False)
        self.copy_as_fake = _action(parent, COPY_AS_LABELS["fake"], enabled=False)
        self.paste = _action(parent, "Paste", standard=QKeySequence.StandardKey.Paste)
        self.select_all = _action(
            parent,
            "Select All",
            standard=QKeySequence.StandardKey.SelectAll,
        )
        self.find = _action(parent, "Find…", standard=QKeySequence.StandardKey.Find)
        self.find_next = _action(
            parent, "Find Next", standard=QKeySequence.StandardKey.FindNext
        )
        self.find_previous = _action(
            parent, "Find Previous", standard=QKeySequence.StandardKey.FindPrevious
        )
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
        self.guides_off = _action(parent, "Off", checkable=True, checked=True)
        self.guides_lines = _action(parent, "Lines", checkable=True)
        self.guides_depth = _action(parent, "Colored by depth", checkable=True)
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
        self.stack_panes.setShortcuts([STACK_PANES, STACK_PANES_ALT])
        self.stack_panes.setObjectName("stackPanes")
        self.next_problem = _action(parent, "Next Problem")
        self.next_problem.setShortcuts(list(NEXT_PROBLEM))
        self.previous_problem = _action(parent, "Previous Problem")
        self.previous_problem.setShortcuts(list(PREVIOUS_PROBLEM))
        self.appearance_system = _action(parent, "System", checkable=True, checked=True)
        self.appearance_light = _action(parent, "Light", checkable=True)
        self.appearance_dark = _action(parent, "Dark", checkable=True)
        self.appearance = self._exclusive_appearance(parent)

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
        # On: the next Format writes keys A–Z at every level. Output only.
        self.sort_keys = _action(parent, "Sort keys", checkable=True)
        self.sort_keys.setObjectName("sortKeys")
        self.minify = _action(parent, "Minify")
        self.expand_all = _action(parent, "Expand all", shortcut=EXPAND_ALL)
        self.collapse_all = _action(parent, "Collapse all", shortcut=COLLAPSE_ALL)
        self.fold = _action(parent, "Fold", shortcut=FOLD)
        self.unfold = _action(parent, "Unfold", shortcut=UNFOLD)
        self.validate = _action(parent, "Validate")
        # Triggered by the Output pane's Options link; not in a menu or toolbar.
        self.output_options = _action(parent, "Output Options")
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

    def copy_as_actions(self) -> dict[CopyAsMode, QAction]:
        """The Copy As items by mode, in menu order."""
        return {
            "original": self.copy_as_original,
            "masked": self.copy_as_masked,
            "fake": self.copy_as_fake,
        }

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

    def _exclusive_appearance(self, parent: QWidget) -> QActionGroup:
        group = QActionGroup(parent)
        group.setExclusive(True)
        group.addAction(self.appearance_system)
        group.addAction(self.appearance_light)
        group.addAction(self.appearance_dark)
        return group

    def stubs(self) -> list[QAction]:
        """Commands whose behavior is not part of the shell.

        Names still listed in :data:`UNBUILT_ACTIONS` are disabled instead,
        so triggering them does nothing. The rest report that they are
        not yet available.
        """
        blocked = {id(action) for action in self._unbuilt_qactions()}
        pending = [*self._stub_actions(), *self._not_yet_actions()]
        return [action for action in pending if id(action) not in blocked]

    def _stub_actions(self) -> list[QAction]:
        """Roadmap commands. Each one belongs to an :data:`UNBUILT_ACTIONS` entry."""
        return [
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
            self.indent,
            self.minify,
            self.validate,
        ]

    def _not_yet_actions(self) -> list[QAction]:
        """Unfinished commands that are not on the Coming soon roadmap.

        Menu order: menu bar, then the toolbar, then the Output pane footer.
        Delete a command here when it ships. The release check prints each
        menu label and fails while this list is non-empty.
        """
        return [
            self.find_replace,
            self.outdent,
            self.output_options,
        ]


def _qapplication() -> QApplication:
    """Return the running application, or a headless one for the release check."""
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication([])


def _not_yet_release_name(shell: ShellActions, action: QAction) -> str:
    """Release-check label: the menu text."""
    del shell
    return action.text()


def _not_yet_menu_names() -> tuple[str, ...]:
    """Release labels of :meth:`ShellActions._not_yet_actions`, in menu order."""
    _qapplication()
    parent = QWidget()
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    try:
        shell = ShellActions(parent)
        return tuple(
            _not_yet_release_name(shell, action) for action in shell._not_yet_actions()
        )
    finally:
        parent.deleteLater()


if __name__ == "__main__":
    raise SystemExit(main())
