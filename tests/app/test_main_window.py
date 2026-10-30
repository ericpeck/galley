"""Smoke tests for the Galley window, menus, and process start method."""

import ast
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSplitter,
    QWidget,
)

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import (
    FORMAT_BUSY_DESCRIPTION,
    FORMAT_BUSY_TOOLTIP,
    FORMATTING_MESSAGE,
    NOT_YET_MESSAGE,
    PROGRESS_BAR_REVEAL_MS,
    PROGRESS_WIDTH_PX,
    SPINNER_REVEAL_MS,
    SPINNER_WIDTH_PX,
    _shows_own_full_screen_item,
)
from galley.app.pane import PaneRole
from galley.app.parse_session import ParseSession
from galley.app.problems_pane import ProblemsPane
from galley.app.theme import ACCENT, ACCENT_DISABLED

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "galley" / "app"
_PORTABLE = QKeySequence.SequenceFormat.PortableText

# Portable text. Ctrl is Command on macOS. ⇧⌘[ switches tabs on macOS, so
# only macOS keeps ⌥⌘ for Fold and Unfold (#95). F8 is Play/Pause on Mac
# keyboards, so macOS lists ⌘' first for the problem keys (#78).
_FOLD = "Ctrl+Alt+" if sys.platform == "darwin" else "Ctrl+Shift+"
_PROBLEM = ["Ctrl+'", "F8"] if sys.platform == "darwin" else ["F8"]
_PREVIOUS = ["Ctrl+Shift+'", "Shift+F8"] if sys.platform == "darwin" else ["Shift+F8"]
# ⌘H is the system Hide on macOS, so Find and Replace is ⌥⌘F there.
_FIND_AND_REPLACE = ["Ctrl+Alt+F"] if sys.platform == "darwin" else ["Ctrl+H"]
# Compare (#65 slice 3d): F7 is a media key on Mac keyboards, so macOS lists
# ⌥⌘↓ and ⌥⌘↑ first. Never F8, which is Next Problem's.
_NEXT_CHANGE = ["Ctrl+Alt+Down", "F7"] if sys.platform == "darwin" else ["F7"]
_PREVIOUS_CHANGE = (
    ["Ctrl+Alt+Up", "Shift+F7"] if sys.platform == "darwin" else ["Shift+F7"]
)
# The find bars' Regex and Whole word keys are QShortcuts, not actions.
_FIND_TOGGLES = "Ctrl+Alt+" if sys.platform == "darwin" else "Alt+"
CUSTOM_SHORTCUTS = {
    "Word Wrap": ["Alt+Z"],
    "Escape as String": ["Ctrl+Shift+E"],
    "Unescape String": ["Ctrl+K, Ctrl+E"],
    "Show Whitespace": ["Ctrl+Shift+8"],
    "Actual Size": ["Ctrl+0"],
    "Format": ["Ctrl+Return"],
    "Show Problems": ["Ctrl+Shift+M"],
    "Stack Panes": ["Ctrl+\\", "Ctrl+Shift+L"],
    "Fold": [_FOLD + "["],
    "Unfold": [_FOLD + "]"],
    "Collapse all": ["Ctrl+K, Ctrl+0"],
    "Expand all": ["Ctrl+K, Ctrl+J"],
    "Next Problem": _PROBLEM,
    "Previous Problem": _PREVIOUS,
    "Find and Replace…": _FIND_AND_REPLACE,
    "Compare": ["Ctrl+Shift+D"],
    "Next Change": _NEXT_CHANGE,
    "Previous Change": _PREVIOUS_CHANGE,
}
# Ctrl+H is Qt's standard Replace key on Windows: the same command.
_SAME_COMMAND = {("Find and Replace…", "Replace")}

STANDARD_SHORTCUTS = {
    "Undo": QKeySequence.StandardKey.Undo,
    "Redo": QKeySequence.StandardKey.Redo,
    "Cut": QKeySequence.StandardKey.Cut,
    "Copy": QKeySequence.StandardKey.Copy,
    "Paste": QKeySequence.StandardKey.Paste,
    "Select All": QKeySequence.StandardKey.SelectAll,
    "Find…": QKeySequence.StandardKey.Find,
    "Find Next": QKeySequence.StandardKey.FindNext,
    "Find Previous": QKeySequence.StandardKey.FindPrevious,
    "Open file…": QKeySequence.StandardKey.Open,
    "Bigger": QKeySequence.StandardKey.ZoomIn,
    "Smaller": QKeySequence.StandardKey.ZoomOut,
    "Enter Full Screen": QKeySequence.StandardKey.FullScreen,
    "Close Window": QKeySequence.StandardKey.Close,
}


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _menu(window, title: str) -> QMenu:
    # PySide 6.9 drops the wrapper from QAction.menu() for the Galley menu
    # once the temporary list from QMenuBar.actions() is released. About
    # and Quit use menu roles. The menu is still in the bar;
    # the object name set when it was created still finds it.
    menu = window.findChild(QMenu, "menu" + title.replace(" ", ""))
    assert menu is not None, title
    assert menu.title() == title
    assert menu.menuAction() in window.menuBar().actions()
    return menu


