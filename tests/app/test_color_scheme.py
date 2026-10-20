"""Galley asks Qt for the light color scheme (spec §13: no dark mode)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

import galley.app.main as main_module
from galley.app.main import create_main_window


class _Hints:
    def __init__(self) -> None:
        self.requested: list[Qt.ColorScheme] = []

    def setColorScheme(self, scheme: Qt.ColorScheme) -> None:  # noqa: N802
        self.requested.append(scheme)


def _record_hints(monkeypatch) -> _Hints:
    hints = _Hints()

    class _Gui:
        @staticmethod
        def styleHints() -> _Hints:  # noqa: N802
            return hints

    monkeypatch.setattr(main_module, "QGuiApplication", _Gui)
    return hints


def test_created_window_requests_the_light_scheme(qtbot, monkeypatch) -> None:
    hints = _record_hints(monkeypatch)
    window = create_main_window()
    qtbot.addWidget(window)
    assert hints.requested == [Qt.ColorScheme.Light]


def test_main_requests_the_light_scheme_after_the_application(monkeypatch) -> None:
    hints = _record_hints(monkeypatch)
    events: list[str] = []

    class _Application:
        def __init__(self, _argv) -> None:
            events.append("application")

        def __getattr__(self, _name):
            # Every call returns 0, so the event loop "exits" cleanly.
            return lambda *_args: 0

    class _Window:
        def __getattr__(self, _name):
            return lambda *_args: None

    def _create() -> _Window:
        events.append(f"window after {list(hints.requested)}")
        return _Window()

    monkeypatch.setattr(main_module, "QApplication", _Application)
    monkeypatch.setattr(main_module, "create_main_window", _create)
    assert main_module.main(["galley"]) == 0
    assert events == ["application", f"window after {[Qt.ColorScheme.Light]}"]


def test_style_hints_never_report_dark(qtbot) -> None:
    window = create_main_window()
    qtbot.addWidget(window)
    # The offscreen platform has no scheme and stays Unknown.
    assert QGuiApplication.styleHints().colorScheme() in (
        Qt.ColorScheme.Light,
        Qt.ColorScheme.Unknown,
    )
