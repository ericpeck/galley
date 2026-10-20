"""UI and code fonts for the shell.

The UI font is the platform system font from
``QFontDatabase.systemFont(GeneralFont)``. The family is not named and
no font file is bundled. Editor text stays monospace.
"""

from PySide6.QtGui import QFont, QFontDatabase

UI_POINT_SIZE = 12
CODE_POINT_SIZE = 12


def ui_font(point_size: int = UI_POINT_SIZE) -> QFont:
    """Return the platform system font used for chrome, menus, and status."""
    font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
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
