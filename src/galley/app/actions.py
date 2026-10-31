"""Menu and toolbar actions for the shell.

Shortcut-bearing commands that Qt already knows use standard keys. The
remaining spec shortcuts come from :mod:`galley.app.shortcuts`.

Unbuilt roadmap commands stay visible and disabled. :data:`UNBUILT_ACTIONS`
is the Coming soon list: delete a name when that command ships.
:meth:`ShellActions._not_yet_actions` is the Not yet list: delete a command
there when it ships. :func:`main` fails while either list is non-empty.
"""

import os
import weakref
from collections.abc import Iterable, Sequence
from functools import partial

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLayout, QWidget

from galley.app.copy_as import COPY_AS_LABELS
from galley.app.shortcuts import (
    ACTUAL_SIZE,
    COLLAPSE_ALL,
    COMPARE,
    ESCAPE_AS_STRING,
    EXPAND_ALL,
    FIND_AND_REPLACE,
    FOLD,
    FORMAT,
    INDENT,
    NEXT_CHANGE,
    NEXT_PROBLEM,
    OUTDENT,
    PREVIOUS_CHANGE,
    PREVIOUS_PROBLEM,
    SHOW_PROBLEMS,
    SHOW_WHITESPACE,
    STACK_PANES,
    STACK_PANES_ALT,
    UNESCAPE_STRING,
    UNFOLD,
    WORD_WRAP,
    apply_standard,
    native_chord_text,
)
from galley.core.mask import CopyAsMode

COMING_SOON = "Coming soon"
# A command that doesn't apply to the Input format is greyed out with this
# tooltip and status tip (B2 on #63), for example "Not available for XML".
NOT_AVAILABLE = "Not available for {label}"
# A command Compare mode turns off, as tooltip and status tip (Chief's 3d
# brief). See ActionHold.
NOT_AVAILABLE_WHILE_COMPARING = "Not available while comparing"
# Minify's tooltip while Input is JSON (Bill).
MINIFY_TOOLTIP = "Remove all spaces and line breaks from the JSON"
# Validate's tooltip and accessible description (Chief's brief).
VALIDATE_TOOLTIP = "Check Input for errors without changing Output."
# Indent's and Outdent's tooltips, with the shortcut (Chief's brief).
INDENT_TOOLTIP = "Indent lines ({keys})"
OUTDENT_TOOLTIP = "Outdent lines ({keys})"

# Menu order: menu bar, then the toolbar.
# Delete a name here to ship that command. Do not keep a second list.
UNBUILT_ACTIONS: tuple[str, ...] = ()

