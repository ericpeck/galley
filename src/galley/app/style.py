"""Widget stylesheets from palette roles.

The Format button fill is the one UI surface that names the brand hex.
Everything else asks the palette, which the light samples seed.
"""

from collections.abc import Callable

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QWidget

from galley.app.theme import ACCENT, pane_edge_colors


def primary_button_style() -> str:
    """Brand fill, palette label. Disabled uses the light role."""
    return f"""
        QPushButton {{
            background-color: {ACCENT};
            color: palette(highlighted-text);
            border: none;
            border-radius: 4px;
            padding: 6px 14px;
            font-weight: 700;
        }}
        QPushButton:disabled {{
            background-color: palette(light);
            color: palette(highlighted-text);
        }}
        """


def secondary_button_style() -> str:
    """Quiet button on the button role with a mid border."""
    return """
        QPushButton {
            background-color: palette(button);
            color: palette(button-text);
            border: 1px solid palette(mid);
            border-radius: 4px;
            padding: 6px 14px;
        }
        """


def link_button_style() -> str:
    """Footer link. Disabled links use the light role."""
    return """
        QPushButton {
            background: transparent;
            color: palette(link);
            border: none;
            padding: 0 4px;
        }
        QPushButton:disabled {
            color: palette(light);
        }
        """


def progress_style() -> str:
    """Write-progress track on the alternate base with an accent fill."""
    return """
        QProgressBar {
            background: palette(alternate-base);
            border: none;
            border-radius: 2px;
            text-align: center;
            color: palette(text);
            min-height: 14px;
            max-height: 14px;
        }
        QProgressBar::chunk {
            background: palette(accent);
        }
        """


def menu_style() -> str:
    """Menu bar and menus. The selected row uses the link role.

    The selected row's label is the base role: white on the light link,
    and the dark base on the lighter dark-mode link, where a white label
    would fall below 4.5:1.

    Disabled rows use the disabled window text. Fusion ignores
    ``palette(disabled, …)`` on a menu item and etches a brighter color,
    so that role is written out as components from the current palette.
    """
    app = QApplication.instance()
    palette = app.palette() if isinstance(app, QApplication) else QPalette()
    disabled = palette.color(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText
    )
    disabled_ink = f"rgb({disabled.red()}, {disabled.green()}, {disabled.blue()})"
    return f"""
        QMenuBar {{
            background: palette(window);
        }}
        QMenuBar::item {{
            color: palette(window-text);
        }}
        QMenuBar::item:selected {{
            background: palette(link);
            color: palette(base);
        }}
        QMenuBar::item:disabled {{
            color: {disabled_ink};
        }}
        QMenu {{
            background: palette(window);
        }}
        QMenu::item {{
            color: palette(window-text);
        }}
        QMenu::item:selected {{
            background: palette(link);
            color: palette(base);
        }}
        QMenu::item:disabled {{
            color: {disabled_ink};
        }}
        """


def pane_style(object_name: str) -> str:
    """1 px edge, thickening to a 2 px ring while focused or a drop target.

    The edge starts from ``Mid`` and the ring from ``Accent``. Each is
    lifted toward the text color until it is 3:1 on the window and the
    base, so the macOS dark accent still shows. Light mode keeps both roles.
    """
    app = QApplication.instance()
    palette = app.palette() if isinstance(app, QApplication) else QPalette()
    edge, ring = pane_edge_colors(palette)
    return f"""
        QWidget#{object_name} {{
            background-color: palette(window);
            border: 1px solid {edge.name()};
        }}
        QWidget#{object_name}[paneFocused="true"],
        QWidget#{object_name}[paneDropTarget="true"] {{
            border: 2px solid {ring.name()};
        }}
        """


def status_bar_style() -> str:
    """Status bar surface. Messages use the placeholder text role.

    Left and right padding keep the message off the frame so the first
    glyph is not clipped.
    """
    return """
        QStatusBar {
            background: palette(window);
            color: palette(placeholder-text);
            padding-left: 8px;
            padding-right: 8px;
        }
        QStatusBar::item {
            border: none;
        }
        """


def muted_text_style() -> str:
    """Captions, counts, and the resting status message."""
    return "color: palette(placeholder-text);"


def palette_baked_styles() -> tuple[Callable[[QWidget], str], ...]:
    """Builders for stylesheets that write palette colors out as values.

    Each takes the widget the stylesheet is on. A scheme switch rebuilds
    any stylesheet one of these produced, because re-parsing the old text
    would keep the old colors.
    """
    return (_menu_style_for, _pane_style_for)


def _menu_style_for(_widget: QWidget) -> str:
    return menu_style()


def _pane_style_for(widget: QWidget) -> str:
    return pane_style(widget.objectName())
