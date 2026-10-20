"""Broadsheet color roles from the Galley UI spec.

Widgets paint ``QPalette`` roles. These samples seed the light palette so
light mode matches the spec. Dark mode starts from the platform's own dark
palette and only fixes the roles below it that miss the contrast rules.
The only sample a widget may name is ``ACCENT`` (``#0080A6``), and only
for the Format button fill and the find highlight. Syntax colors stay hex.
macOS window-control colors belong to platform chrome and are not roles
here.
"""

from collections.abc import Sequence
from typing import Final

from PySide6.QtGui import QColor, QPalette

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
# Dark-mode values from issue #54. ``#0080A6`` text on a dark surface is
# below 4.5:1, so links and accent marks use ACCENT_TEXT_DARK there. The
# Format fill and the find highlight stay ACCENT. Errors pair
# ERROR_DARK with an icon and the word "Error".
ACCENT_TEXT_DARK: Final = "#4DB8DB"
ERROR_DARK: Final = "#FF7B72"
AMBER_TINT: Final = "#F1E3BE"
AMBER_INK: Final = "#6E4604"
GUIDE_DEPTH_BLUE: Final = "#62C5EE"
GUIDE_DEPTH_PINK: Final = "#FF90B1"

# White label on ``accent``. The spec states this contrast pair and does
# not list it as its own role.
TEXT_ON_ACCENT: Final = "#FFFFFF"

# Syntax colors on a dark editor Base, named after the light role each
# replaces. Keys, strings, and numbers/literals are the approved dark-mode
# values from issue #54. #54 lists no punctuation color, so
# TEXT_MUTED_DARK is a muted gray that clears 4.5:1 on the macOS dark Base
# #171717 and its current-line band #343434. COLORS mirrors the light
# spec table, so these are not in it.
ACCENT_INK_DARK: Final = "#7CC7E8"
STRING_DARK: Final = "#E6C07B"
TEXT_CODE_VALUE_DARK: Final = "#C3A6FF"
TEXT_MUTED_DARK: Final = "#A8A4A4"
# Dark gutter line numbers, Bill's #54 update (replaces #6E6A67, which was
# 3.35:1). 5.38:1 on #171717 and 4.66:1 on the dark gutter #242424, and
# dimmer than the dark punctuation TEXT_MUTED_DARK.
LINE_NUMBER_DARK: Final = "#908C89"

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


EDGE_CONTRAST: Final = 3.0


def contrast_ratio(foreground: QColor, background: QColor) -> float:
    """WCAG relative-luminance contrast of two opaque colors."""
    lighter = max(_luminance(foreground), _luminance(background))
    darker = min(_luminance(foreground), _luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def composite(color: QColor, background: QColor) -> QColor:
    """``color`` blended over an opaque ``background``, as painted."""
    return mix(background, QColor(color.rgb()), color.alphaF())


def mix(first: QColor, second: QColor, amount: float) -> QColor:
    """``amount`` of ``second`` blended into ``first``. Both are opaque."""
    return QColor(
        round(first.red() * (1 - amount) + second.red() * amount),
        round(first.green() * (1 - amount) + second.green() * amount),
        round(first.blue() * (1 - amount) + second.blue() * amount),
    )


def visible_edge(edge: QColor, backgrounds: Sequence[QColor], toward: QColor) -> QColor:
    """``edge``, moved toward ``toward`` until it is 3:1 on every background.

    A color that already reaches 3:1, like every light-mode edge, is
    returned unchanged.
    """
    for step in range(11):
        color = mix(edge, toward, step / 10)
        if all(contrast_ratio(color, b) >= EDGE_CONTRAST for b in backgrounds):
            return color
    return toward


def pane_edge_colors(palette: QPalette) -> tuple[QColor, QColor]:
    """The pane's resting edge and its focus ring, each 3:1 or better.

    Both sit between the window and the editor's base, so each must stand
    out from both. ``Mid`` and ``Accent`` are kept when they already do.
    """
    window = palette.color(QPalette.ColorRole.Window)
    base = composite(palette.color(QPalette.ColorRole.Base), window)
    backgrounds = (window, base)
    toward = composite(palette.color(QPalette.ColorRole.WindowText), window)
    edge = visible_edge(
        composite(palette.color(QPalette.ColorRole.Mid), window), backgrounds, toward
    )
    ring = visible_edge(
        composite(palette.color(QPalette.ColorRole.Accent), window),
        backgrounds,
        toward,
    )
    return edge, ring


def selected_problem_palette(palette: QPalette) -> QPalette:
    """Problem selection fill on the highlight role.

    Light mode uses the accent tint sample. Dark mode keeps the native
    Highlight; the row paints its text in an opaque color that reaches
    4.5:1 on it.
    """
    tinted = QPalette(palette)
    if not is_dark(palette):
        tinted.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_TINT))
    return tinted


