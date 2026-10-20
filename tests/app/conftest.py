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
    "dark": {
        QPalette.ColorRole.Window: "#1C1C1C",
        QPalette.ColorRole.Base: "#1C1C1C",
        QPalette.ColorRole.WindowText: "#F3F2F2",
        QPalette.ColorRole.Text: "#F3F2F2",
        QPalette.ColorRole.PlaceholderText: "#B5B1B1",
        QPalette.ColorRole.Mid: "#8A8686",
        QPalette.ColorRole.Highlight: "#0E4F66",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.Accent: "#3DB2DB",
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
