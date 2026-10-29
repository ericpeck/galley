"""Unbuilt roadmap actions stay visible, disabled, and marked Coming soon."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QMenu,
    QPushButton,
    QToolButton,
    QToolTip,
    QWidget,
)

from galley.app.actions import (
    _ACTION_ATTRS,
    COMING_SOON,
    UNBUILT_ACTIONS,
    is_unbuilt,
    main,
    not_yet_failure,
    release_failure,
)
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import NOT_YET_MESSAGE

_RELEASE_FIX = (
    "Build them or remove them from UNBUILT_ACTIONS in actions.py before tagging."
)
_NOT_YET_FIX = (
    "Build them or remove them from _not_yet_actions() in actions.py before tagging."
)
_NOT_YET_MENU_NAMES = (
    "Find and Replace…",
    "Outdent",
    "Output Options",
)
_RELEASE_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release-macos.yml"
)


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
    menu = window.findChild(QMenu, "menu" + title.replace(" ", ""))
    assert isinstance(menu, QMenu)
    return menu


def _visit_shell(window, note: Callable[[QAction], None]) -> None:
    """Visit commands in menu order: menu bar, then the toolbar."""

    def walk_menu(menu: QMenu) -> None:
        children = menu.findChildren(
            QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly
        )
        child_by_action = {id(child.menuAction()): child for child in children}
        for action in menu.actions():
            note(action)
            child = child_by_action.get(id(action))
            if child is not None:
                walk_menu(child)

    def walk_toolbar(widget: QWidget) -> None:
        layout = widget.layout()
        if layout is None:
            return
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            child = item.widget()
            if child is None:
                continue
            if isinstance(child, QToolButton):
                action = child.defaultAction()
                if isinstance(action, QAction):
                    note(action)
            walk_toolbar(child)

    for title in [action.text() for action in window.menuBar().actions()]:
        walk_menu(_menu(window, title))
    toolbar = window.findChild(QWidget, "mainToolbar")
    assert isinstance(toolbar, QWidget)
    walk_toolbar(toolbar)


def _menu_order(window) -> list[str]:
    """Unbuilt names as they first appear: menu bar, then the toolbar."""
    found: list[str] = []

    def note(action: QAction) -> None:
        name = action.property("unbuiltName")
        if isinstance(name, str) and name not in found:
            found.append(name)

    _visit_shell(window, note)
    return found


def _visit_output_footer(window, note: Callable[[QAction], None]) -> None:
    """Visit the Output pane footer's Options link by the command it triggers."""
    button = window.findChild(QPushButton, "outputOptionsButton")
    assert isinstance(button, QPushButton)
    fired: list[QAction] = []
    slots = []
    for action in window.findChildren(QAction):

        def slot(_checked: bool = False, action: QAction = action) -> None:
            fired.append(action)

        action.triggered.connect(slot)
        slots.append((action, slot))
    button.click()
    for action, slot in slots:
        action.triggered.disconnect(slot)
    for action in fired:
        note(action)


def _not_yet_menu_order(window) -> list[str]:
    """Not yet release labels as they first appear: menu bar, toolbar, footer."""
    pending = {id(action) for action in window._actions._not_yet_actions()}
    found: list[str] = []

    def note(action: QAction) -> None:
        if id(action) not in pending:
            return
        found.append(action.text())
        pending.remove(id(action))

    _visit_shell(window, note)
    _visit_output_footer(window, note)
    assert not pending
    return found


def _release_text(names: str) -> str:
    return (
        'Release blocked: these actions still show "Coming soon": '
        f"{names}.\n{_RELEASE_FIX}"
    )


def _not_yet_text(names: str) -> str:
    return (
        f'Release blocked: these actions still show "Not yet": {names}.\n{_NOT_YET_FIX}'
    )


def _clear_not_yet(monkeypatch) -> None:
    monkeypatch.setattr(
        "galley.app.actions.ShellActions._not_yet_actions",
        lambda _self: [],
    )


def test_release_check_message_is_exact_and_keeps_menu_order() -> None:
    message = release_failure(("Open", "Find"))
    assert message == (
        'Release blocked: these actions still show "Coming soon": Open, Find.\n'
        "Build them or remove them from UNBUILT_ACTIONS in actions.py before tagging."
    )
    assert message == _release_text("Open, Find")
    assert message is not None
    assert message.splitlines() == [
        'Release blocked: these actions still show "Coming soon": Open, Find.',
        _RELEASE_FIX,
    ]
    assert "Coming soon." not in message


