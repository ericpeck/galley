"""HTML is core-only in #63 slice 4. No picker or Open offers it until slice 5."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from galley.app.pane import PaneRole, PaneSpec, PaneWidget
from galley.app.status_bar import INFO_RESERVE
from galley.core.load import detect_format


@pytest.mark.parametrize("role", [PaneRole.INPUT, PaneRole.OUTPUT])
def test_no_picker_offers_html(qtbot, role: PaneSpec) -> None:
    pane = PaneWidget(role)
    qtbot.addWidget(pane)
    combo = pane.format_combo
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert not any("html" in label.casefold() for label in labels)
    assert combo.findText("HTML", Qt.MatchFlag.MatchFixedString) == -1


def test_the_status_bar_and_open_do_not_name_html() -> None:
    assert not any("HTML" in reserve for reserve in INFO_RESERVE)
    for name in ("page.html", "page.htm"):
        assert detect_format(Path(name), "<!DOCTYPE html>\n<p>a") is None