def _labels(menu: QMenu) -> list[str]:
    """The items ``menu`` shows; hidden ones, like an unpicked font, are left out."""
    labels: list[str] = []
    for action in menu.actions():
        if action.isVisible():
            labels.append("|" if action.isSeparator() else action.text())
    return labels


def _submenu(menu: QMenu, title: str) -> QMenu:
    # Direct child only. PySide 6.9 invalidates QAction.menu() wrappers.
    name = "menu" + title.replace(" ", "")
    child = menu.findChild(QMenu, name, Qt.FindDirectChildrenOnly)
    assert child is not None, title
    assert child.title() == title
    assert child.menuAction() in menu.actions()
    return child


def _portable(action: QAction) -> list[str]:
    return [
        sequence.toString(QKeySequence.SequenceFormat.PortableText)
        for sequence in action.shortcuts()
        if not sequence.isEmpty()
    ]


def _sequence_id(sequence: QKeySequence) -> tuple[int, ...]:
    parts: list[int] = []
    for index in range(sequence.count()):
        part = sequence[index]
        combined = part.toCombined() if hasattr(part, "toCombined") else part
        parts.append(int(combined))
    return tuple(parts)


def test_main_window_opens(qtbot) -> None:
    window = _show(qtbot)
    assert window.isVisible()
    assert window.windowTitle() == "Galley"
    assert window.centralWidget() is not None
    window_color = window.palette().color(QPalette.ColorRole.Window)
    app_color = QApplication.palette().color(QPalette.ColorRole.Window)
    assert window_color.name() == app_color.name()


def test_menu_bar_matches_spec(qtbot) -> None:
    window = _show(qtbot)
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles == ["Galley", "File", "Edit", "View", "Help"]

    assert _labels(_menu(window, "Galley")) == ["Quit"]
    assert _labels(_menu(window, "File")) == [
        "Open file…",
        "Open Right Document…",
        "Open Recent",
    ]
    assert _labels(_menu(window, "Edit")) == [
        "Undo",
        "Redo",
        "|",
        "Cut",
        "Copy",
        "Copy As",
        "Paste",
        "Select All",
        "|",
        "Indent",
        "Outdent",
        "|",
        "Find…",
        "Find Next",
        "Find Previous",
        "Find and Replace…",
        "|",
        "Escape as String",
        "Unescape String",
        "|",
        "Format",
        "Convert from Python",
    ]
    edit = _menu(window, "Edit")
    assert _labels(_submenu(edit, "Copy As")) == [
        "Original",
        "Masked",
        "Random fake",
    ]
    view = _menu(window, "View")
    assert _labels(view) == [
        "Font",
        "|",
        "Word Wrap",
        "Show Whitespace",
        "Show Problems",
        "Next Problem",
        "Previous Problem",
        "|",
        "Compare",
        "Next Change",
        "Previous Change",
        "Link Scrolling",
        "|",
        "Expand all",
        "Collapse all",
        "Fold",
        "Unfold",
        "|",
        "Stack Panes",
        "|",
        "Appearance",
        "|",
        "Enter Full Screen",
    ]
    assert _labels(_submenu(view, "Font")) == [
        "Default Font",
        "Choose Font…",
        "|",
        "Bigger",
        "Smaller",
        "Actual Size",
    ]
    assert _labels(_submenu(view, "Appearance")) == ["System", "Light", "Dark"]
    assert _labels(_menu(window, "Help")) == ["About Galley"]
    assert window.findChild(QMenu, "tabMenu") is None


def _menu_bar_actions(window) -> list[QAction]:
    """Every item in the menu bar's menus, submenus included."""
    found: list[QAction] = []

    def walk(menu: QMenu) -> None:
        children = menu.findChildren(QMenu, options=Qt.FindDirectChildrenOnly)
        child_by_action = {id(child.menuAction()): child for child in children}
        for action in menu.actions():
            found.append(action)
            child = child_by_action.get(id(action))
            if child is not None:
                walk(child)

    for title in [action.text() for action in window.menuBar().actions()]:
        walk(_menu(window, title))
    return found


def _app_menu_role(action: QAction) -> QAction.MenuRole:
    """The role macOS gives an item, which decides if it moves to the app menu.

    A TextHeuristicRole item moves by its text: About, Config, Preference,
    Options, Setting, Setup, Quit, or Exit.
    """
    role = action.menuRole()
    if role != QAction.MenuRole.TextHeuristicRole:
        return role
    text = action.text().replace("&", "").lower()
    if text.startswith("about") or text.endswith("about"):
        return QAction.MenuRole.AboutRole
    if text.startswith(("config", "preference", "options", "setting", "setup")):
        return QAction.MenuRole.PreferencesRole
    if text.startswith(("quit", "exit")):
        return QAction.MenuRole.QuitRole
    return QAction.MenuRole.NoRole


