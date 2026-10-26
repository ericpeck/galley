"""XML is core-only in #63 slice 2. No picker or Open offers it until slice 3."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from galley.app.pane import PaneRole, PaneSpec, PaneWidget
from galley.app.status_bar import INFO_RESERVE
from galley.core.load import detect_format


@pytest.mark.parametrize("role", [PaneRole.INPUT, PaneRole.OUTPUT])
def test_no_picker_offers_xml(qtbot, role: PaneSpec) -> None:
    pane = PaneWidget(role)
    qtbot.addWidget(pane)
    combo = pane.format_combo
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert not any("xml" in label.casefold() for label in labels)
    assert combo.findText("XML", Qt.MatchFlag.MatchFixedString) == -1


def test_the_status_bar_and_open_do_not_name_xml() -> None:
    assert not any("XML" in reserve for reserve in INFO_RESERVE)
    assert detect_format(Path("feed.xml"), '<?xml version="1.0"?>\n<a/>') is None
