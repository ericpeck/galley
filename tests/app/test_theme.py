"""Broadsheet role hex values from the UI spec."""

from galley.app.theme import COLORS, TEXT_ON_ACCENT

# Copied from the spec table so this fails if a role drifts.
SPEC_COLORS = {
    "window.bg": "#F3F2F2",
    "editor.bg": "#EAE7E7",
    "gutter.bg": "#D7D3D3",
    "pane.edge": "#7D7979",
    "line.current": "#DEDBDB",
    "dialog.bg": "#EAE9E9",
    "card.bg": "#F3F2F2",
    "scrim": "#B5B2B2",
    "text": "#201E1D",
    "text.code.value": "#2D2B2B",
    "text.muted": "#5E5B5B",
    "accent": "#0080A6",
    "accent.strong": "#006786",
    "accent.ink": "#004961",
    "accent.tint": "#E9F8FF",
    "accent.disabled": "#86BFD0",
    "selection": "#99E0FF",
    "info.tint": "#C2D6DC",
    "string": "#AA0B56",
    "error": "#AA0B56",
    "error.tint": "#FFF1F4",
    "error.icon.ring": "#EEC3D4",
    "amber.tint": "#F1E3BE",
    "amber.ink": "#6E4604",
    "guide.depth.blue": "#62C5EE",
    "guide.depth.pink": "#FF90B1",
}


def test_theme_constants_match_spec() -> None:
    assert COLORS == SPEC_COLORS
    assert COLORS["accent"] == "#0080A6"
    assert COLORS["text.muted"] == "#5E5B5B"
    assert COLORS["accent.disabled"] == "#86BFD0"


def test_button_label_on_accent_is_white() -> None:
    assert TEXT_ON_ACCENT == "#FFFFFF"
