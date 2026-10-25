"""File › Open Recent: the stored list and the submenu (#85).

The list lives in QSettings next to Open's last folder. Order, the cap and
the labels come from :mod:`galley.core.recent`. Choosing an entry asks the
window to open it, so recents get the same replace prompt, size cap, and
off-thread read as every other Open.
"""

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from galley.app.problems_settings import problems_settings
from galley.core.recent import add_recent, clean_recent, recent_labels

RECENT_FILES_KEY = "open/recentFiles"
OPEN_RECENT_MENU = "Open Recent"
CLEAR_MENU = "Clear Menu"
FILE_NOT_FOUND = "File not found"
NOT_FOUND_SUFFIX = " (not found)"


def load_recent_files() -> list[str]:
    """Return the stored recent paths, newest first."""
    value = problems_settings().value(RECENT_FILES_KEY, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return clean_recent(value)


def remember_recent_file(path: Path) -> None:
    """Put ``path`` first in the stored list."""
    entries = add_recent(load_recent_files(), str(path.absolute()))
    _save(entries)


def clear_recent_files() -> None:
    """Forget every recent file."""
    _save([])


def _save(entries: list[str]) -> None:
    settings = problems_settings()
    settings.setValue(RECENT_FILES_KEY, entries)
    settings.sync()


class RecentFilesMenu(QMenu):
    """The Open Recent submenu, rebuilt from settings each time it opens.

    Entries show the file name, and the full path only in the tooltip. A
    missing file stays listed but disabled, reads "name.json (not found)"
    so dimming is not the only cue, and has the tooltip "File not found".
    ``open_requested(Path)`` reports the entry the user chose.
    """

    open_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(OPEN_RECENT_MENU, parent)
        self.setObjectName("menuOpenRecent")
        self.setToolTipsVisible(True)
        self.aboutToShow.connect(self.rebuild)
        self.triggered.connect(self._choose)
        self.rebuild()

    def rebuild(self) -> None:
        """Show the stored list as it is now, with Clear Menu at the bottom."""
        self.clear()
        entries = load_recent_files()
        for entry, label in zip(entries, recent_labels(entries), strict=True):
            found = Path(entry).is_file()
            text = label if found else label + NOT_FOUND_SUFFIX
            # A lone & would underline the next letter instead of showing.
            action = self.addAction(text.replace("&", "&&"))
            action.setEnabled(found)
            action.setToolTip(entry if found else FILE_NOT_FOUND)
            action.setData(entry)
        if entries:
            self.addSeparator()
        clear = self.addAction(CLEAR_MENU)
        clear.setObjectName("clearRecentFiles")
        clear.setEnabled(bool(entries))
        clear.triggered.connect(self._clear)

    def _choose(self, action: QAction) -> None:
        entry = action.data()
        if isinstance(entry, str) and entry:
            self.open_requested.emit(Path(entry))

    def _clear(self) -> None:
        clear_recent_files()
        # Rebuilding deletes this action, so wait until its signal returns.
        QTimer.singleShot(0, self, self.rebuild)
