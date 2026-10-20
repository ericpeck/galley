"""Unbuilt roadmap actions stay visible, disabled, and marked Coming soon."""

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QPushButton, QToolButton, QToolTip, QWidget

from galley.app.actions import (
    _ACTION_ATTRS,
    COMING_SOON,
    UNBUILT_ACTIONS,
    is_unbuilt,
    main,
    release_failure,
)
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import NOT_YET_MESSAGE

_RELEASE_FIX = (
    "Build them or remove them from UNBUILT_ACTIONS in actions.py before tagging."
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


def _note(action: QAction, found: list[str]) -> None:
    name = action.property("unbuiltName")
    if isinstance(name, str) and name not in found:
        found.append(name)


def _walk_menu(menu: QMenu, found: list[str]) -> None:
    children = menu.findChildren(
        QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly
    )
    child_by_action = {id(child.menuAction()): child for child in children}
    for action in menu.actions():
        _note(action, found)
        child = child_by_action.get(id(action))
        if child is not None:
            _walk_menu(child, found)


def _walk_toolbar(widget: QWidget, found: list[str]) -> None:
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
                _note(action, found)
        _walk_toolbar(child, found)


def _menu_order(window) -> list[str]:
    """Unbuilt names as they first appear: menu bar, toolbar, tab menu."""
    found: list[str] = []
    for title in [action.text() for action in window.menuBar().actions()]:
        _walk_menu(_menu(window, title), found)
    toolbar = window.findChild(QWidget, "mainToolbar")
    assert isinstance(toolbar, QWidget)
    _walk_toolbar(toolbar, found)
    tab_menu = window.findChild(QMenu, "tabMenu")
    assert isinstance(tab_menu, QMenu)
    _walk_menu(tab_menu, found)
    return found


def _release_text(names: str) -> str:
    return (
        'Release blocked: these actions still show "Coming soon": '
        f"{names}.\n{_RELEASE_FIX}"
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


def test_release_check_passes_when_the_list_is_empty(monkeypatch, capsys) -> None:
    monkeypatch.setattr("galley.app.actions.UNBUILT_ACTIONS", ())
    assert release_failure() is None
    assert release_failure(()) is None
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_release_check_fails_with_the_live_list_in_menu_order(qtbot, capsys) -> None:
    window = _show(qtbot)
    order = _menu_order(window)
    assert order == list(UNBUILT_ACTIONS)
    assert order
    message = release_failure()
    assert message == _release_text(", ".join(order))
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == message + "\n"


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
    assert len(pushed) == 5
    assert {button.objectName() for button in pushed if button.objectName()} == {
        "emptyOpenButton"
    }
    assert sorted(
        button.accessibleName() for button in pushed if button.accessibleName()
    ) == ["Close", "Close", "New tab", "New tab"]
    buttons.extend(pushed)

    seen: set[str] = set()
    for button in buttons:
        assert button.isVisible()
        assert not button.isEnabled()
        assert button.toolTip() == COMING_SOON
        assert button.accessibleDescription() == COMING_SOON
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus
        host = button.parentWidget()
        assert isinstance(host, QWidget)
        if host.property("comingSoonHost") is True:
            assert host.isEnabled()
            assert host.toolTip() == COMING_SOON
            assert host.accessibleDescription() == COMING_SOON
        action = button.defaultAction() if isinstance(button, QToolButton) else None
        if isinstance(action, QAction):
            seen.add(action.property("unbuiltName"))
        QToolTip.hideText()
        center = QPoint(max(button.width() // 2, 0), max(button.height() // 2, 0))
        qtbot.mouseMove(button, center)
        qtbot.waitUntil(lambda: QToolTip.text() == COMING_SOON, timeout=2000)
    hosts = [
        button.parentWidget() for button in buttons if isinstance(button, QToolButton)
    ]
    assert hosts
    assert all(
        host is not None and host.property("comingSoonHost") is True for host in hosts
    )
    assert seen == {"Find", "Indent", "Sort keys", "Minify", "Expand", "Validate"}


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
    qtbot.keyClick(editor, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(editor, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(editor, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == ""
    assert output.toPlainText() == ""
    assert window.statusBar().currentMessage() == status
    assert status != NOT_YET_MESSAGE


def test_built_actions_are_unaffected(qtbot) -> None:
    window = _show(qtbot)
    for text in ("Show Problems", "Stack Panes", "Paste", "Undo"):
        action = _action(window, text)
        assert action.isEnabled()
        assert action.toolTip() != COMING_SOON
        assert not is_unbuilt(action)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    qtbot.keyClicks(editor, "{}")
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
    collapse = actions.collapse_all
    assert covered[id(collapse)] == "Expand"
    assert collapse.property("unbuiltName") == "Expand"
    assert collapse.toolTip() == COMING_SOON
    assert not collapse.isEnabled()
    assert collapse.isVisible()
    assert "Collapse" not in UNBUILT_ACTIONS
    message = release_failure()
    assert message == _release_text(", ".join(UNBUILT_ACTIONS))
    assert message is not None
    assert message.count("Expand") == 1
    assert "Collapse" not in message
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
