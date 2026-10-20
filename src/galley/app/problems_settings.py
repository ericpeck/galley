"""Remember Problems pane state and whether Input and Output are stacked."""

from PySide6.QtCore import QSettings

STATE_OPEN = "open"
STATE_MINIMIZED = "minimized"
STATE_CLOSED = "closed"
DEFAULT_WIDTH = 280
MIN_WIDTH = 200
STRIP_WIDTH = 28

_STATE_KEY = "problems/state"
_WIDTH_KEY = "problems/width"
_STACKED_KEY = "view/stackPanes"
_STATES = frozenset({STATE_OPEN, STATE_MINIMIZED, STATE_CLOSED})


def problems_settings() -> QSettings:
    """User settings for Galley, stored with Qt's per-platform Ini layout."""
    return QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        "Galley",
        "Galley",
    )


def load_problems_preferences() -> tuple[str, int]:
    """Return the saved pane state and width."""
    settings = problems_settings()
    state = settings.value(_STATE_KEY, STATE_OPEN)
    if not isinstance(state, str) or state not in _STATES:
        state = STATE_OPEN
    width = _width(settings.value(_WIDTH_KEY, DEFAULT_WIDTH))
    return state, width


def save_problems_preferences(state: str, width: int) -> None:
    """Store the pane state and width so the next launch can restore them."""
    settings = problems_settings()
    settings.setValue(_STATE_KEY, state)
    settings.setValue(_WIDTH_KEY, width)
    settings.sync()


def load_stacked_panes() -> bool:
    """Return whether Input was last saved over Output."""
    return _as_bool(problems_settings().value(_STACKED_KEY, False))


def save_stacked_panes(stacked: bool) -> None:
    """Store the pane layout so the next launch can restore it."""
    settings = problems_settings()
    settings.setValue(_STACKED_KEY, bool(stacked))
    settings.sync()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if isinstance(value, int):
        return value != 0
    return False


def _width(value: object) -> int:
    try:
        width = int(str(value))
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    if width < MIN_WIDTH:
        return DEFAULT_WIDTH
    return width
