"""Pane edge, keyboard focus ring, and what each pane is called."""

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from galley.app.editor import CodeEditor
from galley.app.format_picker import FormatComboBox
from galley.app.pane import PaneKind, PaneRole, PaneSpec, PaneWidget
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


def _format_caption(pane: PaneWidget) -> QLabel:
    captions = [
        label
        for label in pane.findChildren(QLabel)
        if label.buddy() is pane.format_combo
    ]
    assert len(captions) == 1
    return captions[0]


@pytest.mark.parametrize(
    ("spec", "copy_name"),
    [
        (PaneSpec("original", "Original 1", "Compare as", PaneKind.SOURCE), None),
        (
            PaneSpec("changed", "Changed 1", "Compare as", PaneKind.RESULT),
            "changedCopyButton",
        ),
    ],
)
def test_a_pane_shows_the_title_and_label_it_is_given(
    qtbot, spec: PaneSpec, copy_name: str | None
) -> None:
    pane = PaneWidget(spec)
    qtbot.addWidget(pane)
    assert pane.objectName() == f"{spec.name}Pane"
    assert pane.title == spec.title

    tab = pane.findChild(QLabel, f"{spec.name}TabTitle")
    assert tab is not None and tab.text() == spec.title
    assert _format_caption(pane).text() == "Compare as"

    combo = pane.findChild(FormatComboBox, f"{spec.name}Format")
    assert combo is pane.format_combo
    assert combo.accessibleName() == "Compare as"

    editor = pane.findChild(CodeEditor, f"{spec.name}Editor")
    assert editor is pane.editor
    assert editor.accessibleName() == f"{spec.title} editor"

    if copy_name is None:
        assert pane.paste_button is not None and pane.clear_button is not None
        assert pane.copy_button is None
    else:
        assert pane.findChild(QPushButton, copy_name) is pane.copy_button
        assert pane.paste_button is None and pane.clear_button is None


def test_the_input_and_output_roles_keep_their_names_and_controls(qtbot) -> None:
    source = PaneWidget(PaneRole.INPUT)
    result = PaneWidget(PaneRole.OUTPUT)
    qtbot.addWidget(source)
    qtbot.addWidget(result)

    assert source.objectName() == "inputPane"
    assert source.editor.objectName() == "inputEditor"
    assert source.format_combo.objectName() == "inputFormat"
    assert source.title == "Input"
    assert source.editor.accessibleName() == "Input editor"
    assert source._tab_title.text() == "Input"
    assert source._tab_title.accessibleName() == "Input"
    assert _format_caption(source).text() == "Input as"
    assert source.count_label.text() == "Empty"
    assert source.findChild(QWidget, "emptyInput") is source.empty_overlay
    for name in ("inputEmptyTitle", "inputEmptyHint"):
        assert source.findChild(QLabel, name) is not None
    for name in ("emptyPasteButton", "emptyOpenButton", "footerPasteButton"):
        assert source.findChild(QPushButton, name) is not None
    assert source.findChild(QPushButton, "clearButton") is source.clear_button

    assert result.objectName() == "outputPane"
    assert result.editor.objectName() == "outputEditor"
    assert result.format_combo.objectName() == "outputFormat"
    assert result.title == "Output"
    assert result.editor.accessibleName() == "Output editor"
    assert result._tab_title.text() == "Output"
    assert result._tab_title.accessibleName() == "Output"
    assert _format_caption(result).text() == "Output as"
    assert result.count_label.text() == ""
    assert result.findChild(QWidget, "emptyOutput") is result.empty_overlay
    assert result.findChild(QLabel, "outputEmptyTitle") is not None
    assert result.findChild(QPushButton, "outputOptionsButton") is not None
    assert result.findChild(QPushButton, "outputCopyButton") is result.copy_button
