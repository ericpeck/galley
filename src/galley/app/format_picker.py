"""Format pickers and the Pop out label.

The closed picker keeps the platform popup-button chrome. Its value is
painted from ``QPalette.ButtonText`` or ``QPalette.Text`` on the field
background (``QPalette.Base``). Nothing here sets a stylesheet color.
Blue is limited to the arrow chip. Pop out is painted from
``QPalette.Link`` or ``QPalette.PlaceholderText``.
"""

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QWidget,
)

from galley.app.theme import ACCENT

_MIN_CONTRAST = 4.5


def contrast_ratio(foreground: QColor, background: QColor) -> float:
    """WCAG relative-luminance contrast of two opaque colors."""
    lighter = max(_luminance(foreground), _luminance(background))
    darker = min(_luminance(foreground), _luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def closed_field_colors(picker: QComboBox) -> tuple[QColor, QColor]:
    """Ink and field colors the closed picker actually paints."""
    palette = picker.palette()
    group = _group(picker)
    field = palette.color(group, QPalette.ColorRole.Base)
    button_text = palette.color(group, QPalette.ColorRole.ButtonText)
    text = palette.color(group, QPalette.ColorRole.Text)
    if contrast_ratio(text, field) > contrast_ratio(button_text, field):
        return text, field
    return button_text, field


def pop_out_colors(button: QPushButton) -> tuple[QColor, QColor]:
    """Ink and surface colors the Pop out label actually paints."""
    palette = button.palette()
    group = _group(button)
    surface = palette.color(group, QPalette.ColorRole.Window)
    link = palette.color(group, QPalette.ColorRole.Link)
    placeholder = palette.color(group, QPalette.ColorRole.PlaceholderText)
    if contrast_ratio(link, surface) >= _MIN_CONTRAST:
        return link, surface
    if contrast_ratio(placeholder, surface) >= _MIN_CONTRAST:
        return placeholder, surface
    if contrast_ratio(link, surface) >= contrast_ratio(placeholder, surface):
        return link, surface
    return placeholder, surface


class FormatComboBox(QComboBox):
    """JSON/YAML picker. The closed value uses palette text, not a stylesheet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._followed_window_text = False

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._followed_window_text:
            return
        self._followed_window_text = True
        self._use_window_text_roles()

    def showPopup(self) -> None:
        self._sync_popup_palette()
        super().showPopup()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._sync_popup_palette()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.state = option.state & ~QStyle.StateFlag.State_Selected
        self.style().drawComplexControl(
            QStyle.ComplexControl.CC_ComboBox, option, painter, self
        )
        edit = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        arrow = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )
        ink, field = closed_field_colors(self)
        painter.fillRect(edit, field)
        painter.setPen(ink)
        painter.setFont(self.font())
        painter.drawText(
            edit.adjusted(2, 0, -2, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            option.currentText,
        )
        _paint_arrow_chip(painter, arrow, self.palette())
        painter.end()

    def _use_window_text_roles(self) -> None:
        """Copy the window's text roles so light mode follows the app palette.

        An explicit ``setPalette`` after the first show replaces these
        colors, which is how a dark palette takes over. Disabled colors
        stay on the platform's disabled group.
        """
        window = self.window()
        if window is None or window is self:
            return
        palette = QPalette(self.palette())
        source = window.palette()
        groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)
        roles = (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.WindowText,
        )
        for group in groups:
            for role in roles:
                palette.setColor(group, role, source.color(group, role))
        self.setPalette(palette)

    def _sync_popup_palette(self) -> None:
        """Give the list Text on Base, and a selection pair that contrasts."""
        view = self.view()
        palette = QPalette(self.palette())
        group = _group(self)
        highlight = palette.color(group, QPalette.ColorRole.Highlight)
        highlighted = palette.color(group, QPalette.ColorRole.HighlightedText)
        text = palette.color(group, QPalette.ColorRole.Text)
        field = palette.color(group, QPalette.ColorRole.Base)
        if contrast_ratio(highlighted, highlight) < _MIN_CONTRAST:
            if contrast_ratio(text, highlight) >= contrast_ratio(field, highlight):
                highlighted = text
            else:
                highlighted = field
            for item_group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
            ):
                palette.setColor(
                    item_group, QPalette.ColorRole.HighlightedText, highlighted
                )
        view.setPalette(palette)


class PopOutButton(QPushButton):
    """Flat Pop out label painted with the link or placeholder role."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Pop out", parent)
        self.setFlat(True)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setFont(self.font())
        ink, _surface = pop_out_colors(self)
        painter.setPen(ink)
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignCenter),
            self.text(),
        )
        painter.end()


def _paint_arrow_chip(painter: QPainter, rect: QRect, palette: QPalette) -> None:
    chip = QColor(ACCENT)
    painter.fillRect(rect, chip)
    ink = _best_ink(
        chip,
        (
            palette.color(QPalette.ColorRole.HighlightedText),
            palette.color(QPalette.ColorRole.Base),
            palette.color(QPalette.ColorRole.Button),
            palette.color(QPalette.ColorRole.Text),
            palette.color(QPalette.ColorRole.ButtonText),
        ),
    )
    painter.setPen(QPen(ink, 1.6))
    center_x = rect.center().x()
    center_y = rect.center().y()
    painter.drawLine(center_x - 3, center_y - 1, center_x, center_y + 2)
    painter.drawLine(center_x, center_y + 2, center_x + 3, center_y - 1)


def _best_ink(background: QColor, candidates: tuple[QColor, ...]) -> QColor:
    best = candidates[0]
    best_ratio = -1.0
    for color in candidates:
        ratio = contrast_ratio(color, background)
        if ratio > best_ratio:
            best = color
            best_ratio = ratio
    return best


def _group(widget: QWidget) -> QPalette.ColorGroup:
    if not widget.isEnabled():
        return QPalette.ColorGroup.Disabled
    return widget.palette().currentColorGroup()


def _luminance(color: QColor) -> float:
    return (
        0.2126 * _channel(color.red())
        + 0.7152 * _channel(color.green())
        + 0.0722 * _channel(color.blue())
    )


def _channel(value: int) -> float:
    srgb = value / 255
    if srgb <= 0.04045:
        return srgb / 12.92
    return ((srgb + 0.055) / 1.055) ** 2.4
