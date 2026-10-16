"""UI and code fonts for the shell.

The spec leaves the exact families open, so these are Qt's generic serif
and monospace. A later licensing choice can replace the family names here.
"""

from PySide6.QtGui import QFont

UI_POINT_SIZE = 12
CODE_POINT_SIZE = 12


def ui_font(point_size: int = UI_POINT_SIZE) -> QFont:
    """Return the serif font used for chrome, menus, and status text."""
    font = QFont()
    font.setFamilies(["serif"])
    font.setStyleHint(QFont.StyleHint.Serif)
    font.setPointSize(point_size)
    return font


def code_font(point_size: int = CODE_POINT_SIZE) -> QFont:
    """Return the monospace font used for editor text and line numbers."""
    font = QFont()
    font.setFamilies(["monospace"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(point_size)
    return font
