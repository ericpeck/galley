"""Format picker and Pop out contrast in light and dark palettes."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

from galley.app.format_picker import closed_field_colors, pop_out_colors
from galley.app.main import create_main_window

# Light-mode reference values from the readability spec. The widgets do not
# bake these in; the assertions compare the resolved palette against them.
_LIGHT_TEXT = QColor("#201E1D")
_LIGHT_MUTED = QColor("#5E5B5B")
_MIN_CONTRAST = 4.5


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        if srgb <= 0.04045:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast(foreground: QColor, background: QColor) -> float:
    lighter = max(_luminance(foreground), _luminance(background))
    darker = min(_luminance(foreground), _luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def _pickers(window) -> list[QComboBox]:
    found = []
    for name in ("inputFormat", "outputFormat"):
        picker = window.findChild(QComboBox, name)
        assert isinstance(picker, QComboBox)
        found.append(picker)
    return found


def _pop_outs(window) -> list[QPushButton]:
    buttons = [
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "Pop out"
    ]
    assert len(buttons) == 2
    return buttons


def _install(widget, *, dark: bool) -> None:
    """Replace the widget palette. Highlighted text stays light on purpose."""
    palette = QPalette(widget.palette())
    if dark:
        field = QColor("#1C1C1C")
        ink = QColor("#F3F2F2")
        surface = QColor("#1C1C1C")
        highlight = QColor("#0E4F66")
        link = QColor("#D7EEF8")
    else:
        field = QColor("#FFFFFF")
        ink = QColor("#201E1D")
        surface = QColor("#F3F2F2")
        highlight = QColor("#99E0FF")
        link = QColor("#0000FF")
    roles = {
        QPalette.ColorRole.Base: field,
        QPalette.ColorRole.Button: surface,
        QPalette.ColorRole.Window: surface,
        QPalette.ColorRole.Text: ink,
        QPalette.ColorRole.ButtonText: ink,
        QPalette.ColorRole.WindowText: ink,
        QPalette.ColorRole.Highlight: highlight,
        QPalette.ColorRole.HighlightedText: QColor("#FFFFFF"),
        QPalette.ColorRole.Link: link,
        QPalette.ColorRole.PlaceholderText: ink,
    }
    for role, color in roles.items():
        palette.setColor(role, color)
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)
    widget.repaint()
    QApplication.processEvents()


def _sampled_contrast(widget, *, width_fraction: float) -> float:
    """Contrast of the strongest rendered pixel against the field fill."""
    image = widget.grab().toImage()
    assert image.width() > 8 and image.height() > 8
    limit_x = max(4, int(image.width() * width_fraction))
    counts: dict[str, int] = {}
    for y in range(2, image.height() - 2):
        for x in range(2, limit_x):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    background = QColor(max(counts, key=counts.get))
    strongest = 1.0
    for name in counts:
        strongest = max(strongest, _contrast(QColor(name), background))
    return strongest


def test_format_pickers_contrast_in_light_and_dark(qtbot) -> None:
    window = _show(qtbot)
    pickers = _pickers(window)
    window_ink = window.palette().color(QPalette.ColorRole.ButtonText)

    for picker in pickers:
        assert picker.styleSheet() == ""
        assert "color" not in picker.styleSheet()
        ink = picker.palette().color(QPalette.ColorRole.ButtonText)
        assert ink.name() == window_ink.name()
        assert _luminance(ink) <= _luminance(_LIGHT_TEXT) + 1e-9
        painted_ink, painted_field = closed_field_colors(picker)
        assert painted_ink.name() != painted_field.name()
        assert _contrast(painted_ink, painted_field) >= _MIN_CONTRAST
        assert _sampled_contrast(picker, width_fraction=0.55) >= _MIN_CONTRAST

    for dark in (False, True):
        for picker in pickers:
            _install(picker, dark=dark)
            painted_ink, painted_field = closed_field_colors(picker)
            assert painted_ink.name() != painted_field.name()
            assert _contrast(painted_ink, painted_field) >= _MIN_CONTRAST
            assert _sampled_contrast(picker, width_fraction=0.55) >= _MIN_CONTRAST
            resolved = picker.palette().color(QPalette.ColorRole.ButtonText)
            assert _contrast(resolved, painted_field) >= _MIN_CONTRAST
            if dark:
                assert _luminance(resolved) > _luminance(_LIGHT_MUTED)


def test_popup_list_contrast_in_light_and_dark(qtbot) -> None:
    window = _show(qtbot)
    picker = _pickers(window)[0]
    for dark in (False, True):
        _install(picker, dark=dark)
        picker.showPopup()
        view = picker.view()
        text = view.palette().color(QPalette.ColorRole.Text)
        field = view.palette().color(QPalette.ColorRole.Base)
        highlight = view.palette().color(QPalette.ColorRole.Highlight)
        highlighted = view.palette().color(QPalette.ColorRole.HighlightedText)
        assert text.name() != field.name()
        assert _contrast(text, field) >= _MIN_CONTRAST
        assert _contrast(highlighted, highlight) >= _MIN_CONTRAST
        picker.hidePopup()


def test_pop_out_contrast_in_light_and_dark(qtbot) -> None:
    window = _show(qtbot)
    buttons = _pop_outs(window)
    for button in buttons:
        assert button.styleSheet() == ""
        ink, surface = pop_out_colors(button)
        assert ink.name() != surface.name()
        assert _contrast(ink, surface) >= _MIN_CONTRAST
        assert _luminance(ink) <= _luminance(_LIGHT_MUTED) + 1e-9
        assert _sampled_contrast(button, width_fraction=1.0) >= _MIN_CONTRAST

    for dark in (False, True):
        for button in buttons:
            _install(button, dark=dark)
            ink, surface = pop_out_colors(button)
            assert ink.name() != surface.name()
            assert _contrast(ink, surface) >= _MIN_CONTRAST
            assert _sampled_contrast(button, width_fraction=1.0) >= _MIN_CONTRAST
            if not dark:
                assert _luminance(ink) <= _luminance(_LIGHT_MUTED) + 1e-9
            else:
                assert _luminance(ink) > _luminance(_LIGHT_MUTED)


def test_format_picker_selection_still_switches_json_and_yaml(qtbot) -> None:
    window = _show(qtbot)
    output = window.findChild(QComboBox, "outputFormat")
    direction = window.findChild(QLabel, "directionLabel")
    assert isinstance(output, QComboBox)
    assert direction is not None
    output.setCurrentText("YAML")
    assert output.currentText() == "YAML"
    assert direction.text() == "JSON → YAML"
    output.setCurrentText("JSON")
    assert output.currentText() == "JSON"
    assert direction.text() == "JSON → JSON"
