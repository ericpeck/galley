"""Broadsheet color roles from the Galley UI spec.

Widgets paint ``QPalette`` roles. These samples seed the light palette so
light mode matches the spec. The only sample a widget may name is
``ACCENT`` (``#0080A6``), and only for the Format button fill and the
find highlight. Syntax colors stay hex. macOS window-control colors
belong to platform chrome and are not roles here.
"""

from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

WINDOW_BG: Final = "#F3F2F2"
PANE_EDGE: Final = "#7D7979"
LINE_CURRENT: Final = "#DEDBDB"
DIALOG_BG: Final = "#EAE9E9"
CARD_BG: Final = "#F3F2F2"
SCRIM: Final = "#B5B2B2"
TEXT: Final = "#201E1D"
TEXT_CODE_VALUE: Final = "#2D2B2B"
TEXT_MUTED: Final = "#5E5B5B"
ACCENT: Final = "#0080A6"
ACCENT_STRONG: Final = "#006786"
ACCENT_INK: Final = "#004961"
ACCENT_TINT: Final = "#E9F8FF"
# 45% of accent over window.bg, nearest integer per channel.
# round(0.45 * #0088B0 + 0.55 * #F3F2F2) reproduced measured #86C2D4.
# The same blend of #0080A6 over #F3F2F2 is #86BFD0.
ACCENT_DISABLED: Final = "#86BFD0"
SELECTION: Final = "#99E0FF"
INFO_TINT: Final = "#C2D6DC"
STRING: Final = "#AA0B56"
ERROR: Final = "#AA0B56"
ERROR_TINT: Final = "#FFF1F4"
ERROR_ICON_RING: Final = "#EEC3D4"
AMBER_TINT: Final = "#F1E3BE"
AMBER_INK: Final = "#6E4604"
GUIDE_DEPTH_BLUE: Final = "#62C5EE"
GUIDE_DEPTH_PINK: Final = "#FF90B1"

# White label on ``accent``. The spec states this contrast pair and does
# not list it as its own role.
TEXT_ON_ACCENT: Final = "#FFFFFF"

# Role names match the spec table. Guide depth lists the two sampled hues.
COLORS: Final[dict[str, str]] = {
    # Light-mode samples. The editor paints palette Base, the gutter
    # AlternateBase; these hexes are not applied to those surfaces.
    "window.bg": WINDOW_BG,
    "editor.bg": "#FFFFFF",
    "gutter.bg": "#EAE7E7",
    "pane.edge": PANE_EDGE,
    "line.current": LINE_CURRENT,
    "dialog.bg": DIALOG_BG,
    "card.bg": CARD_BG,
    "scrim": SCRIM,
    "text": TEXT,
    "text.code.value": TEXT_CODE_VALUE,
    "text.muted": TEXT_MUTED,
    "accent": ACCENT,
    "accent.strong": ACCENT_STRONG,
    "accent.ink": ACCENT_INK,
    "accent.tint": ACCENT_TINT,
    "accent.disabled": ACCENT_DISABLED,
    "selection": SELECTION,
    "info.tint": INFO_TINT,
    "string": STRING,
    "error": ERROR,
    "error.tint": ERROR_TINT,
    "error.icon.ring": ERROR_ICON_RING,
    "amber.tint": AMBER_TINT,
    "amber.ink": AMBER_INK,
    "guide.depth.blue": GUIDE_DEPTH_BLUE,
    "guide.depth.pink": GUIDE_DEPTH_PINK,
}


def selected_problem_palette(palette: QPalette) -> QPalette:
    """Problem selection fill on the highlight role.

    Light mode uses the accent tint sample. A dark palette gets a fill mixed
    from its own window and accent roles, so its light text still reads.
    """
    tinted = QPalette(palette)
    window = palette.color(QPalette.ColorRole.Window)
    if window.lightness() < 128:
        accent = palette.color(QPalette.ColorRole.Accent)
        fill = QColor(
            round(window.red() * 0.75 + accent.red() * 0.25),
            round(window.green() * 0.75 + accent.green() * 0.25),
            round(window.blue() * 0.75 + accent.blue() * 0.25),
        )
    else:
        fill = QColor(ACCENT_TINT)
    tinted.setColor(QPalette.ColorRole.Highlight, fill)
    return tinted


def find_highlight_color() -> QColor:
    """Brand color for a find match. Same hex as the Format button fill."""
    return QColor(ACCENT)


def install_light_palette(app: QApplication) -> None:
    """Seed palette roles from the light samples when the scheme is not dark.

    A dark scheme, or a palette whose base is already dark, is left alone.
    """
    hints = QGuiApplication.styleHints()
    if hints.colorScheme() == Qt.ColorScheme.Dark:
        return
    base = app.palette().color(QPalette.ColorRole.Base)
    if base.lightness() < 128:
        return
    app.setPalette(_light_palette(app.palette()))


def _light_palette(current: QPalette) -> QPalette:
    """Light-mode roles. Disabled text stays the platform fade."""
    palette = QPalette(current)
    active = (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
    )
    roles = {
        QPalette.ColorRole.Window: WINDOW_BG,
        QPalette.ColorRole.Button: WINDOW_BG,
        QPalette.ColorRole.Base: COLORS["editor.bg"],
        QPalette.ColorRole.AlternateBase: COLORS["gutter.bg"],
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.PlaceholderText: TEXT_MUTED,
        QPalette.ColorRole.Highlight: SELECTION,
        QPalette.ColorRole.Link: ACCENT_STRONG,
        QPalette.ColorRole.Mid: PANE_EDGE,
        QPalette.ColorRole.Midlight: LINE_CURRENT,
        QPalette.ColorRole.ToolTipBase: WINDOW_BG,
        QPalette.ColorRole.ToolTipText: TEXT,
    }
    for role, sample in roles.items():
        color = QColor(sample)
        for group in active:
            palette.setColor(group, role, color)
    # Brand blue and the white label stay put on a disabled Format button.
    # ``Light`` is the disabled Format fill (the old accent.disabled blend).
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QPalette.ColorRole.Accent, QColor(ACCENT))
        palette.setColor(
            group, QPalette.ColorRole.HighlightedText, QColor(TEXT_ON_ACCENT)
        )
        palette.setColor(group, QPalette.ColorRole.Light, QColor(ACCENT_DISABLED))
    return palette