def test_not_yet_release_check_message_is_exact_and_keeps_order() -> None:
    message = not_yet_failure(("Escape as String", "Outdent"))
    assert message == _not_yet_text("Escape as String, Outdent")
    assert message is not None
    assert message.splitlines() == [
        'Release blocked: these actions still show "Not yet": '
        "Escape as String, Outdent.",
        _NOT_YET_FIX,
    ]
    assert not_yet_failure(()) is None


def test_release_check_passes_when_both_lists_are_empty(monkeypatch, capsys) -> None:
    monkeypatch.setattr("galley.app.actions.UNBUILT_ACTIONS", ())
    _clear_not_yet(monkeypatch)
    assert release_failure() is None
    assert release_failure(()) is None
    assert not_yet_failure(()) is None
    assert not_yet_failure() is None
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_release_check_fails_when_only_coming_soon_remains(monkeypatch, capsys) -> None:
    _clear_not_yet(monkeypatch)
    assert not_yet_failure() is None
    message = release_failure()
    assert message == _release_text(", ".join(UNBUILT_ACTIONS))
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == message + "\n"
    assert "Not yet" not in captured.out


def test_release_check_fails_when_only_not_yet_remains(
    monkeypatch, qtbot, capsys
) -> None:
    monkeypatch.setattr("galley.app.actions.UNBUILT_ACTIONS", ())
    window = _show(qtbot)
    names = _not_yet_menu_order(window)
    assert names == list(_NOT_YET_MENU_NAMES)
    assert release_failure() is None
    message = not_yet_failure()
    assert message == _not_yet_text(", ".join(names))
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == message + "\n"
    assert "Coming soon" not in captured.out


def test_release_check_fails_when_both_lists_remain(qtbot, capsys) -> None:
    window = _show(qtbot)
    coming = _menu_order(window)
    not_yet = _not_yet_menu_order(window)
    assert coming == list(UNBUILT_ACTIONS)
    assert not_yet == list(_NOT_YET_MENU_NAMES)
    assert coming
    assert not_yet
    coming_message = release_failure()
    not_yet_message = not_yet_failure()
    assert coming_message == _release_text(", ".join(coming))
    assert not_yet_message == _not_yet_text(", ".join(not_yet))
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"{coming_message}\n{not_yet_message}\n"


def test_output_options_link_triggers_its_not_yet_action(qtbot) -> None:
    window = _show(qtbot)
    button = window.findChild(QPushButton, "outputOptionsButton")
    assert isinstance(button, QPushButton)
    assert button.text() == "Options"
    options = window._actions.output_options
    assert options.text() == "Output Options"
    assert options in window._actions._not_yet_actions()
    assert options in window._actions.stubs()
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(output, CodeEditor)
    fired: list[bool] = []
    options.triggered.connect(lambda _checked=False: fired.append(True))
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert fired == [True]
    assert window.statusBar().currentMessage() == NOT_YET_MESSAGE
    assert output.toPlainText() == ""


def test_tag_release_runs_the_unbuilt_action_check_after_copyright() -> None:
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    notice = workflow.find("- name: Check copyright notice")
    unbuilt = workflow.find("- name: Check unbuilt actions")
    build = workflow.find("- name: Build Galley.app")
    assert notice != -1
    assert notice < unbuilt < build
    block = workflow[unbuilt:build]
    assert block.startswith("- name: Check unbuilt actions\n")
    assert "if: startsWith(github.ref, 'refs/tags/v')" in block
    assert (
        "uv run --frozen --no-dev --no-editable python -m galley.app.actions" in block
    )
    build_step = workflow[build : workflow.find("- name: Check release version")]
    assert "\n        if:" not in build_step
    assert "workflow_dispatch:" in workflow