# ShellActions attributes for each roadmap name. Shipping a command does
# not edit this map; it only deletes the name from UNBUILT_ACTIONS.
_ACTION_ATTRS: dict[str, tuple[str, ...]] = {
    "Open": ("open_file",),
    "Open Right Document…": ("open_right_document",),
    "Find": ("find",),
    "Compare": ("compare",),
    "Next Change": ("next_change",),
    "Previous Change": ("previous_change",),
    "Link Scrolling": ("link_scrolling",),
    "Indent": ("indent",),
    "Sort keys": ("sort_keys",),
    "Minify": ("minify",),
    # Collapse all shares this entry, so the release line stays "Expand".
    "Expand": ("expand_all", "collapse_all"),
    "Validate": ("validate",),
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


class ActionHold:
    """Grey out commands while Compare runs, then give each its own state back.

    :meth:`set_held` is the one function that saves and restores. Holding
    saves each action's enabled state, tooltip, status tip and
    not-available reason, then greys it out with
    :data:`NOT_AVAILABLE_WHILE_COMPARING`. Releasing puts the saved state
    back. ``widgets`` are controls without an action, such as the indent
    size picker; they keep their enabled state and tooltip the same way.

    While held, the actions sit in a disabled ``QActionGroup``, so a window
    sync that turns one on leaves it off, and the reason comes back if a
    sync sets another tooltip. A held widget is turned off again the same
    way, such as the Format button, which the window enables itself.

    ``parent`` owns the group and the widget guard, so Qt deletes them with
    it. Left to Python's gc, their Qt destructors could run mid-collection
    while Qt still used them. The hold keeps a weak reference only, so it
    does not keep Compare or the window alive until that collection.
    """

    def __init__(
        self,
        actions: Iterable[QAction],
        widgets: Iterable[QWidget] = (),
        *,
        parent: QObject,
    ) -> None:
        self._actions = tuple(actions)
        self._widgets = tuple(widgets)
        self._group: QActionGroup | None = None
        self._saved: list[tuple[bool, str, str, object]] = []
        self._saved_widgets: list[tuple[bool, str, str]] = []
        self._watchers: list[tuple[QAction, partial[None]]] = []
        self._parent = weakref.ref(parent)
        self._guard = _WidgetGuard(parent)

    def is_held(self) -> bool:
        return self._group is not None

    def set_held(self, held: bool) -> None:
        """Grey everything out with the reason, or put each saved state back."""
        if held == self.is_held():
            return
        if held:
            self._hold()
        else:
            self._release()

    def _hold(self) -> None:
        # Checked first, so a refused hold leaves every action as it was.
        parent = self._parent()
        if parent is None:
            raise RuntimeError(
                "ActionHold can't hold: its parent was deleted, so there is "
                "nothing to own the action group."
            )
        self._saved = [
            (
                action.isEnabled(),
                action.toolTip(),
                action.statusTip(),
                action.property("notAvailable"),
            )
            for action in self._actions
        ]
        self._saved_widgets = [
            (widget.isEnabled(), widget.toolTip(), widget.accessibleDescription())
            for widget in self._widgets
        ]
        group = QActionGroup(parent)
        group.setExclusive(False)
        self._group = group
        for action in self._actions:
            group.addAction(action)
        group.setEnabled(False)
        for action in self._actions:
            _show_reason(action)
            watcher = partial(_keep_reason, action)
            action.changed.connect(watcher)
            self._watchers.append((action, watcher))
        for widget in self._widgets:
            _hold_widget(widget)
            widget.installEventFilter(self._guard)

    def _release(self) -> None:
        for action, watcher in self._watchers:
            action.changed.disconnect(watcher)
        self._watchers = []
        for widget in self._widgets:
            widget.removeEventFilter(self._guard)
        group = self._group
        assert group is not None
        group.setEnabled(True)
        for action in self._actions:
            group.removeAction(action)
        self._group = None
        group.deleteLater()
        for action, (enabled, tip, status, reason) in zip(
            self._actions, self._saved, strict=True
        ):
            action.setProperty("notAvailable", reason)
            action.setToolTip(tip)
            action.setStatusTip(status)
            action.setEnabled(enabled)
        for widget, (enabled, tip, description) in zip(
            self._widgets, self._saved_widgets, strict=True
        ):
            widget.setEnabled(enabled)
            widget.setToolTip(tip)
            widget.setAccessibleDescription(description)
        self._saved = []
        self._saved_widgets = []


class _WidgetGuard(QObject):
    """Turns a held widget off again, with the reason, after the window changes it."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            isinstance(watched, QWidget)
            and event.type() in (QEvent.Type.EnabledChange, QEvent.Type.ToolTipChange)
            and (
                watched.isEnabled()
                or watched.toolTip() != NOT_AVAILABLE_WHILE_COMPARING
            )
        ):
            _hold_widget(watched)
        return False


def _hold_widget(widget: QWidget) -> None:
    widget.setEnabled(False)
    widget.setToolTip(NOT_AVAILABLE_WHILE_COMPARING)
    widget.setAccessibleDescription(NOT_AVAILABLE_WHILE_COMPARING)


def _show_reason(action: QAction) -> None:
    action.setProperty("notAvailable", NOT_AVAILABLE_WHILE_COMPARING)
    action.setToolTip(NOT_AVAILABLE_WHILE_COMPARING)
    action.setStatusTip(NOT_AVAILABLE_WHILE_COMPARING)


def _keep_reason(action: QAction) -> None:
    """Put the reason back after a window sync replaced a held action's tips."""
    if (
        action.toolTip() != NOT_AVAILABLE_WHILE_COMPARING
        or action.statusTip() != NOT_AVAILABLE_WHILE_COMPARING
        or not_available_reason(action) != NOT_AVAILABLE_WHILE_COMPARING
    ):
        _show_reason(action)


def set_busy(action: QAction, tooltip: str, description: str) -> None:
    """Mark ``action`` as running its job, or "" for both when it is done.

    The toolbar shows the tooltip, the description and a busy cursor on its
    button. The caller enables and disables the action.
    """
    action.setProperty("busyToolTip", tooltip)
    action.setProperty("busyDescription", description)


def busy_tips(action: QAction) -> tuple[str, str]:
    """The busy tooltip and description of ``action``, or two empty strings."""
    tooltip = action.property("busyToolTip")
    description = action.property("busyDescription")
    return (
        tooltip if isinstance(tooltip, str) else "",
        description if isinstance(description, str) else "",
    )


def set_ready_description(action: QAction, description: str) -> None:
    """The accessible description ``action``'s toolbar button has when ready.

    A busy or greyed-out button shows its own description instead.
    """
    action.setProperty("readyDescription", description)


def ready_description(action: QAction) -> str:
    """The description set with :func:`set_ready_description`, or ""."""
    description = action.property("readyDescription")
    return description if isinstance(description, str) else ""


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
        # ⌘W / Ctrl+W. It is in no menu; the window holds it so the keys work.
        self.close_window = _action(
            parent,
            "Close Window",
            standard=QKeySequence.StandardKey.Close,
            role=QAction.MenuRole.NoRole,
        )
        self.open_file = _action(
            parent,
            "Open file…",
            standard=QKeySequence.StandardKey.Open,
        )
        # File › Open Right Document… loads Compare's Right side (#65 slice
        # 3e). No shortcut.
        self.open_right_document = _action(
            parent,
            "Open Right Document…",
            role=QAction.MenuRole.NoRole,
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
        # Opens Input's find bar with the replace row.
        self.find_replace = _action(
            parent, "Find and Replace…", shortcut=FIND_AND_REPLACE
        )
        self.escape_string = _action(
            parent, "Escape as String", shortcut=ESCAPE_AS_STRING
        )
        self.unescape_string = _action(
            parent, "Unescape String", shortcut=UNESCAPE_STRING
        )

        # View › Font. NoRole keeps every item out of the macOS app menu.
        self.default_font = _action(
            parent,
            "Default Font",
            checkable=True,
            checked=True,
            role=QAction.MenuRole.NoRole,
        )
        # The family Choose Font… picked; hidden until there is one.
        self.picked_font = _action(
            parent, "", checkable=True, role=QAction.MenuRole.NoRole
        )
        self.picked_font.setObjectName("pickedFont")
        self.picked_font.setVisible(False)
        self.font_family = QActionGroup(parent)
        self.font_family.setExclusive(True)
        self.font_family.addAction(self.default_font)
        self.font_family.addAction(self.picked_font)
        self.choose_font = _action(parent, "Choose Font…", role=QAction.MenuRole.NoRole)
        self.bigger = _action(
            parent,
            "Bigger",
            standard=QKeySequence.StandardKey.ZoomIn,
            role=QAction.MenuRole.NoRole,
        )
        self.smaller = _action(
            parent,
            "Smaller",
            standard=QKeySequence.StandardKey.ZoomOut,
            role=QAction.MenuRole.NoRole,
        )
        self.actual_size = _action(
            parent,
            "Actual Size",
            shortcut=ACTUAL_SIZE,
            role=QAction.MenuRole.NoRole,
        )
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
        # View section for Compare (#65). Next and Previous Change wait for
        # Compare mode. Link Scrolling is on until it is turned off.
        self.compare = _action(
            parent,
            "Compare",
            shortcut=COMPARE,
            checkable=True,
            role=QAction.MenuRole.NoRole,
        )
        self.next_change = _action(
            parent,
            "Next Change",
            enabled=False,
            role=QAction.MenuRole.NoRole,
        )
        self.next_change.setShortcuts(list(NEXT_CHANGE))
        self.previous_change = _action(
            parent,
            "Previous Change",
            enabled=False,
            role=QAction.MenuRole.NoRole,
        )
        self.previous_change.setShortcuts(list(PREVIOUS_CHANGE))
        self.link_scrolling = _action(
            parent,
            "Link Scrolling",
            checkable=True,
            checked=True,
            role=QAction.MenuRole.NoRole,
        )
        self.appearance_system = _action(parent, "System", checkable=True, checked=True)
        self.appearance_light = _action(parent, "Light", checkable=True)
        self.appearance_dark = _action(parent, "Dark", checkable=True)
        self.appearance = self._exclusive_appearance(parent)

        # Edit › Indent and Outdent act on the focused pane's lines.
        self.indent = _action(parent, "Indent", shortcut=INDENT)
        self.indent.setToolTip(INDENT_TOOLTIP.format(keys=native_chord_text(INDENT)))
        self.outdent = _action(parent, "Outdent", shortcut=OUTDENT)
        self.outdent.setToolTip(OUTDENT_TOOLTIP.format(keys=native_chord_text(OUTDENT)))
        # On: the next Format writes keys A–Z at every level. Output only.
        self.sort_keys = _action(parent, "Sort keys", checkable=True)
        self.sort_keys.setObjectName("sortKeys")
        # Writes Input's JSON to Output with no spaces or line breaks.
        self.minify = _action(parent, "Minify")
        self.minify.setToolTip(MINIFY_TOOLTIP)
        self.expand_all = _action(parent, "Expand all", shortcut=EXPAND_ALL)
        self.collapse_all = _action(parent, "Collapse all", shortcut=COLLAPSE_ALL)
        self.fold = _action(parent, "Fold", shortcut=FOLD)
        self.unfold = _action(parent, "Unfold", shortcut=UNFOLD)
        # Checks Input with Format's checks. Output is never changed.
        self.validate = _action(parent, "Validate")
        self.validate.setToolTip(VALIDATE_TOOLTIP)
        set_ready_description(self.validate, VALIDATE_TOOLTIP)
        # Opens the Output Options popover from the Output pane's Options
        # link. Not in a menu or toolbar.
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
        return []

    def _not_yet_actions(self) -> list[QAction]:
        """Unfinished commands that are not on the Coming soon roadmap.

        Menu order: menu bar, then the toolbar. Delete a command here when
        it ships. The release check prints each menu label and fails while
        this list is non-empty. An empty list prints nothing.
        """
        return []


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
