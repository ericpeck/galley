"""Small monochrome toolbar icons.

Glyphs use the palette's placeholder text. The Format glyph uses the
brand fill it sits on, in both modes.
A few transparent pixels sit to the right of label icons so the
platform's icon gap lands near 8 px. No stylesheet sets the color.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from galley.app.theme import ACCENT

_ICON = 16
_GAP = 4


def toolbar_icon(name: str, *, pad: bool = False) -> QIcon:
    """Return a toolbar icon for ``name``, or an empty icon when unknown."""
    pixmap = _draw(name)
    if pixmap is None:
        return QIcon()
    if not pad:
        return QIcon(pixmap)
    wider = QPixmap(pixmap.width() + _GAP, pixmap.height())
    wider.fill(Qt.GlobalColor.transparent)
    painter = QPainter(wider)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return QIcon(wider)


def _ink(name: str) -> QColor:
    if name == "Format":
        # Dark mode moves the Accent role off the brand blue, but the
        # Format button fill stays ACCENT, so the glyph stays with it.
        return QColor(ACCENT)
    app = QApplication.instance()
    palette = app.palette() if isinstance(app, QApplication) else QPalette()
    return palette.color(QPalette.ColorRole.PlaceholderText)


def _draw(name: str) -> QPixmap | None:
    color = _ink(name)
    pixmap = QPixmap(_ICON, _ICON)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    drawn = _stroke(painter, name)
    painter.end()
    if not drawn:
        return None
    return pixmap


def _stroke(painter: QPainter, name: str) -> bool:
    if name == "Paste":
        painter.drawRoundedRect(4, 3, 8, 10, 1, 1)
        painter.drawLine(6, 3, 6, 5)
        painter.drawLine(10, 3, 10, 5)
        painter.drawLine(6, 5, 10, 5)
        return True
    if name == "Copy":
        painter.drawRect(6, 4, 6, 8)
        painter.drawRect(4, 3, 6, 8)
        return True
    if name == "Undo":
        painter.drawArc(4, 4, 8, 8, 30 * 16, 200 * 16)
        painter.drawLine(4, 8, 4, 5)
        painter.drawLine(4, 8, 7, 8)
        return True
    if name == "Redo":
        painter.drawArc(4, 4, 8, 8, 150 * 16, -200 * 16)
        painter.drawLine(12, 8, 12, 5)
        painter.drawLine(12, 8, 9, 8)
        return True
    if name == "Outdent":
        painter.drawLine(11, 4, 5, 8)
        painter.drawLine(5, 8, 11, 12)
        painter.drawLine(5, 8, 12, 8)
        return True
    if name == "Indent":
        painter.drawLine(5, 4, 11, 8)
        painter.drawLine(11, 8, 5, 12)
        painter.drawLine(4, 8, 11, 8)
        return True
    if name == "Sort keys":
        painter.drawLine(4, 4, 12, 4)
        painter.drawLine(4, 8, 10, 8)
        painter.drawLine(4, 12, 8, 12)
        return True
    if name == "Minify":
        painter.drawLine(3, 6, 13, 6)
        painter.drawLine(5, 10, 11, 10)
        return True
    if name == "Expand all":
        painter.drawLine(4, 6, 8, 10)
        painter.drawLine(8, 10, 12, 6)
        return True
    if name == "Collapse all":
        painter.drawLine(4, 10, 8, 6)
        painter.drawLine(8, 6, 12, 10)
        return True
    if name == "Validate":
        painter.drawLine(3, 8, 6, 12)
        painter.drawLine(6, 12, 13, 4)
        return True
    if name == "Find…":
        painter.drawEllipse(3, 3, 7, 7)
        painter.drawLine(9, 9, 13, 13)
        return True
    if name == "Convert from Python":
        painter.drawLine(6, 4, 3, 8)
        painter.drawLine(3, 8, 6, 12)
        painter.drawLine(10, 4, 13, 8)
        painter.drawLine(13, 8, 10, 12)
        return True
    if name == "Format":
        painter.drawArc(3, 3, 5, 10, 90 * 16, 180 * 16)
        painter.drawArc(8, 3, 5, 10, 270 * 16, 180 * 16)
        return True
    return False