def test_unbuilt_actions_are_visible_disabled_and_marked(qtbot) -> None:
    window = _show(qtbot)
    marked = [action for action in window.findChildren(QAction) if is_unbuilt(action)]
    assert marked
    names = [action.property("unbuiltName") for action in marked]
    assert set(names) == set(UNBUILT_ACTIONS)
    for action in marked:
        assert action.isVisible()
        assert not action.isEnabled()
        assert action.toolTip() == COMING_SOON
        assert action.text() != COMING_SOON
        assert not action.text().endswith("Coming soon")

    for title in [action.text() for action in window.menuBar().actions()]:
        for action in _menu(window, title).actions():
            if not is_unbuilt(action):
                continue
            assert action.isVisible()
            assert not action.isEnabled()
            assert action.toolTip() == COMING_SOON


def _coming_soon_tip(button: QWidget) -> str:
    """Icon-only unbuilt buttons keep the action name in the tooltip."""
    if isinstance(button, QToolButton):
        action = button.defaultAction()
        icon_only = button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        if isinstance(action, QAction) and is_unbuilt(action) and icon_only:
            return f"{action.text()} — coming soon"
    return COMING_SOON


def test_unbuilt_buttons_show_coming_soon(qtbot) -> None:
    window = _show(qtbot)
    buttons: list[QWidget] = [
        button
        for button in window.findChildren(QToolButton)
        if isinstance(button.defaultAction(), QAction)
        and is_unbuilt(button.defaultAction())
    ]
    pushed = [
        button
        for button in window.findChildren(QPushButton)
        if button.toolTip() == COMING_SOON
    ]
    # The tab strips' Close and New tab buttons were the last ones.
    assert pushed == []
    open_button = window.findChild(QPushButton, "emptyOpenButton")
    assert isinstance(open_button, QPushButton)
    assert open_button.isEnabled()
    assert open_button.toolTip() != COMING_SOON

    seen: set[str] = set()
    for button in buttons:
        assert button.isVisible()
        assert not button.isEnabled()
        expected = _coming_soon_tip(button)
        assert button.toolTip() == expected
        assert button.accessibleDescription() == COMING_SOON
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus
        host = button.parentWidget()
        assert isinstance(host, QWidget)
        if host.property("comingSoonHost") is True:
            assert host.isEnabled()
            assert host.toolTip() == expected
            assert host.accessibleDescription() == COMING_SOON
        action = button.defaultAction() if isinstance(button, QToolButton) else None
        if isinstance(action, QAction):
            seen.add(action.property("unbuiltName"))
            if button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
                assert button.accessibleName() == expected
        QToolTip.hideText()
        center = QPoint(max(button.width() // 2, 0), max(button.height() // 2, 0))
        qtbot.mouseMove(button, center)
        qtbot.waitUntil(lambda tip=expected: QToolTip.text() == tip, timeout=2000)
    hosts = [
        button.parentWidget() for button in buttons if isinstance(button, QToolButton)
    ]
    assert hosts
    assert all(
        host is not None and host.property("comingSoonHost") is True for host in hosts
    )
    assert seen == {"Indent"}


def test_unbuilt_shortcuts_do_nothing_and_write_nothing(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    qtbot.waitUntil(lambda: editor.hasFocus())
    status = window.statusBar().currentMessage()
    for action in window.findChildren(QAction):
        if is_unbuilt(action):
            action.trigger()
    qtbot.keyClick(editor, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(editor, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == ""
    assert output.toPlainText() == ""
    assert window.statusBar().currentMessage() == status
    assert status != NOT_YET_MESSAGE


def test_built_actions_are_unaffected(qtbot) -> None:
    window = _show(qtbot)
    for text in ("Show Problems", "Stack Panes", "Paste", "Open file…"):
        action = _action(window, text)
        assert action.isEnabled()
        assert action.toolTip() != COMING_SOON
        assert not is_unbuilt(action)
    undo = _action(window, "Undo")
    assert undo.toolTip() != COMING_SOON
    assert not is_unbuilt(undo)
    assert not undo.isEnabled()
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    qtbot.keyClicks(editor, "{}")
    assert undo.isEnabled()
    for text in ("Format", "Convert from Python"):
        action = _action(window, text)
        assert action.isEnabled()
        assert action.toolTip() != COMING_SOON
        assert not is_unbuilt(action)
    convert = _action(window, "Convert from Python")
    buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is convert
    ]
    assert len(buttons) == 1
    assert buttons[0].isEnabled()
    host = buttons[0].parentWidget()
    assert isinstance(host, QWidget)
    assert host.toolTip() != COMING_SOON


def test_every_stub_action_is_covered_by_an_unbuilt_entry(qtbot) -> None:
    window = _show(qtbot)
    actions = window._actions
    covered: dict[int, str] = {}
    for name in UNBUILT_ACTIONS:
        for attr in _ACTION_ATTRS[name]:
            action = getattr(actions, attr)
            covered[id(action)] = name
    missing = [
        action.text() for action in actions._stub_actions() if id(action) not in covered
    ]
    assert missing == []
    for action in actions._stub_actions():
        assert action.property("unbuiltName") == covered[id(action)]
        assert action.property("unbuiltName") in UNBUILT_ACTIONS
    message = release_failure()
    assert message == _release_text(", ".join(UNBUILT_ACTIONS))
    assert message is not None
    not_yet = {id(action) for action in actions._not_yet_actions()}
    assert not_yet
    assert not_yet.isdisjoint(covered)
    assert {id(action) for action in actions.stubs()} == not_yet


def test_deleting_a_name_from_the_list_turns_that_action_on(monkeypatch, qtbot) -> None:
    monkeypatch.setattr("galley.app.actions.UNBUILT_ACTIONS", ("Open",))
    window = _show(qtbot)
    opened = _action(window, "Open file…")
    assert not opened.isEnabled()
    assert opened.toolTip() == COMING_SOON
    find = _action(window, "Find…")
    assert find.isEnabled()
    assert find.toolTip() != COMING_SOON
    assert not is_unbuilt(find)


def test_expand_and_collapse_all_shipped(qtbot) -> None:
    """#64 built Expand all and Collapse all, so the release check drops them."""
    window = _show(qtbot)
    actions = window._actions
    assert "Expand" not in UNBUILT_ACTIONS
    stubs = {id(action) for action in actions._stub_actions()}
    for action in (actions.expand_all, actions.collapse_all):
        assert not is_unbuilt(action)
        assert id(action) not in stubs
        assert action.toolTip() != COMING_SOON
    message = release_failure()
    assert message is not None
    assert "Expand" not in message
    assert "Collapse" not in message
    assert "Indent" in message


def test_indent_size_picker_is_coming_soon_until_indent_ships(qtbot) -> None:
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "indentSize")
    assert isinstance(picker, QComboBox)
    assert picker.isVisible()
    assert not picker.isEnabled()
    assert picker.toolTip() == COMING_SOON
    assert picker.accessibleDescription() == COMING_SOON
    assert picker.focusPolicy() == Qt.FocusPolicy.NoFocus
    host = picker.parentWidget()
    assert isinstance(host, QWidget)
    assert host.property("comingSoonHost") is True
    assert host.isEnabled()
    assert host.toolTip() == COMING_SOON
    submenu = window.findChild(QMenu, "indentSizeMenu")
    assert isinstance(submenu, QMenu)
    assert not submenu.menuAction().isEnabled()
    assert submenu.menuAction().toolTip() == COMING_SOON


def test_indent_size_picker_turns_on_with_indent(monkeypatch, qtbot) -> None:
    without_indent = tuple(name for name in UNBUILT_ACTIONS if name != "Indent")
    monkeypatch.setattr("galley.app.actions.UNBUILT_ACTIONS", without_indent)
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "indentSize")
    assert isinstance(picker, QComboBox)
    assert picker.isEnabled()
    assert picker.toolTip() == "Indent size"
    assert picker.accessibleDescription() == ""
    submenu = window.findChild(QMenu, "indentSizeMenu")
    assert isinstance(submenu, QMenu)
    assert submenu.menuAction().isEnabled()


def test_release_check_lists_output_options_last_under_not_yet() -> None:
    coming = release_failure()
    not_yet = not_yet_failure()
    assert coming is not None
    assert not_yet == _not_yet_text(", ".join(_NOT_YET_MENU_NAMES))
    assert "Output Options" not in coming


def test_release_check_no_longer_lists_preferences() -> None:
    """Release-check PR 2a removed Preferences instead of building it."""
    assert "Preferences" not in UNBUILT_ACTIONS
    assert "Preferences" not in _ACTION_ATTRS
    coming = release_failure()
    not_yet = not_yet_failure()
    assert coming is not None
    assert not_yet is not None
    assert "Preferences" not in coming
    assert "Preferences" not in not_yet


def test_release_check_no_longer_lists_guides() -> None:
    """Release-check PR 2b removed Indent Guides instead of building them."""
    assert "Guides" not in UNBUILT_ACTIONS
    assert "Guides" not in _ACTION_ATTRS
    coming = release_failure()
    not_yet = not_yet_failure()
    assert coming is not None
    assert not_yet is not None
    assert "Guides" not in coming
    assert "Guides" not in not_yet


def test_release_check_no_longer_lists_pop_out() -> None:
    """Release-check PR 2c removed Pop out instead of building it."""
    assert "Pop out" not in UNBUILT_ACTIONS
    assert "Pop out" not in _ACTION_ATTRS
    coming = release_failure()
    not_yet = not_yet_failure()
    assert coming is not None
    assert not_yet is not None
    for name in ("Pop out", "Show window", "Bring back"):
        assert name not in coming
        assert name not in not_yet


def test_release_check_no_longer_lists_tabs() -> None:
    """Release-check PR 2d removed Tabs instead of building them."""
    assert "Tabs" not in UNBUILT_ACTIONS
    assert "Tabs" not in _ACTION_ATTRS
    coming = release_failure()
    not_yet = not_yet_failure()
    assert coming is not None
    assert not_yet is not None
    for name in (
        "Tabs",
        "Rename",
        "Duplicate",
        "Close others",
        "Close tabs to the right",
        "New tab",
    ):
        assert name not in coming
        assert name not in not_yet


# Menu order: File, then the View section after Previous Problem.
_COMPARE_UNBUILT = (
    "Open Right Document…",
    "Compare",
    "Next Change",
    "Previous Change",
    "Link Scrolling",
)


def test_compare_items_stay_unbuilt_until_they_ship(qtbot, capsys) -> None:
    """#65 slices 3d and 3e stay on the release check until they ship.

    Open Right Document… uses one U+2026 ellipsis. None of the five has a
    shortcut. Compare is the only checkable item. Each one is disabled and
    says Coming soon.
    """
    missing = [name for name in _COMPARE_UNBUILT if name not in UNBUILT_ACTIONS]
    assert missing == []
    open_right = _COMPARE_UNBUILT[0]
    assert open_right.endswith("\u2026")
    assert not open_right.endswith("...")
    for name in _COMPARE_UNBUILT:
        assert name in _ACTION_ATTRS

    window = _show(qtbot)
    actions = window._actions
    wired = {
        "Open Right Document…": actions.open_right_document,
        "Compare": actions.compare,
        "Next Change": actions.next_change,
        "Previous Change": actions.previous_change,
        "Link Scrolling": actions.link_scrolling,
    }
    file_menu = _menu(window, "File")
    file_items = file_menu.actions()
    open_at = [item.text() for item in file_items].index("Open file…")
    assert file_items[open_at + 1] is actions.open_right_document

    view = _menu(window, "View")
    view_items = view.actions()
    view_labels = ["|" if item.isSeparator() else item.text() for item in view_items]
    previous = view_labels.index("Previous Problem")
    assert view_labels[previous : previous + 7] == [
        "Previous Problem",
        "|",
        "Compare",
        "Next Change",
        "Previous Change",
        "Link Scrolling",
        "|",
    ]
    section = view_items[previous + 2 : previous + 6]
    assert section == [
        actions.compare,
        actions.next_change,
        actions.previous_change,
        actions.link_scrolling,
    ]
    assert view_items[previous + 1].isSeparator()
    assert view_items[previous + 6].isSeparator()

    assert actions.compare.isCheckable()
    assert not actions.compare.isChecked()
    for name, action in wired.items():
        assert action.text() == name
        assert action in actions._stub_actions()
        assert action not in actions._not_yet_actions()
        assert action.isVisible()
        assert not action.isEnabled()
        assert action.toolTip() == COMING_SOON
        assert is_unbuilt(action)
        assert action.property("unbuiltName") == name
        assert action.menuRole() == QAction.MenuRole.NoRole
        assert action.shortcut().isEmpty()
        assert list(action.shortcuts()) == []
        if name != "Compare":
            assert not action.isCheckable()
    assert actions.open_right_document.text()[-1] == "\u2026"
    assert "..." not in actions.open_right_document.text()

    message = release_failure()
    assert message is not None
    assert ", ".join(_COMPARE_UNBUILT) in message
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert message in captured.out
    for name in _COMPARE_UNBUILT:
        assert name in captured.out
