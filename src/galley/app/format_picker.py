"""Format pickers and the Pop out label.

The closed picker keeps the platform popup-button chrome. Its value is
painted from ``QPalette.ButtonText`` or ``QPalette.Text`` on the field
background (``QPalette.Base``). Nothing here sets a stylesheet color.
Blue is limited to the arrow chip. Pop out is painted from
``QPalette.Link`` or ``QPalette.PlaceholderText``. A disabled (Coming
soon) Pop out uses the muted placeholder role, so it no longer reads as a
link.
"""

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QWidget,
)

from galley.app.actions import COMING_SOON
from galley.app.theme import contrast_ratio

_MIN_CONTRAST = 4.5
POP_OUT_TEXT = "Pop out"
POP_OUT_NAME = "Pop out into window"
_POP_OUT_GLYPH = 12
_POP_OUT_PADDING = 6


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
    """Ink and surface colors the Pop out label actually paints.

    A disabled Pop out is Coming soon. It keeps readable muted ink from
    the window's own group instead of the faint disabled link.
    """
    palette = button.palette()
    if not button.isEnabled():
        group = palette.currentColorGroup()
        if group == QPalette.ColorGroup.Disabled:
            group = QPalette.ColorGroup.Active
        return (
            palette.color(group, QPalette.ColorRole.PlaceholderText),
            palette.color(group, QPalette.ColorRole.Window),
        )
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
        self._copied_window_text: tuple[int, ...] = ()

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
            if self._followed_window_text:
                # The copied text roles are explicit, so a scheme change
                # skips them. Copy again once the window has settled.
                QTimer.singleShot(0, self, self._follow_window_text)

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

    def _follow_window_text(self) -> None:
        """Copy again only when the window's text roles changed.

        A palette set on the picker itself is left alone.
        """
        window = self.window()
        if window is None or window is self:
            return
        if _text_roles(window.palette()) != self._copied_window_text:
            self._use_window_text_roles()

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
        self._copied_window_text = _text_roles(source)
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
    """Flat Pop out label painted with the link or placeholder role.

    A narrow tab strip makes it icon-only. ``text()`` and the accessible
    name do not change. The tooltip names the command, with "coming soon"
    while it is unbuilt.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(POP_OUT_TEXT, parent)
        self.setFlat(True)
        self.setAccessibleName(POP_OUT_NAME)
        self.setToolTip(POP_OUT_NAME)
        self._icon_only = False

    def is_icon_only(self) -> bool:
        return self._icon_only

    def set_icon_only(self, icon_only: bool) -> None:
        """Show only the pop-out glyph, or the glyph-free text label."""
        if icon_only == self._icon_only:
            return
        self._icon_only = icon_only
        self.sync_tool_tip()
        self.updateGeometry()
        self.update()

    def sync_tool_tip(self) -> None:
        """Match the tooltip to the label shown and the Coming soon mark."""
        name = self.accessibleName()
        if self.accessibleDescription() != COMING_SOON:
            self.setToolTip(name)
        elif self._icon_only:
            self.setToolTip(f"{name} — coming soon")
        else:
            self.setToolTip(COMING_SOON)

    def text_width(self) -> int:
        """Width with the text label, whichever mode is showing."""
        return QPushButton.sizeHint(self).width()

    def icon_width(self) -> int:
        return _POP_OUT_GLYPH + 2 * _POP_OUT_PADDING

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self._icon_only:
            hint.setWidth(self.icon_width())
        return hint

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        if self._icon_only:
            hint.setWidth(self.icon_width())
        return hint

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        ink, _surface = pop_out_colors(self)
        if self._icon_only:
            _paint_pop_out_glyph(painter, self.rect(), ink)
            painter.end()
            return
        painter.setFont(self.font())
        painter.setPen(ink)
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignCenter),
            self.text(),
        )
        painter.end()


def _paint_pop_out_glyph(painter: QPainter, rect: QRect, ink: QColor) -> None:
    """A window with an arrow leaving its top-right corner."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(ink, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    size = _POP_OUT_GLYPH
    left = rect.center().x() - size // 2
    top = rect.center().y() - size // 2
    painter.translate(left + 0.5, top + 0.5)
    painter.drawLine(5, 1, 1, 1)
    painter.drawLine(1, 1, 1, 11)
    painter.drawLine(1, 11, 11, 11)
    painter.drawLine(11, 11, 11, 7)
    painter.drawLine(6, 6, 11, 1)
    painter.drawLine(8, 1, 11, 1)
    painter.drawLine(11, 1, 11, 4)


def _paint_arrow_chip(painter: QPainter, rect: QRect, palette: QPalette) -> None:
    chip = palette.color(QPalette.ColorRole.Accent)
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


def _text_roles(palette: QPalette) -> tuple[int, ...]:
    return tuple(
        palette.color(group, role).rgba()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.WindowText,
        )
    )


def _group(widget: QWidget) -> QPalette.ColorGroup:
    if not widget.isEnabled():
        return QPalette.ColorGroup.Disabled
    return widget.palette().currentColorGroup()
