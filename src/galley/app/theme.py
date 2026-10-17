"""Broadsheet color roles from the Galley UI spec.

Hex values are the spec's named roles. ``text.muted`` is the line-number
color. ``accent`` is the button and focus color. macOS window-control
colors belong to platform chrome and are not roles here.
"""

from typing import Final

WINDOW_BG: Final = "#F3F2F2"
EDITOR_BG: Final = "#EAE7E7"
GUTTER_BG: Final = "#D7D3D3"
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
    "window.bg": WINDOW_BG,
    "editor.bg": EDITOR_BG,
    "gutter.bg": GUTTER_BG,
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
