"""Widget stylesheets built from Broadsheet roles."""

from galley.app.theme import (
    ACCENT,
    ACCENT_DISABLED,
    ACCENT_STRONG,
    GUTTER_BG,
    PANE_EDGE,
    TEXT,
    TEXT_MUTED,
    TEXT_ON_ACCENT,
    WINDOW_BG,
)


def primary_button_style() -> str:
    """Accent fill with white label. Disabled uses ``accent.disabled``."""
    return f"""
        QPushButton {{
            background-color: {ACCENT};
            color: {TEXT_ON_ACCENT};
            border: none;
            border-radius: 4px;
            padding: 6px 14px;
            font-weight: 700;
        }}
        QPushButton:disabled {{
            background-color: {ACCENT_DISABLED};
            color: {TEXT_ON_ACCENT};
        }}
        """


def secondary_button_style() -> str:
    """Quiet button on the window background with a pane edge."""
    return f"""
        QPushButton {{
            background-color: {WINDOW_BG};
            color: {TEXT};
            border: 1px solid {PANE_EDGE};
            border-radius: 4px;
            padding: 6px 14px;
        }}
        """


def link_button_style() -> str:
    """Footer link. Disabled links use the faded accent."""
    return f"""
        QPushButton {{
            background: transparent;
            color: {ACCENT};
            border: none;
            padding: 0 4px;
        }}
        QPushButton:disabled {{
            color: {ACCENT_DISABLED};
        }}
        """


def progress_style() -> str:
    """Write-progress track on ``gutter.bg`` with an ``accent`` fill."""
    return f"""
        QProgressBar {{
            background: {GUTTER_BG};
            border: none;
            border-radius: 2px;
            text-align: center;
            color: {TEXT};
            min-height: 14px;
            max-height: 14px;
        }}
        QProgressBar::chunk {{
            background: {ACCENT};
        }}
        """


def menu_style() -> str:
    """Menu bar and menu highlight from the spec (accent.strong, white)."""
    return f"""
        QMenuBar {{
            background: {WINDOW_BG};
            color: {TEXT};
        }}
        QMenuBar::item:selected {{
            background: {ACCENT_STRONG};
            color: {TEXT_ON_ACCENT};
        }}
        QMenu {{
            background: {WINDOW_BG};
            color: {TEXT};
        }}
        QMenu::item:selected {{
            background: {ACCENT_STRONG};
            color: {TEXT_ON_ACCENT};
        }}
        """


def pane_style(object_name: str) -> str:
    """1 px pane edge, thickening to a 2 px accent ring while focused."""
    return f"""
        QWidget#{object_name} {{
            background-color: {WINDOW_BG};
            border: 1px solid {PANE_EDGE};
        }}
        QWidget#{object_name}[paneFocused="true"] {{
            border: 2px solid {ACCENT};
        }}
        """


def status_bar_style() -> str:
    """Status bar surface. Messages use the muted text color.

    Left and right padding keep the message off the frame so the first
    glyph is not clipped.
    """
    return f"""
        QStatusBar {{
            background: {WINDOW_BG};
            color: {TEXT_MUTED};
            padding-left: 8px;
            padding-right: 8px;
        }}
        QStatusBar::item {{
            border: none;
        }}
        """


def muted_text_style() -> str:
    """Captions, counts, and the resting status message."""
    return f"color: {TEXT_MUTED};"
