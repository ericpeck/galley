"""View › Font: the family and size of editor text.

The choice is saved in QSettings ``editor/font_family`` and
``editor/font_size`` and read before the first window shows. An empty
family is Default Font, the platform monospace font from
:func:`~galley.app.fonts.code_font`. A saved family that is missing or not
monospace falls back to Default Font without a word.

Changes made in one event-loop pass are applied together: five Bigger
steps in a row emit :attr:`EditorFont.changed` once, so the editors lay
out their text once.
"""

from typing import Final

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from galley.app.fonts import CODE_POINT_SIZE, code_font
from galley.app.problems_settings import problems_settings

DEFAULT_SIZE: Final = CODE_POINT_SIZE
MIN_SIZE: Final = 8
MAX_SIZE: Final = 48

# Choose Font… choices awaiting UI review; each is one switch.
CHOOSE_FONT_TITLE: Final = "Choose Font"
# The macOS native panel ignores MonospacedFonts, so Qt's own dialog runs
# on every platform. True would list every family on macOS.
CHOOSE_FONT_NATIVE: Final = False
# The size picked in the dialog applies too, clamped to 8–48.
CHOOSE_FONT_SETS_SIZE: Final = True
# After Default Font, the picked family stays listed (unchecked) for the
# rest of the session. It is not saved, so a restart drops it.
KEEP_PICKED_FAMILY: Final = True

_FAMILY_KEY = "editor/font_family"
_SIZE_KEY = "editor/font_size"


def is_monospace_family(family: str) -> bool:
    """True when this machine has ``family`` and it is fixed pitch."""
    return family in QFontDatabase.families() and QFontDatabase.isFixedPitch(family)


def clamp_size(size: int) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, size))


def load_editor_font() -> tuple[str, int]:
    """Return the saved family ("" for Default Font) and size."""
    settings = problems_settings()
    family = settings.value(_FAMILY_KEY, "")
    if not isinstance(family, str) or not is_monospace_family(family):
        family = ""
    try:
        size = clamp_size(int(str(settings.value(_SIZE_KEY, DEFAULT_SIZE))))
    except ValueError:
        size = DEFAULT_SIZE
    return family, size


def save_editor_font(family: str, size: int) -> None:
    """Store the family and size so the next launch starts with them."""
    settings = problems_settings()
    settings.setValue(_FAMILY_KEY, family)
    settings.setValue(_SIZE_KEY, size)
    settings.sync()


class EditorFont(QObject):
    """The editor font for every window. One per QApplication."""

    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.setObjectName("galleyEditorFont")
        self._family = ""
        self._size = DEFAULT_SIZE
        # The family Choose Font… picked this session, kept for the menu
        # after a switch back to Default Font.
        self._picked = ""
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(0)
        self._flush.timeout.connect(self._apply)

    def family(self) -> str:
        """The family in use, or "" for Default Font."""
        return self._family

    def picked_family(self) -> str:
        """The family to list above Choose Font…, or "" for none."""
        return self._picked

    def size(self) -> int:
        return self._size

    def font(self) -> QFont:
        font = code_font(self._size)
        if self._family:
            # The monospace hint stays as the fallback.
            font.setFamilies([self._family])
        return font

    def pending(self) -> bool:
        """True while a change waits for the next event-loop pass."""
        return self._flush.isActive()

    def set_family(self, family: str) -> None:
        """Use ``family``, or Default Font for "". Ignored if not monospace."""
        if family and not is_monospace_family(family):
            return
        if family or not KEEP_PICKED_FAMILY:
            self._picked = family
        self._update(family, self._size)

    def set_size(self, size: int) -> None:
        self._update(self._family, clamp_size(size))

    def step(self, delta: int) -> None:
        """Bigger (+1) or Smaller (-1), within 8–48 points."""
        self.set_size(self._size + delta)

    def reset_size(self) -> None:
        """Actual Size: the default size, same family."""
        self.set_size(DEFAULT_SIZE)

    def restore(self) -> None:
        """Take the saved family and size now, without saving them again."""
        self._flush.stop()
        self._family, self._size = load_editor_font()
        self._picked = self._family
        self.changed.emit()

    def _update(self, family: str, size: int) -> None:
        if (family, size) == (self._family, self._size):
            return
        self._family, self._size = family, size
        self._flush.start()

    def _apply(self) -> None:
        save_editor_font(self._family, self._size)
        self.changed.emit()


def install_editor_font(app: QApplication) -> EditorFont:
    """Create the application's EditorFont if needed and read the saved font."""
    editor_font = application_editor_font(app)
    if editor_font is None:
        editor_font = EditorFont(app)
    editor_font.restore()
    return editor_font


def application_editor_font(app: QApplication | None = None) -> EditorFont | None:
    """The EditorFont installed on ``app`` (default: the running app)."""
    if app is None:
        instance = QApplication.instance()
        if not isinstance(instance, QApplication):
            return None
        app = instance
    return app.findChild(EditorFont, "galleyEditorFont")
