"""Tips ``set_not_available`` shows after an action is available again (#144).

The menu tooltip and the status bar are what a person reads. Offscreen
ignores the OS appearance, so the test sets light and dark itself.
"""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu, QToolTip

from galley.app.actions import set_not_available
from galley.app.theme import is_dark

_ORIGINAL_TIP = "Sort object keys"
_ORIGINAL_STATUS = "Puts object keys in order"
_CHANGED_TIP = "Sort object keys A to Z"
_CHANGED_STATUS = "Alphabetical key order"
_XML_REASON = "Not available for XML"
_HTML_REASON = "Not available for HTML"


def _window(qtbot) -> tuple[QMainWindow, QMenu, QAction]:
    window = QMainWindow()
    qtbot.addWidget(window)
    window.resize(480, 320)
    window.statusBar()
    action = QAction("Sort keys", window)
    action.setToolTip(_ORIGINAL_TIP)
    action.setStatusTip(_ORIGINAL_STATUS)
    menu = window.menuBar().addMenu("Edit")
    menu.setToolTipsVisible(True)
    menu.addAction(action)
    window.show()
    qtbot.waitExposed(window)
    return window, menu, action


def _shown_tips(
    qtbot, window: QMainWindow, menu: QMenu, action: QAction
) -> tuple[str, str]:
    """Tooltip and status-bar text for ``action`` while the menu is open.

    ``set_not_available`` does not enable or disable the action. The action
    stays enabled so the menu can highlight it: a disabled item is skipped,
    and then the status bar never receives the status tip.
    """
    QToolTip.hideText()
    menu.close()
    qtbot.mouseMove(window, QPoint(200, 200))
    menu.popup(window.mapToGlobal(QPoint(8, window.menuBar().height() + 4)))
    try:
        qtbot.waitUntil(menu.isVisible)
        qtbot.mouseMove(menu, menu.actionGeometry(action).center())
        qtbot.waitUntil(
            lambda: (
                QToolTip.isVisible()
                and QToolTip.text() == action.toolTip()
                and window.statusBar().currentMessage() == action.statusTip()
            ),
            timeout=2000,
        )
        return QToolTip.text(), window.statusBar().currentMessage()
    finally:
        menu.close()
        QToolTip.hideText()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_clearing_the_reason_keeps_tips_changed_while_available(
    qtbot, install_scheme, scheme: str
) -> None:
    palette = install_scheme(scheme)
    assert is_dark(palette) == (scheme == "dark")
    window, menu, action = _window(qtbot)
    assert is_dark(window.palette()) == (scheme == "dark")

    def shown(tooltip: str, status: str, state: str) -> None:
        assert _shown_tips(qtbot, window, menu, action) == (tooltip, status), state

    shown(_ORIGINAL_TIP, _ORIGINAL_STATUS, "available")
    set_not_available(action, _XML_REASON)
    shown(_XML_REASON, _XML_REASON, "unavailable")
    set_not_available(action, _HTML_REASON)
    shown(_HTML_REASON, _HTML_REASON, "reason changed while unavailable")
    set_not_available(action, "")
    shown(_ORIGINAL_TIP, _ORIGINAL_STATUS, "available again")

    action.setToolTip(_CHANGED_TIP)
    action.setStatusTip(_CHANGED_STATUS)
    shown(_CHANGED_TIP, _CHANGED_STATUS, "tips changed while available")
    set_not_available(action, _XML_REASON)
    shown(_XML_REASON, _XML_REASON, "unavailable again")
    set_not_available(action, "")
    shown(_CHANGED_TIP, _CHANGED_STATUS, "available after the tip change")
