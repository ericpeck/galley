"""Remember Problems pane state, the Input / Output split, stacking, Sort keys,
and the indent size."""

from PySide6.QtCore import QSettings

from galley.app.indent import DEFAULT_INDENT_CHOICE, is_indent_choice

STATE_OPEN = "open"
STATE_MINIMIZED = "minimized"
STATE_CLOSED = "closed"
DEFAULT_WIDTH = 280
MIN_WIDTH = 200
STRIP_WIDTH = 28

_STATE_KEY = "problems/state"
_WIDTH_KEY = "problems/width"
_STACKED_KEY = "view/stackPanes"
_SPLIT_KEY = "view/splitSizes"
_SORT_KEYS_KEY = "format/sortKeys"
_INDENT_KEY = "format/indentSize"
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


def load_sort_keys() -> bool:
    """Return whether Sort keys was last saved on. A fresh install is off."""
    return _as_bool(problems_settings().value(_SORT_KEYS_KEY, False))


def save_sort_keys(sort_keys: bool) -> None:
    """Store the Sort keys toggle so the next launch can restore it."""
    settings = problems_settings()
    settings.setValue(_SORT_KEYS_KEY, bool(sort_keys))
    settings.sync()


def load_indent_size() -> str:
    """Return the saved indent size picker choice. A fresh install is 2 spaces."""
    choice = problems_settings().value(_INDENT_KEY, DEFAULT_INDENT_CHOICE)
    return choice if is_indent_choice(choice) else DEFAULT_INDENT_CHOICE


def save_indent_size(choice: str) -> None:
    """Store the indent size picker choice so the next launch can restore it."""
    settings = problems_settings()
    settings.setValue(_INDENT_KEY, choice)
    settings.sync()


def load_split_sizes() -> list[int] | None:
    """Return the saved Input and Output sizes, or None for an even split."""
    value = problems_settings().value(_SPLIT_KEY)
    if value is None:
        return None
    parts = value if isinstance(value, list) else str(value).split(",")
    try:
        sizes = [int(str(part)) for part in parts]
    except (TypeError, ValueError):
        return None
    if len(sizes) != 2 or min(sizes) <= 0:
        return None
    return sizes


def save_split_sizes(sizes: list[int]) -> None:
    """Store the Input and Output sizes so the next launch keeps the ratio."""
    settings = problems_settings()
    settings.setValue(_SPLIT_KEY, ",".join(str(int(size)) for size in sizes))
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
