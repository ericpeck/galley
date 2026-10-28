"""Format picker contrast in light and dark palettes."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from galley.app.format_picker import closed_field_colors
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


def _force_scheme(scheme: Qt.ColorScheme) -> bool:
    """Ask Qt for ``scheme``. Return whether the hint actually changed.

    Offscreen ignores the OS dark-mode setting, and this PySide build
    keeps ``colorScheme()`` at ``Unknown`` after ``setColorScheme``.
    Callers then apply an explicit palette so the dark pass is not a
    second look at the light colors.
    """
    hints = QGuiApplication.styleHints()
    setter = getattr(hints, "setColorScheme", None)
    if setter is None:
        return False
    setter(scheme)
    QApplication.processEvents()
    return hints.colorScheme() == scheme


def _apply_mode(widgets, *, dark: bool) -> None:
    scheme = Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light
    if _force_scheme(scheme):
        for widget in widgets:
            widget.repaint()
        QApplication.processEvents()
        return
    for widget in widgets:
        _install(widget, dark=dark)


def _restore_scheme(previous: Qt.ColorScheme) -> None:
    hints = QGuiApplication.styleHints()
    hints.setColorScheme(previous)
    QApplication.processEvents()
    assert hints.colorScheme() == previous


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
    hints = QGuiApplication.styleHints()
    previous = hints.colorScheme()
    window_ink = window.palette().color(QPalette.ColorRole.ButtonText)

    for picker in pickers:
        assert picker.styleSheet() == ""
        assert "color" not in picker.styleSheet()
        ink = picker.palette().color(QPalette.ColorRole.ButtonText)
        assert ink.name() == window_ink.name()
        assert _luminance(ink) <= _luminance(_LIGHT_TEXT) + 1e-9

    try:
        _apply_mode(pickers, dark=False)
        light_pairs = {}
        for picker in pickers:
            painted_ink, painted_field = closed_field_colors(picker)
            light_pairs[picker.objectName()] = (
                painted_ink.name(),
                painted_field.name(),
            )
            assert painted_ink.name() != painted_field.name()
            assert _contrast(painted_ink, painted_field) >= _MIN_CONTRAST
            assert _sampled_contrast(picker, width_fraction=0.55) >= _MIN_CONTRAST

        _apply_mode(pickers, dark=True)
        for picker in pickers:
            painted_ink, painted_field = closed_field_colors(picker)
            dark_pair = (painted_ink.name(), painted_field.name())
            assert dark_pair != light_pairs[picker.objectName()]
            assert painted_ink.name() != painted_field.name()
            assert _contrast(painted_ink, painted_field) >= _MIN_CONTRAST
            assert _sampled_contrast(picker, width_fraction=0.55) >= _MIN_CONTRAST
            resolved = picker.palette().color(QPalette.ColorRole.ButtonText)
            assert resolved.name() != light_pairs[picker.objectName()][0]
            assert _luminance(resolved) > _luminance(_LIGHT_MUTED)
    finally:
        _restore_scheme(previous)


def _popup_colors(picker: QComboBox) -> tuple[str, str, str, str]:
    picker.showPopup()
    view = picker.view()
    text = view.palette().color(QPalette.ColorRole.Text).name()
    field = view.palette().color(QPalette.ColorRole.Base).name()
    highlight = view.palette().color(QPalette.ColorRole.Highlight).name()
    highlighted = view.palette().color(QPalette.ColorRole.HighlightedText).name()
    picker.hidePopup()
    return text, field, highlight, highlighted


def test_popup_list_contrast_in_light_and_dark(qtbot) -> None:
    window = _show(qtbot)
    picker = _pickers(window)[0]
    hints = QGuiApplication.styleHints()
    previous = hints.colorScheme()
    try:
        _apply_mode([picker], dark=False)
        light = _popup_colors(picker)
        text, field, highlight, highlighted = (QColor(name) for name in light)
        assert text.name() != field.name()
        assert _contrast(text, field) >= _MIN_CONTRAST
        assert _contrast(highlighted, highlight) >= _MIN_CONTRAST

        _apply_mode([picker], dark=True)
        dark = _popup_colors(picker)
        assert dark != light
        text, field, highlight, highlighted = (QColor(name) for name in dark)
        assert text.name() != field.name()
        assert _contrast(text, field) >= _MIN_CONTRAST
        assert _contrast(highlighted, highlight) >= _MIN_CONTRAST
    finally:
        _restore_scheme(previous)


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