def test_preferences_is_removed(qtbot) -> None:
    """Preferences isn't built for v1: no action, menu item, or shortcut."""
    window = _show(qtbot)
    assert not hasattr(window._actions, "preferences")
    preferences_keys = {
        _sequence_id(sequence)
        for sequence in QKeySequence.keyBindings(QKeySequence.StandardKey.Preferences)
        if not sequence.isEmpty()
    }
    for action in window.findChildren(QAction):
        assert "preferences" not in action.text().lower(), action.text()
        assert action.menuRole() != QAction.MenuRole.PreferencesRole, action.text()
        keys = {_sequence_id(sequence) for sequence in action.shortcuts()}
        assert keys.isdisjoint(preferences_keys), action.text()
    for menu in window.findChildren(QMenu):
        assert "preferences" not in menu.title().lower(), menu.title()


def test_galley_menu_has_no_dangling_separator(qtbot) -> None:
    window = _show(qtbot)
    galley = _menu(window, "Galley")
    assert _labels(galley) == ["Quit"]
    assert [action.menuRole() for action in galley.actions()] == [
        QAction.MenuRole.QuitRole
    ]
    for title in [action.text() for action in window.menuBar().actions()]:
        labels = _labels(_menu(window, title))
        assert labels, title
        assert labels[0] != "|", title
        assert labels[-1] != "|", title
        pairs = zip(labels, labels[1:], strict=False)
        assert not any(a == b == "|" for a, b in pairs), title


def test_indent_guides_are_removed(qtbot) -> None:
    """Indent Guides aren't built for v1: no submenu, actions, or group."""
    window = _show(qtbot)
    for attr in ("guides_off", "guides_lines", "guides_depth", "indent_guides"):
        assert not hasattr(window._actions, attr), attr
    assert window.findChild(QMenu, "menuIndentGuides") is None
    for menu in window.findChildren(QMenu):
        assert "guide" not in menu.title().lower(), menu.title()
    for action in window.findChildren(QAction):
        assert "guide" not in action.text().lower(), action.text()
        assert action.text() not in ("Off", "Lines", "Colored by depth"), action.text()


def test_view_menu_has_no_empty_submenu_or_dangling_separator(qtbot) -> None:
    window = _show(qtbot)
    view = _menu(window, "View")
    labels = _labels(view)
    assert labels[0] != "|"
    assert labels[-1] != "|"
    pairs = zip(labels, labels[1:], strict=False)
    assert not any(a == b == "|" for a, b in pairs)
    submenus = view.findChildren(QMenu, options=Qt.FindDirectChildrenOnly)
    titles = [menu.title() for menu in submenus if menu.menuAction() in view.actions()]
    assert titles == ["Font", "Appearance"]
    for menu in submenus:
        assert menu.actions(), menu.title()


def test_menu_bar_has_no_empty_submenu(qtbot) -> None:
    """Every submenu in the menu bar shows at least one item.

    The release check reads the action lists, so it can't see an empty
    submenu. This names any it finds, such as "View ▸ Font".
    """
    window = _show(qtbot)
    empty: list[str] = []

    def walk(menu: QMenu, path: str) -> None:
        children = menu.findChildren(QMenu, options=Qt.FindDirectChildrenOnly)
        for child in children:
            if child.menuAction() not in menu.actions():
                continue
            named = f"{path} ▸ {child.title()}"
            shown = [
                action
                for action in child.actions()
                if action.isVisible() and not action.isSeparator()
            ]
            if not shown:
                empty.append(named)
            walk(child, named)

    for title in [action.text() for action in window.menuBar().actions()]:
        walk(_menu(window, title), title)
    assert empty == []


def test_pop_out_is_removed(qtbot) -> None:
    """Pop out isn't built for v1: no actions, Window menu, or strip button."""
    window = _show(qtbot)
    for attr in ("pop_out", "show_window", "bring_back"):
        assert not hasattr(window._actions, attr), attr
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles == ["Galley", "File", "Edit", "View", "Help"]
    assert window.findChild(QMenu, "menuWindow") is None
    for menu in window.findChildren(QMenu):
        assert menu.title() != "Window", menu.objectName()
    removed = ("Pop out into window", "Show window", "Bring back")
    for action in window.findChildren(QAction):
        assert "pop out" not in action.text().lower(), action.text()
        assert action.text() not in removed, action.text()
    for name in ("input", "output"):
        assert window.findChild(QWidget, f"{name}PopOut") is None, name
    for widget in window.findChildren(QWidget):
        assert not widget.objectName().endswith("PopOut"), widget.objectName()
        assert "pop out" not in widget.accessibleName().lower(), widget.objectName()


_TAB_ATTRS = (
    "rename",
    "duplicate",
    "close_tab",
    "close_others",
    "close_tabs_right",
    "new_tab",
)
_TAB_LABELS = (
    "Rename",
    "Duplicate",
    "Close",
    "Close others",
    "Close tabs to the right",
    "New tab",
)


def test_tabs_are_removed(qtbot) -> None:
    """Tabs aren't built for v1: no actions, tab menu, or strip menu."""
    window = _show(qtbot)
    for attr in _TAB_ATTRS:
        assert not hasattr(window._actions, attr), attr
    assert window.findChild(QMenu, "tabMenu") is None
    assert not hasattr(window, "_tab_menu")
    for action in window.findChildren(QAction):
        assert action.text() not in _TAB_LABELS, action.text()
    for name in ("input", "output"):
        strip = window.findChild(QWidget, f"{name}TabStrip")
        assert isinstance(strip, QWidget), name
        assert strip.contextMenuPolicy() == Qt.ContextMenuPolicy.DefaultContextMenu
        assert window.findChild(QPushButton, f"{name}CloseTab") is None
        assert window.findChild(QPushButton, f"{name}NewTab") is None