def find_highlight_color() -> QColor:
    """Brand color for a find match. Same hex as the Format button fill."""
    return QColor(ACCENT)


def light_palette(current: QPalette) -> QPalette:
    """Light-mode roles on ``current``, with a legible disabled fade."""
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
    _set_disabled_text(palette)
    return palette


# Text minimum from AGENTS.md. EDGE_CONTRAST above is the 3:1 edge rule.
TEXT_MIN_CONTRAST: Final = 4.5
# Disabled labels are exempt from 4.5:1 but must stay legible (3:1, per
# Bill) on the button face and clearly dimmer than enabled text.
DISABLED_TEXT_CONTRAST: Final = 3.0
# Native button faces sit a little toward the text from the window: the
# cocoa dark tool button face is #3E3E3E on the #323232 window. A share of
# 0.08 covers that with some margin in both schemes.
_BUTTON_FACE_SHARE: Final = 0.08
# Share of ERROR_DARK mixed into the dark editor Base for a problem line.
_ERROR_TINT_DARK_SHARE: Final = 0.2
# Share of ACCENT over the window for the disabled Format fill, as in
# ACCENT_DISABLED.
_ACCENT_DISABLED_SHARE: Final = 0.45


def dark_palette(platform: QPalette) -> QPalette:
    """Dark-mode roles on top of the platform's dark palette.

    The platform keeps window, base, text, and selection. These roles are
    replaced because the macOS dark palette misses them: ``AlternateBase``
    is a light gray (the gutter and Undo/Redo tray), ``PlaceholderText`` and
    disabled text are 25% white, ``Mid`` is too close to the window for a
    3:1 edge, and Inactive ``ButtonText`` is black. The replacements are
    mixed from the palette's own window and text until they pass, so no new
    UI hex is introduced.

    A platform without a dark palette (offscreen, or a style that ignores
    the color scheme) gets the light roles with their lightness turned
    over, then the same fixes.
    """
    if is_dark(platform):
        palette = QPalette(platform)
    else:
        palette = _turned_over(light_palette(platform))
    window = palette.color(QPalette.ColorRole.Window)
    base = palette.color(QPalette.ColorRole.Base)
    text = composite(palette.color(QPalette.ColorRole.Text), base)
    window_text = composite(palette.color(QPalette.ColorRole.WindowText), window)
    gutter = mix(base, window, 0.5)
    muted = _first_passing(window, text, (window, base, gutter), TEXT_MIN_CONTRAST)
    edge = _first_passing(window, text, (window, base), EDGE_CONTRAST)
    roles = {
        QPalette.ColorRole.AlternateBase: gutter,
        QPalette.ColorRole.PlaceholderText: muted,
        QPalette.ColorRole.Mid: edge,
        QPalette.ColorRole.Link: QColor(ACCENT_TEXT_DARK),
        QPalette.ColorRole.ToolTipBase: window,
        QPalette.ColorRole.ToolTipText: window_text,
    }
    for role, color in roles.items():
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, role, color)
    # macOS reports Inactive ButtonText as opaque black in both schemes, so
    # an unfocused dark window would draw black toolbar labels.
    palette.setColor(
        QPalette.ColorGroup.Inactive,
        QPalette.ColorRole.ButtonText,
        palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText),
    )
    disabled_fill = mix(window, QColor(ACCENT), _ACCENT_DISABLED_SHARE)
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QPalette.ColorRole.Accent, QColor(ACCENT_TEXT_DARK))
        palette.setColor(
            group, QPalette.ColorRole.HighlightedText, QColor(TEXT_ON_ACCENT)
        )
        palette.setColor(group, QPalette.ColorRole.Light, disabled_fill)
    _set_disabled_text(palette)
    return palette


