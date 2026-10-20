"""Keep Qt settings from one app test out of the next."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette


@pytest.fixture(autouse=True)
def isolated_settings(qapp, tmp_path: Path) -> None:
    """Point QSettings at a throwaway directory for this test."""
    del qapp
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path),
    )


_SCHEMES = {
    "light": {
        QPalette.ColorRole.Window: "#F3F2F2",
        QPalette.ColorRole.Base: "#FFFFFF",
        QPalette.ColorRole.WindowText: "#201E1D",
        QPalette.ColorRole.Text: "#201E1D",
        QPalette.ColorRole.PlaceholderText: "#5E5B5B",
        QPalette.ColorRole.Mid: "#7D7979",
        QPalette.ColorRole.Highlight: "#99E0FF",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.Accent: "#0080A6",
    },
    # macOS Dark Mode roles as cocoa reports them. Text roles carry alpha
    # (#AARRGGBB), so a test sees the blended color users see.
    "dark": {
        QPalette.ColorRole.Window: "#323232",
        QPalette.ColorRole.Base: "#171717",
        QPalette.ColorRole.WindowText: "#D8FFFFFF",
        QPalette.ColorRole.Text: "#D8FFFFFF",
        QPalette.ColorRole.PlaceholderText: "#3FFFFFFF",
        QPalette.ColorRole.Mid: "#242424",
        QPalette.ColorRole.Highlight: "#314F78",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.Accent: "#0A60FF",
    },
}


@pytest.fixture(params=["light", "dark"])
def scheme_palette(request, qapp) -> Iterator[QPalette]:
    """Install a light or dark application palette for one test.

    Offscreen ignores the OS color scheme, so the test sets one itself.
    Stylesheet ``palette(...)`` values come from the application palette,
    so create widgets after this fixture runs.
    """
    previous = QPalette(qapp.palette())
    palette = QPalette(previous)
    for role, color in _SCHEMES[request.param].items():
        palette.setColor(role, QColor(color))
    qapp.setPalette(palette)
    yield palette
    qapp.setPalette(previous)
