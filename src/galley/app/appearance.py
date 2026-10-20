"""View › Appearance: System, Light, or Dark.

The choice is saved in QSettings ``appearance/mode`` and applied before
the first window shows. ``styleHints().setColorScheme`` asks the platform
for the scheme (``Unknown`` follows the OS). The application palette is
then rebuilt from the platform's palette for that scheme. It is rebuilt
again whenever the scheme changes, because an explicit application
palette does not follow the OS by itself.
"""

from collections.abc import Callable
from typing import Final

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from galley.app.problems_settings import problems_settings
from galley.app.style import palette_baked_styles
from galley.app.theme import dark_palette, is_dark, light_palette

MODE_SYSTEM: Final = "system"
MODE_LIGHT: Final = "light"
MODE_DARK: Final = "dark"
MODES: Final = (MODE_SYSTEM, MODE_LIGHT, MODE_DARK)
# Menu labels in menu order.
MODE_LABELS: Final[dict[str, str]] = {
    MODE_SYSTEM: "System",
    MODE_LIGHT: "Light",
    MODE_DARK: "Dark",
}

_MODE_KEY = "appearance/mode"
_SCHEMES: Final[dict[str, Qt.ColorScheme]] = {
    MODE_SYSTEM: Qt.ColorScheme.Unknown,
    MODE_LIGHT: Qt.ColorScheme.Light,
    MODE_DARK: Qt.ColorScheme.Dark,
}


def load_appearance_mode() -> str:
    """Return the saved mode, or System when nothing valid is saved."""
    mode = problems_settings().value(_MODE_KEY, MODE_SYSTEM)
    if not isinstance(mode, str) or mode not in MODES:
        return MODE_SYSTEM
    return mode


def save_appearance_mode(mode: str) -> None:
    """Store the mode so the next launch starts in it."""
    settings = problems_settings()
    settings.setValue(_MODE_KEY, mode)
    settings.sync()


class Appearance(QObject):
    """The application's appearance mode. One per QApplication."""

    mode_changed = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.setObjectName("galleyAppearance")
        self._app = app
        self._mode = MODE_SYSTEM
        self._system_scheme: Qt.ColorScheme | None = None
        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(self._on_scheme_changed)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, *, save: bool = True) -> None:
        """Switch to ``mode`` now, in every window. Save it unless told not to."""
        if mode not in MODES:
            raise ValueError(f"unknown appearance mode: {mode!r}")
        changed = mode != self._mode
        self._mode = mode
        self._system_scheme = None
        if save:
            save_appearance_mode(mode)
        QGuiApplication.styleHints().setColorScheme(_SCHEMES[mode])
        self.apply()
        if changed:
            self.mode_changed.emit(mode)

    def restore(self) -> None:
        """Apply the saved mode without saving it again."""
        self.set_mode(load_appearance_mode(), save=False)

    def apply(self) -> None:
        """Rebuild the application palette for the current scheme."""
        baked = _baked_styles()
        # A default palette drops the explicit one, so this reads the
        # platform's palette for the scheme just requested.
        self._app.setPalette(QPalette())
        platform = QPalette(self._app.palette())
        if self._wants_dark(platform):
            self._app.setPalette(dark_palette(platform))
        else:
            self._app.setPalette(light_palette(platform))
        _restyle(self._app, baked)

    def _wants_dark(self, platform: QPalette) -> bool:
        if self._mode == MODE_DARK:
            return True
        if self._mode == MODE_LIGHT:
            return False
        scheme = self._system_scheme
        if scheme is None:
            scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
        return is_dark(platform)

    def _on_scheme_changed(self, scheme: Qt.ColorScheme) -> None:
        if self._mode != MODE_SYSTEM:
            return
        self._system_scheme = scheme
        # Qt emits this before it updates the platform palette.
        QTimer.singleShot(0, self, self.apply)


def set_application_palette(app: QApplication, palette: QPalette) -> None:
    """Install ``palette`` and bring every widget stylesheet up to date."""
    baked = _baked_styles()
    app.setPalette(palette)
    _restyle(app, baked)


def _baked_styles() -> dict[str, Callable[[], str]]:
    """Current text of each palette-baked stylesheet, mapped to its builder."""
    return {style(): style for style in palette_baked_styles()}


def _restyle(app: QApplication, baked: dict[str, Callable[[], str]]) -> None:
    """Parse each widget stylesheet again.

    A stylesheet resolves ``palette(...)`` when it is applied, so without
    this the status bar, links, and menus keep the old scheme's colors.
    ``baked`` maps the old text of a stylesheet that writes colors out as
    values to the function that rebuilds it.
    """
    for widget in app.allWidgets():
        sheet = widget.styleSheet()
        if not sheet:
            continue
        rebuild = baked.get(sheet)
        widget.setStyleSheet("")
        widget.setStyleSheet(rebuild() if rebuild is not None else sheet)


def install_appearance(app: QApplication) -> Appearance:
    """Create the application's Appearance if needed and apply the saved mode."""
    appearance = application_appearance(app)
    if appearance is None:
        appearance = Appearance(app)
    appearance.restore()
    return appearance


def application_appearance(app: QApplication | None = None) -> Appearance | None:
    """The Appearance installed on ``app`` (default: the running app)."""
    if app is None:
        instance = QApplication.instance()
        if not isinstance(instance, QApplication):
            return None
        app = instance
    return app.findChild(Appearance, "galleyAppearance")
