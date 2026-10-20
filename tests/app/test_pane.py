"""Pane edge and keyboard focus ring."""

from PySide6.QtGui import QColor

from galley.app.pane import PaneRole, PaneWidget
from galley.app.theme import ACCENT, PANE_EDGE


def _left_edge(pane: PaneWidget) -> QColor:
    image = pane.grab().toImage()
    return image.pixelColor(0, image.height() // 2)


def test_pane_draws_its_edge_and_the_focus_ring(qtbot) -> None:
    pane = PaneWidget(PaneRole.INPUT)
    qtbot.addWidget(pane)
    pane.resize(480, 520)
    pane.show()
    qtbot.waitExposed(pane)

    assert _left_edge(pane) == QColor(PANE_EDGE)
    pane.set_keyboard_focus(True)
    assert _left_edge(pane) == QColor(ACCENT)
    pane.set_keyboard_focus(False)
    assert _left_edge(pane) == QColor(PANE_EDGE)
