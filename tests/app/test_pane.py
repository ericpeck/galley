"""Pane edge and keyboard focus ring."""

from PySide6.QtGui import QColor

from galley.app.pane import PaneRole, PaneWidget
from galley.app.theme import ACCENT, PANE_EDGE


def _left_edge_is(pane: PaneWidget, expected: str) -> bool:
    # Within 8 per channel, so fractional scaling cannot break the match.
    image = pane.grab().toImage()
    color = image.pixelColor(0, image.height() // 2)
    target = QColor(expected)
    return (
        abs(color.red() - target.red()) <= 8
        and abs(color.green() - target.green()) <= 8
        and abs(color.blue() - target.blue()) <= 8
    )


def test_pane_draws_its_edge_and_the_focus_ring(qtbot) -> None:
    pane = PaneWidget(PaneRole.INPUT)
    qtbot.addWidget(pane)
    pane.resize(480, 520)
    pane.show()
    qtbot.waitExposed(pane)

    assert _left_edge_is(pane, PANE_EDGE)
    pane.set_keyboard_focus(True)
    assert _left_edge_is(pane, ACCENT)
    pane.set_keyboard_focus(False)
    assert _left_edge_is(pane, PANE_EDGE)
