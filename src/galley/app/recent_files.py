"""File › Open Recent: the stored list and the submenu (#85).

The list lives in QSettings next to Open's last folder. Order, the cap and
the labels come from :mod:`galley.core.recent`. Choosing an entry asks the
window to open it, so recents get the same replace prompt, size cap, and
off-thread read as every other Open.

Whether each file still exists is checked in the thread pool (#114): a
``stat`` on a sleeping drive or a lost network share can block for seconds.
The menu shows the last answer at once and updates a row when its check
comes back.
"""

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
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


class FileChecker(QObject):
    """Check in the thread pool whether recent files still exist.

    ``checked`` carries the entry and whether it is a file. An entry has at
    most one check running, so a path that hangs ties up one thread, not
    one more each time the menu opens.
    """

    checked = Signal(str, bool)
    _finished = Signal(str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running: set[str] = set()
        # Queued from the pool thread; ``checked`` then goes out on this one.
        self._finished.connect(self._done)

    def check(self, entries: list[str]) -> None:
        for entry in entries:
            if entry in self._running:
                continue
            self._running.add(entry)
            # The global pool, as Open uses, so quitting never waits on it.
            QThreadPool.globalInstance().start(_CheckTask(self, entry))

    def busy(self) -> bool:
        """True while any check has not come back."""
        return bool(self._running)

    def _done(self, entry: str, found: bool) -> None:
        self._running.discard(entry)
        self.checked.emit(entry, found)


class _CheckTask(QRunnable):
    def __init__(self, checker: FileChecker, entry: str) -> None:
        super().__init__()
        self._checker = checker
        self._entry = entry

    def run(self) -> None:
        try:
            found = Path(self._entry).is_file()
        except Exception:
            # Any failure still ends the check; Open reports the real error.
            found = False
        try:
            self._checker._finished.emit(self._entry, found)
        except RuntimeError:
            # The menu closed with its window while the check ran.
            return


class RecentFilesMenu(QMenu):
    """The Open Recent submenu, rebuilt from settings each time it opens.

    Entries show the file name, and the full path only in the tooltip. A
    missing file stays listed but disabled, reads "name.json (not found)"
    so dimming is not the only cue, and has the tooltip "File not found".
    ``open_requested(Path)`` reports the entry the user chose.

    The menu never waits on the disk. A row shows the last check's answer,
    or is enabled before its first check, and a check runs for every row
    each time the menu is built. Opening a file that has gone since still
    gets Open's "Couldn't find" message.
    """

    open_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(OPEN_RECENT_MENU, parent)
        self.setObjectName("menuOpenRecent")
        self.setToolTipsVisible(True)
        self._found: dict[str, bool] = {}
        self._labels: dict[str, str] = {}
        self._checker = FileChecker(self)
        self._checker.checked.connect(self._apply_check)
        self.aboutToShow.connect(self.rebuild)
        self.triggered.connect(self._choose)
        self.rebuild()

    def checking(self) -> bool:
        """True while a file check has not come back."""
        return self._checker.busy()

    def rebuild(self) -> None:
        """Show the stored list as it is now, with Clear Menu at the bottom."""
        self.clear()
        entries = load_recent_files()
        self._labels = dict(zip(entries, recent_labels(entries), strict=True))
        self._found = {
            entry: found
            for entry, found in self._found.items()
            if entry in self._labels
        }
        for entry in entries:
            action = self.addAction("")
            action.setData(entry)
            self._show_state(action, entry)
        if entries:
            self.addSeparator()
        clear = self.addAction(CLEAR_MENU)
        clear.setObjectName("clearRecentFiles")
        clear.setEnabled(bool(entries))
        clear.triggered.connect(self._clear)

        self._checker.check(entries)

    def _apply_check(self, entry: str, found: bool) -> None:
        if entry not in self._labels or self._found.get(entry) == found:
            return
        self._found[entry] = found
        for action in self.actions():
            if action.data() == entry:
                self._show_state(action, entry)

    def _show_state(self, action: QAction, entry: str) -> None:
        found = self._found.get(entry, True)
        label = self._labels[entry]
        text = label if found else label + NOT_FOUND_SUFFIX
        # A lone & would underline the next letter instead of showing.
        action.setText(text.replace("&", "&&"))
        action.setEnabled(found)
        action.setToolTip(entry if found else FILE_NOT_FOUND)

    def _choose(self, action: QAction) -> None:
        entry = action.data()
        if isinstance(entry, str) and entry:
            self.open_requested.emit(Path(entry))

    def _clear(self) -> None:
        clear_recent_files()
        # Rebuilding deletes this action, so wait until its signal returns.
        QTimer.singleShot(0, self, self.rebuild)