class _CloseSpy(QObject):
    """Counts the window's close events, and turns them down while ``refuse``."""

    def __init__(self) -> None:
        super().__init__()
        self.closes = 0
        self.refuse = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Close:
            self.closes += 1
            if self.refuse:
                event.ignore()
                return True
        return False


@pytest.mark.parametrize("target", ["inputEditor", "outputEditor"])
def test_ctrl_w_closes_the_window(qtbot, target) -> None:
    """Ctrl+W (⌘W on macOS) closes the window the way its close button does.

    The key sends the window a close event, so a close it turns down (as a
    save prompt would on Cancel) leaves the window open.
    """
    window = _show(qtbot)
    spy = _CloseSpy()
    window.installEventFilter(spy)
    focus = window.findChild(CodeEditor, target)
    assert isinstance(focus, CodeEditor)
    window.activateWindow()
    focus.setFocus()
    qtbot.waitUntil(focus.hasFocus, timeout=2000)

    spy.refuse = True
    qtbot.keyClick(focus, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    assert spy.closes == 1
    assert window.isVisible()

    spy.refuse = False
    qtbot.keyClick(focus, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    assert spy.closes == 2
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=2000)


def test_close_others_chord_stays_unbound(qtbot) -> None:
    """With Tabs removed, nothing starts with the old Ctrl+K, Ctrl+Shift+W chord."""
    window = _show(qtbot)
    chord = _sequence_id(
        QKeySequence("Ctrl+K, Ctrl+Shift+W", QKeySequence.SequenceFormat.PortableText)
    )
    bound: list[str] = []
    for action in window.findChildren(QAction):
        for sequence in action.shortcuts():
            if _sequence_id(sequence)[: len(chord)] == chord:
                rendered = sequence.toString(QKeySequence.SequenceFormat.PortableText)
                bound.append(f"{rendered}: {action.text() or action.objectName()}")
    assert bound == []


def test_only_about_and_quit_move_to_the_macos_app_menu(qtbot) -> None:
    window = _show(qtbot)
    moved = {
        action.text(): _app_menu_role(action)
        for action in _menu_bar_actions(window)
        if not action.isSeparator()
        and _app_menu_role(action)
        not in (QAction.MenuRole.NoRole, QAction.MenuRole.ApplicationSpecificRole)
    }
    assert moved == {
        "About Galley": QAction.MenuRole.AboutRole,
        "Quit": QAction.MenuRole.QuitRole,
    }


def test_view_defaults_match_spec(qtbot) -> None:
    window = _show(qtbot)
    assert _action(window, "Word Wrap").isChecked()
    assert not _action(window, "Show Whitespace").isChecked()
    assert not _action(window, "Stack Panes").isChecked()


def test_shortcuts_match_spec_and_do_not_collide(qtbot) -> None:
    window = _show(qtbot)
    for text, expected in CUSTOM_SHORTCUTS.items():
        assert _portable(_action(window, text)) == expected
    for text, standard in STANDARD_SHORTCUTS.items():
        expected = {
            sequence.toString(QKeySequence.SequenceFormat.PortableText)
            for sequence in QKeySequence.keyBindings(standard)
            if not sequence.isEmpty()
        }
        assert set(_portable(_action(window, text))) == expected

    owners: dict[tuple[int, ...], str] = {}
    duplicates: list[str] = []
    for label, sequence in _bound_keys(window):
        if sequence.isEmpty() or sequence.count() == 0:
            continue
        ident = _sequence_id(sequence)
        previous = owners.get(ident)
        if previous is None:
            owners[ident] = label
        elif previous != label:
            rendered = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            duplicates.append(f"{rendered}: {previous} and {label}")
    assert duplicates == []


def _bound_keys(window) -> list[tuple[str, QKeySequence]]:
    """Every action's keys, then the find bars' Regex and Whole word keys.

    The toggles are QShortcuts on each find bar. Every bar binds the same
    key to the same toggle, so a toggle is labelled by its key.
    """
    bound: list[tuple[str, QKeySequence]] = []
    for action in window.findChildren(QAction):
        label = action.text() or action.objectName()
        bound.extend((label, sequence) for sequence in action.shortcuts())
    for shortcut in window.findChildren(QShortcut):
        key = shortcut.key()
        bound.append((f"find toggle {key.toString(_PORTABLE)}", key))
    return bound


def test_compare_keys_avoid_the_find_toggles(qtbot) -> None:
    """Compare, Next Change and Previous Change miss Regex and Whole word.

    Regex is ⌥⌘R / Alt+R and Whole word ⌥⌘W / Alt+W (#170). The toggles
    are QShortcuts on the find bars, so the action-only walk above would
    not see them.
    """
    window = _show(qtbot)
    toggles = {
        _sequence_id(shortcut.key()) for shortcut in window.findChildren(QShortcut)
    }
    assert _sequence_id(QKeySequence(_FIND_TOGGLES + "R", _PORTABLE)) in toggles
    assert _sequence_id(QKeySequence(_FIND_TOGGLES + "W", _PORTABLE)) in toggles
    for text in ("Compare", "Next Change", "Previous Change"):
        for sequence in _action(window, text).shortcuts():
            assert _sequence_id(sequence) not in toggles, text
            assert "F8" not in sequence.toString(_PORTABLE), text


def test_custom_shortcuts_avoid_standard_keys_and_chord_prefixes(qtbot) -> None:
    window = _show(qtbot)
    standard: dict[tuple[int, ...], str] = {}
    for key in QKeySequence.StandardKey:
        for sequence in QKeySequence.keyBindings(key):
            if not sequence.isEmpty():
                standard[_sequence_id(sequence)] = key.name
    whole: dict[tuple[int, ...], str] = {}
    for action in window.findChildren(QAction):
        for sequence in action.shortcuts():
            if not sequence.isEmpty():
                whole[_sequence_id(sequence)] = action.text()

    clashes: list[str] = []
    for text in CUSTOM_SHORTCUTS:
        for sequence in _action(window, text).shortcuts():
            ident = _sequence_id(sequence)
            for end in range(1, len(ident) + 1):
                name = standard.get(ident[:end])
                if name is not None and (text, name) not in _SAME_COMMAND:
                    clashes.append(f"{text}: {name}")
            for end in range(1, len(ident)):
                if ident[:end] in whole:
                    clashes.append(f"{text}: prefix is {whole[ident[:end]]}")
    assert clashes == []


def test_splitter_starts_even(qtbot) -> None:
    window = _show(qtbot)
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.count() == 2

    def _even() -> bool:
        sizes = splitter.sizes()
        total = sum(sizes)
        if total <= 200:
            return False
        return abs(sizes[0] - sizes[1]) / total < 0.05

    qtbot.waitUntil(_even)
    ratio = window.findChild(QLabel, "splitRatio")
    assert ratio is not None
    parts = ratio.text().split()
    assert len(parts) == 2
    assert abs(int(parts[0]) - 50) <= 1
    assert abs(int(parts[1]) - 50) <= 1


def test_empty_state(qtbot) -> None:
    window = _show(qtbot)
    status = window.findChild(QLabel, "statusMessage")
    direction = window.findChild(QLabel, "directionLabel")
    caption = window.findChild(QLabel, "editingCaption")
    input_title = window.findChild(QLabel, "inputEmptyTitle")
    output_title = window.findChild(QLabel, "outputEmptyTitle")
    hint = window.findChild(QLabel, "inputEmptyHint")
    count = window.findChild(QLabel, "inputCount")
    clear = window.findChild(QPushButton, "clearButton")
    copy = window.findChild(QPushButton, "outputCopyButton")
    format_button = window.findChild(QPushButton, "formatButton")
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert status is not None and status.text() == "Ready"
    assert direction is not None and direction.text() == "JSON → JSON"
    assert caption is not None and caption.text() == "Editing <b>Input</b>"
    assert input_title is not None
    assert input_title.text().startswith(
        "Paste JSON, YAML, XML, or HTML, or drop a file here."
    )
    assert input_title.isVisible()
    assert hint is not None
    assert hint.text() == "⌨ ⌘V to paste · Ctrl+V on Windows"
    assert output_title is not None
    assert output_title.text() == "Formatted output will appear here."
    assert output_title.isVisible()
    assert count is not None and count.text() == "Empty"
    assert clear is not None and not clear.isEnabled()
    assert copy is not None and not copy.isEnabled()
    assert format_button is not None and not format_button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert format_button.text().startswith("{·} Format")
    assert "Format  " not in format_button.text()
    sheet = format_button.styleSheet()
    assert ACCENT in sheet
    assert "palette(highlighted-text)" in sheet
    assert "palette(light)" in sheet
    light = QApplication.palette().color(QPalette.ColorRole.Light).name()
    assert light == ACCENT_DISABLED.lower()
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    qtbot.waitUntil(lambda: editor.hasFocus())


def test_editors_have_line_number_gutters(qtbot) -> None:
    window = _show(qtbot)
    for name in ("inputEditor", "outputEditor"):
        editor = window.findChild(CodeEditor, name)
        assert isinstance(editor, CodeEditor)
        assert (
            editor.line_number_color().name()
            == editor.palette().color(QPalette.ColorRole.PlaceholderText).name()
        )
        assert (
            editor.gutter_color().name()
            == editor.palette().color(QPalette.ColorRole.AlternateBase).name()
        )
        assert editor.viewportMargins().left() >= 48
        base = editor.palette().color(QPalette.ColorRole.Base)
        app_base = QApplication.palette().color(QPalette.ColorRole.Base)
        assert base.name() == app_base.name()


def test_stub_action_reports_not_yet(qtbot, monkeypatch) -> None:
    # Nothing is Not yet any more. Link Scrolling on the Not yet list is the
    # example: a stub reports Not yet.
    monkeypatch.setattr(
        "galley.app.actions.ShellActions._not_yet_actions",
        lambda self: [self.link_scrolling],
    )
    window = _show(qtbot)
    _action(window, "Link Scrolling").trigger()
    assert NOT_YET_MESSAGE.lower().startswith("not yet")
    assert window.statusBar().currentMessage() == NOT_YET_MESSAGE
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(output, CodeEditor)
    assert output.toPlainText() == ""


def test_typing_enables_format_without_writing_output(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    qtbot.waitUntil(lambda: editor.hasFocus())
    qtbot.keyClicks(editor, "abc")
    assert _action(window, "Format").isEnabled()
    count = window.findChild(QLabel, "inputCount")
    assert count is not None
    assert count.text() == "1 line · 3 characters"
    title = window.findChild(QLabel, "inputEmptyTitle")
    assert title is not None and not title.isVisible()
    _action(window, "Format").trigger()
    status = window.findChild(QLabel, "statusMessage")
    assert status is not None
    qtbot.waitUntil(lambda: status.text() == "1 problem", timeout=15000)
    assert output.toPlainText() == ""
    clear = window.findChild(QPushButton, "clearButton")
    assert clear is not None
    clear.click()
    assert editor.toPlainText() == ""
    assert count.text() == "Empty"
    assert not _action(window, "Format").isEnabled()


def test_direction_follows_format_pickers(qtbot) -> None:
    window = _show(qtbot)
    output_format = window.findChild(QComboBox, "outputFormat")
    direction = window.findChild(QLabel, "directionLabel")
    assert output_format is not None
    assert direction is not None
    output_format.setCurrentText("YAML")
    assert direction.text() == "JSON → YAML"


def test_word_wrap_toggles_both_editors(qtbot) -> None:
    window = _show(qtbot)
    action = _action(window, "Word Wrap")
    editors = [
        window.findChild(CodeEditor, "inputEditor"),
        window.findChild(CodeEditor, "outputEditor"),
    ]
    assert all(isinstance(editor, CodeEditor) for editor in editors)
    assert action.isChecked()
    for editor in editors:
        assert isinstance(editor, CodeEditor)
        assert editor.lineWrapMode() == CodeEditor.LineWrapMode.WidgetWidth
    action.trigger()
    assert not action.isChecked()
    for editor in editors:
        assert isinstance(editor, CodeEditor)
        assert editor.lineWrapMode() == CodeEditor.LineWrapMode.NoWrap


def test_focus_caption_follows_the_editor(qtbot) -> None:
    window = _show(qtbot)
    output = window.findChild(CodeEditor, "outputEditor")
    caption = window.findChild(QLabel, "editingCaption")
    assert isinstance(output, CodeEditor)
    assert caption is not None
    output.setFocus()
    qtbot.waitUntil(lambda: caption.text() == "Editing <b>Output</b>")
    window.findChild(CodeEditor, "inputEditor").setFocus()
    qtbot.waitUntil(lambda: caption.text() == "Editing <b>Input</b>")


def test_focus_caption_reads_the_pane_titles(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(PaneRole, "INPUT", replace(PaneRole.INPUT, title="Original 1"))
    monkeypatch.setattr(PaneRole, "OUTPUT", replace(PaneRole.OUTPUT, title="Changed 1"))
    window = _show(qtbot)
    caption = window.findChild(QLabel, "editingCaption")
    assert caption is not None
    assert caption.text() == "Editing <b>Original 1</b>"
    window.findChild(CodeEditor, "outputEditor").setFocus()
    qtbot.waitUntil(lambda: caption.text() == "Editing <b>Changed 1</b>")
    window.findChild(CodeEditor, "inputEditor").setFocus()
    qtbot.waitUntil(lambda: caption.text() == "Editing <b>Original 1</b>")


def test_format_uses_the_worker_without_a_progress_bar(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    _action(window, "Format").trigger()
    assert not bar.isVisible()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()

    def _done() -> bool:
        assert not bar.isVisible()
        return output.toPlainText() != ""

    qtbot.waitUntil(_done, timeout=15000)
    assert output.toPlainText() == '{\n  "a": 1\n}\n'
    assert status.text() == "Valid JSON"
    assert not cancel.isVisible()
    assert not spinner.isVisible()
    assert button.text() == idle_text
    assert button.isEnabled()


def test_quick_format_hides_the_spinner_and_cancel(qtbot, monkeypatch) -> None:
    clock = _freeze_clock(monkeypatch)
    window = _show(qtbot)
    window.start_parse_worker()
    qtbot.waitUntil(
        lambda: (
            window._session.process is not None
            and window._session.process.pid is not None
        ),
        timeout=15000,
    )
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    clock["now"] = 1_000.0
    _action(window, "Format").trigger()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert not bar.isVisible()
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()

    def _done() -> bool:
        assert not spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        assert status.text() != FORMATTING_MESSAGE
        assert not status.text().startswith(f"{FORMATTING_MESSAGE} ")
        return output.toPlainText() != ""

    qtbot.waitUntil(_done, timeout=15000)
    assert output.toPlainText() == '{\n  "a": 1\n}\n'
    assert status.text() == "Valid JSON"
    assert button.text() == idle_text
    assert button.isEnabled()


def test_busy_indicator_appears_at_the_spinner_delay(qtbot, monkeypatch) -> None:
    clock = _freeze_clock(monkeypatch)
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    started = 1_000.0
    clock["now"] = started
    _action(window, "Format").trigger()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()
    clock["now"] = started + (SPINNER_REVEAL_MS - 1) / 1000
    window._apply_deferred_reveal()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert status.text() != FORMATTING_MESSAGE
    clock["now"] = started + SPINNER_REVEAL_MS / 1000
    window._apply_deferred_reveal()
    assert spinner.isVisible()
    assert not cancel.isVisible()
    assert not bar.isVisible()
    assert spinner.width() == SPINNER_WIDTH_PX
    assert spinner.accessibleName() == "Formatting"
    assert spinner.minimum() == 0
    assert spinner.maximum() == 0
    assert not spinner.isTextVisible()
    assert status.text() == FORMATTING_MESSAGE
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert editor.isReadOnly()


def test_write_bar_appears_after_the_progress_delay(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    clock = _freeze_clock(monkeypatch)
    window = _show(qtbot)
    window.start_parse_worker()
    session = window._session
    qtbot.waitUntil(
        lambda: session.process is not None and session.process.pid is not None,
        timeout=15000,
    )
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    assert _action(window, "Format").isEnabled()
    started = 2_000.0
    clock["now"] = started
    window._formatting = True
    window._format_started_s = started
    window._sync_format_button()
    destination = tmp_path / "out.json"
    destination.write_bytes(b"{}")
    try:
        text = json.dumps({"blob": "x" * 80000})
        session.submit(
            text,
            "json",
            "json",
            destination=str(destination),
            chunk_size=4096,
            chunk_pause_s=0.2,
        )
        qtbot.waitUntil(
            lambda: session.total > 0 and 0 < session.written < session.total,
            timeout=15000,
        )
        assert not spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        clock["now"] = started + SPINNER_REVEAL_MS / 1000
        window._apply_deferred_reveal()
        assert spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        assert status.text() == FORMATTING_MESSAGE
        clock["now"] = started + (PROGRESS_BAR_REVEAL_MS - 1) / 1000
        window._apply_deferred_reveal()
        assert not bar.isVisible()
        clock["now"] = started + PROGRESS_BAR_REVEAL_MS / 1000
        window._apply_deferred_reveal()
        assert bar.isVisible()
        assert not spinner.isVisible()
        assert cancel.isVisible()
        assert bar.width() == PROGRESS_WIDTH_PX
        assert bar.accessibleName() == "Formatting progress"
        assert bar.minimum() == 0
        assert bar.maximum() == 0
        assert not bar.isTextVisible()
        assert status.text() == FORMATTING_MESSAGE
        assert "%" not in status.text()
        assert button.text() == FORMATTING_MESSAGE
        assert button.isEnabled()
    finally:
        window.shutdown_parse_worker()


def _freeze_clock(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(
        "galley.app.main_window._monotonic",
        lambda: clock["now"],
    )
    return clock


def test_configure_runtime_leaves_start_method_unset() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import multiprocessing",
                    "from galley.app.runtime import configure_runtime",
                    "configure_runtime()",
                    "print(multiprocessing.get_start_method(allow_none=True))",
                ]
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "None"


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_entry_point_uses_freeze_support_without_global_start_method() -> None:
    runtime_tree = ast.parse((APP_ROOT / "runtime.py").read_text(encoding="utf-8"))
    configure_calls = [
        node
        for node in ast.walk(_function(runtime_tree, "configure_runtime"))
        if isinstance(node, ast.Call)
    ]
    assert any(
        _call_name(node) == "multiprocessing.freeze_support" for node in configure_calls
    )
    assert not any(
        _call_name(node) == "multiprocessing.set_start_method"
        for node in configure_calls
    )

    main_tree = ast.parse((APP_ROOT / "main.py").read_text(encoding="utf-8"))
    main_body = _function(main_tree, "main").body
    if (
        main_body
        and isinstance(main_body[0], ast.Expr)
        and isinstance(main_body[0].value, ast.Constant)
        and isinstance(main_body[0].value.value, str)
    ):
        main_body = main_body[1:]
    first_statement = main_body[0]
    assert isinstance(first_statement, ast.Expr)
    assert isinstance(first_statement.value, ast.Call)
    assert _call_name(first_statement.value) == "configure_runtime"


def test_format_click_while_working_does_not_start_another_job(
    qtbot, monkeypatch
) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(button, QPushButton)
    submitted: list[str] = []
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = (  # type: ignore[method-assign]
        lambda text, *_args, **_kwargs: submitted.append(text)
    )
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    button.click()
    assert submitted == ['{"a": 1}']
    assert button.isEnabled()
    assert button.text() == FORMATTING_MESSAGE
    button.click()
    _action(window, "Format").trigger()
    assert submitted == ['{"a": 1}']
    window._cancel_format()
    assert button.text() == idle_text
    assert button.isEnabled()


def _held_format(qtbot, monkeypatch):
    """A window whose Format job never finishes on its own."""
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(button, QPushButton)
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    editor.setPlainText('{"a": 1}')
    return window, button


def _assert_format_idle(window, button, idle_text: str) -> None:
    hint = (
        _action(window, "Format")
        .shortcut()
        .toString(QKeySequence.SequenceFormat.NativeText)
    )
    assert hint
    assert button.text() == idle_text
    assert button.accessibleName() == "Format"
    assert button.accessibleDescription() == ""
    assert button.toolTip() == f"Format ({hint})"
    assert not button.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
    assert button.cursor().shape() != Qt.CursorShape.BusyCursor


def _assert_format_busy(button) -> None:
    assert button.isEnabled()
    assert button.text() == FORMATTING_MESSAGE
    assert button.accessibleName() == FORMATTING_MESSAGE
    assert button.accessibleDescription() == FORMAT_BUSY_DESCRIPTION
    assert button.toolTip() == FORMAT_BUSY_TOOLTIP
    assert button.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
    assert button.cursor().shape() == Qt.CursorShape.BusyCursor


def _succeed(window) -> None:
    window._session.finished_job_id = window._accepted_job_id
    window._on_format_succeeded('{\n  "a": 1\n}\n')


def _fail(window) -> None:
    window._on_format_failed("The formatter stopped.")


def _cancel(window) -> None:
    window._cancel_format()


@pytest.mark.parametrize("finish", [_succeed, _fail, _cancel])
def test_format_busy_state_and_its_reset(qtbot, monkeypatch, finish) -> None:
    window, button = _held_format(qtbot, monkeypatch)
    idle_text = button.text()
    _assert_format_idle(window, button, idle_text)
    _action(window, "Format").trigger()
    assert window._formatting
    _assert_format_busy(button)
    finish(window)
    assert not window._formatting
    _assert_format_idle(window, button, idle_text)
    assert button.isEnabled()


def test_format_keeps_its_width_while_working(qtbot, monkeypatch) -> None:
    window, button = _held_format(qtbot, monkeypatch)
    idle_width = button.sizeHint().width()
    _action(window, "Format").trigger()
    assert button.text() == FORMATTING_MESSAGE
    assert button.sizeHint().width() == idle_width
    window.resize(window.width() - 200, window.height())
    qtbot.wait(10)
    assert button.sizeHint().width() == idle_width
    window._cancel_format()
    assert button.sizeHint().width() == idle_width


def test_clear_resets_status_problems_and_counts(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    counts = window.findChild(QLabel, "statusCounts")
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(editor, CodeEditor)
    assert status is not None and counts is not None
    assert isinstance(pane, ProblemsPane)
    editor.setPlainText('{\n  "price": 4.5\n  "qty": 3\n}\n')
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: status.text() == "1 problem", timeout=15000)
    qtbot.waitUntil(lambda: pane.isVisible() and pane.count() == 1, timeout=15000)
    assert editor.problem_lines() == {2}
    window.statusBar().set_counts("5 differences")
    assert counts.isVisible()

    clear = window.findChild(QPushButton, "clearButton")
    assert clear is not None
    clear.click()
    assert editor.toPlainText() == ""
    assert status.text() == "Ready"
    assert pane.count() == 0
    qtbot.waitUntil(lambda: not pane.isVisible())
    assert editor.problem_lines() == set()
    assert counts.text() == ""
    assert not counts.isVisible()


def test_whitespace_only_input_counts_as_empty(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    count = window.findChild(QLabel, "inputCount")
    title = window.findChild(QLabel, "inputEmptyTitle")
    format_button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert status is not None and count is not None and title is not None
    assert format_button is not None
    editor.setPlainText("abc")
    assert _action(window, "Format").isEnabled()
    assert _action(window, "Convert from Python").isEnabled()
    assert not title.isVisible()

    editor.setPlainText("  \n\t\n  ")
    assert count.text() == "Empty"
    assert title.isVisible()
    assert not format_button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert not _action(window, "Convert from Python").isEnabled()
    _action(window, "Format").trigger()
    qtbot.wait(200)
    assert status.text() == "Ready"
    _action(window, "Convert from Python").trigger()
    assert status.text() == "Ready"


def test_view_menu_holds_one_enter_full_screen_item(qtbot) -> None:
    window = _show(qtbot)
    view = _menu(window, "View")
    ours = [action for action in view.actions() if action.text() == "Enter Full Screen"]
    assert len(ours) == 1
    assert ours[0] is window._actions.full_screen
    assert ours[0].shortcut() == QKeySequence(QKeySequence.StandardKey.FullScreen)
    # macOS adds its own item to any menu titled View, so cocoa gets none of ours.
    assert _shows_own_full_screen_item("cocoa") is False
    assert _shows_own_full_screen_item("offscreen") is True
    assert _shows_own_full_screen_item("windows") is True
    assert _shows_own_full_screen_item("xcb") is True


def test_cocoa_leaves_enter_full_screen_to_the_system_menu(monkeypatch, qtbot) -> None:
    monkeypatch.setattr("galley.app.main_window._platform_name", lambda: "cocoa")
    window = _show(qtbot)
    view = _menu(window, "View")
    labels = _labels(view)
    assert "Enter Full Screen" not in labels
    assert labels[-1] == "Appearance"
    # The action still tracks the window state for the system item.
    assert window._actions.full_screen.isCheckable()
