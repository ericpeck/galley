"""Pane edge and keyboard focus ring."""

import pytest
from PySide6.QtGui import QColor, QPalette

from galley.app.pane import PaneRole, PaneWidget
from galley.app.theme import composite, contrast_ratio


def _left_edge(pane: PaneWidget) -> QColor:
    image = pane.grab().toImage()
    return image.pixelColor(0, image.height() // 2)


def test_pane_edge_and_focus_ring_reach_three_to_one(qtbot, scheme_palette) -> None:
    pane = PaneWidget(PaneRole.INPUT)
    qtbot.addWidget(pane)
    pane.resize(480, 520)
    pane.show()
    qtbot.waitExposed(pane)
    window = scheme_palette.color(QPalette.ColorRole.Window)
    base = composite(scheme_palette.color(QPalette.ColorRole.Base), window)

    edge = _left_edge(pane)
    pane.set_keyboard_focus(True)
    ring = _left_edge(pane)
    pane.set_keyboard_focus(False)
    assert _left_edge(pane) == edge
    assert ring != edge

    for color in (edge, ring):
        assert contrast_ratio(color, window) >= 3
        assert contrast_ratio(color, base) >= 3


@pytest.mark.parametrize("scheme_palette", ["light"], indirect=True)
def test_light_mode_keeps_the_mid_and_accent_roles(qtbot, scheme_palette) -> None:
    pane = PaneWidget(PaneRole.INPUT)
    qtbot.addWidget(pane)
    pane.resize(480, 520)
    pane.show()
    qtbot.waitExposed(pane)
    assert _near(_left_edge(pane), scheme_palette.color(QPalette.ColorRole.Mid))
    pane.set_keyboard_focus(True)
    assert _near(_left_edge(pane), scheme_palette.color(QPalette.ColorRole.Accent))


def _near(color: QColor, target: QColor) -> bool:
    # Within 8 per channel, so fractional scaling cannot break the match.
    return (
        abs(color.red() - target.red()) <= 8
        and abs(color.green() - target.green()) <= 8
        and abs(color.blue() - target.blue()) <= 8
    )
