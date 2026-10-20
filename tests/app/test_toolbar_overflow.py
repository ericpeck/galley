"""Format stays fully visible; secondary labels are never cut mid-word."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from galley.app.actions import COMING_SOON, is_unbuilt
from galley.app.main import create_main_window

_PERMANENT_ICON = frozenset({"Outdent", "Indent", "Expand all", "Collapse all"})


def _show(qtbot, width: int):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.resize(width, 800)
    toolbar = window.findChild(QWidget, "mainToolbar")
    assert isinstance(toolbar, QWidget)
    qtbot.waitUntil(lambda: window.width() == width and toolbar.width() == width)
    return window, toolbar


def _fully_inside(widget: QWidget, ancestor: QWidget) -> bool:
    if not widget.isVisible():
        return False
    bounds = ancestor.rect()
    top_left = widget.mapTo(ancestor, widget.rect().topLeft())
    bottom_right = widget.mapTo(ancestor, widget.rect().bottomRight())
    return bounds.contains(top_left) and bounds.contains(bottom_right)


def _secondary_buttons(toolbar: QWidget) -> list[QToolButton]:
    buttons: list[QToolButton] = []
    for button in toolbar.findChildren(QToolButton):
        if button.objectName() == "toolbarOverflow":
            continue
        if isinstance(button.defaultAction(), QAction):
            buttons.append(button)
    return buttons


def _text_is_squeezed(button: QToolButton) -> bool:
    """True when a visible label is narrower than the text it is meant to draw."""
    if button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
        return False
    return button.width() < button.sizeHint().width()


def _overflow_button(toolbar: QWidget) -> QToolButton:
    button = toolbar.findChild(QToolButton, "toolbarOverflow")
    assert isinstance(button, QToolButton)
    return button


def _assert_same_action(action: QAction) -> None:
    hits: list[QAction] = []

    def remember(checked: bool = False) -> None:
        del checked
        hits.append(action)

    action.triggered.connect(remember)
    try:
        action.trigger()
        if action.isEnabled():
            assert hits == [action]
        else:
            assert hits == []
    finally:
        action.triggered.disconnect(remember)


@pytest.mark.parametrize("width", [1280, 1024])
def test_format_stays_visible_and_labels_are_not_elided(qtbot, width: int) -> None:
    window, toolbar = _show(qtbot, width)
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(button, QPushButton)
    assert _fully_inside(button, window)
    assert _fully_inside(button, toolbar)
    assert button.width() >= button.sizeHint().width()
    assert "Format" in button.text()

    overflow = _overflow_button(toolbar)
    menu = overflow.menu()
    assert menu is not None
    overflowed: list[QAction] = []
    for tool in _secondary_buttons(toolbar):
        action = tool.defaultAction()
        assert isinstance(action, QAction)
        if not tool.isVisible() or not _fully_inside(tool, toolbar):
            overflowed.append(action)
            continue
        assert _fully_inside(tool, toolbar)
        if tool.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            assert tool.accessibleName() == action.text()
            if is_unbuilt(action):
                assert tool.toolTip() == COMING_SOON
            elif action.text() not in _PERMANENT_ICON:
                assert tool.toolTip() == action.text()
        else:
            assert not _text_is_squeezed(tool)

    if overflowed:
        assert overflow.isVisible()
        assert overflow.text() == "»"
        for action in overflowed:
            matches = [item for item in menu.actions() if item is action]
            assert matches == [action]
            _assert_same_action(action)
    else:
        assert not overflow.isVisible()
        assert menu.actions() == []


def _actions_in_toolbar_order(toolbar: QWidget) -> list[tuple[QToolButton, QAction]]:
    found: list[tuple[QToolButton, QAction]] = []

    def walk(widget: QWidget) -> None:
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
            if (
                isinstance(child, QToolButton)
                and child.objectName() != "toolbarOverflow"
            ):
                action = child.defaultAction()
                if isinstance(action, QAction):
                    found.append((child, action))
            walk(child)

    walk(toolbar)
    return found


def test_overflow_menu_triggers_the_same_actions(qtbot) -> None:
    """A row that cannot fit still reaches each hidden action through »."""
    window, toolbar = _show(qtbot, 1024)
    window.setMinimumWidth(280)
    width = 1024
    overflow = _overflow_button(toolbar)

    def _enabled_hidden() -> bool:
        return any(
            action.isEnabled()
            for button, action in _actions_in_toolbar_order(toolbar)
            if not button.isVisible()
        )

    while width > 280:
        window.resize(width, 800)
        qtbot.waitUntil(lambda: toolbar.width() == window.width())
        button = window.findChild(QPushButton, "formatButton")
        assert isinstance(button, QPushButton)
        if (
            overflow.isVisible()
            and _enabled_hidden()
            and button.width() >= button.sizeHint().width()
            and _fully_inside(button, window)
        ):
            break
        width -= 40
    assert overflow.isVisible()
    assert overflow.text() == "»"
    assert _fully_inside(window.findChild(QPushButton, "formatButton"), window)

    ordered = _actions_in_toolbar_order(toolbar)
    hidden = [action for button, action in ordered if not button.isVisible()]
    shown = [action for button, action in ordered if button.isVisible()]
    menu = overflow.menu()
    assert menu is not None
    assert list(menu.actions()) == hidden
    assert hidden
    assert any(action.isEnabled() for action in hidden)
    for action in hidden:
        _assert_same_action(action)
    for action in shown:
        assert all(item is not action for item in menu.actions())