def _set_disabled_text(palette: QPalette) -> None:
    """Disabled text and icon roles: an opaque blend of Text toward Window.

    The platform fade is 25% alpha on macOS (about 1.9:1 in light and 2.1:1
    in dark on the toolbar), so Coming soon buttons read as faint smudges.
    The blend stops at the first step that reaches 3:1 on the window, the
    base, and the button face.
    """
    window = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
    base = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base)
    text = composite(
        palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text), window
    )
    face = button_face(palette)
    faded = _first_passing(window, text, (window, base, face), DISABLED_TEXT_CONTRAST)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, faded)


def button_face(palette: QPalette) -> QColor:
    """Where native buttons draw their face: a step from Window toward Text."""
    window = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
    text = composite(
        palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text), window
    )
    return mix(window, text, _BUTTON_FACE_SHARE)


def is_dark(palette: QPalette) -> bool:
    """Whether ``palette`` paints a dark window."""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def error_color(palette: QPalette) -> QColor:
    """Error ink for the scheme ``palette`` belongs to."""
    return QColor(ERROR_DARK if is_dark(palette) else ERROR)


def ink_on(fill: QColor, palette: QPalette) -> QColor:
    """Label color on a filled shape: HighlightedText or Base, whichever
    reads better. White on the light error, the dark Base on #FF7B72, where
    white is about 2.5:1.
    """
    label = composite(palette.color(QPalette.ColorRole.HighlightedText), fill)
    base = composite(palette.color(QPalette.ColorRole.Base), fill)
    if contrast_ratio(label, fill) >= contrast_ratio(base, fill):
        return label
    return base


def line_number_color(palette: QPalette) -> QColor:
    """Gutter numerals: muted text in light mode, LINE_NUMBER_DARK in dark."""
    if is_dark(palette):
        return QColor(LINE_NUMBER_DARK)
    return palette.color(QPalette.ColorRole.PlaceholderText)


def problem_line_tint(palette: QPalette) -> QColor:
    """Band behind a problem line. Dark mode mixes the error into Base."""
    if not is_dark(palette):
        return QColor(ERROR_TINT)
    base = palette.color(QPalette.ColorRole.Base)
    return mix(base, QColor(ERROR_DARK), _ERROR_TINT_DARK_SHARE)


def _first_passing(
    start: QColor, end: QColor, surfaces: tuple[QColor, ...], minimum: float
) -> QColor:
    """The least move from ``start`` toward ``end`` that reaches ``minimum``."""
    for step in range(101):
        candidate = mix(start, end, step / 100)
        if all(contrast_ratio(candidate, surface) >= minimum for surface in surfaces):
            return candidate
    return QColor(end)


def _turned_over(palette: QPalette) -> QPalette:
    """``palette`` with each role's HSL lightness reversed."""
    turned = QPalette(palette)
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role in QPalette.ColorRole:
            if role == QPalette.ColorRole.NColorRoles:
                continue
            color = palette.color(group, role)
            hue, saturation, lightness, alpha = color.getHslF()
            turned.setColor(
                group, role, QColor.fromHslF(hue, saturation, 1 - lightness, alpha)
            )
    return turned


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
