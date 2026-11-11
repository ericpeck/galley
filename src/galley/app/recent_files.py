"""File › Open Recent: the stored list and the submenu (#85).

The list lives in QSettings next to Open's last folder. Order, the cap and
the labels come from :mod:`galley.core.recent`. Choosing an entry asks the
window to open it, so recents get the same replace prompt, size cap, and
off-thread read as every other Open.

Whether each file still exists is checked off the UI thread (#114): a
``stat`` on a sleeping drive or a lost network share can block for seconds,
or forever on a hung mount. At most two checks run at once. The rest wait
in a queue, not on a thread, so a hung mount cannot park one thread per
row (#153). The menu shows the last answer at once and updates a row when
its check comes back.
"""

import os
import stat
import threading
import weakref
from collections import deque
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget
from shiboken6 import isValid

from galley.app.problems_settings import problems_settings
from galley.core.recent import add_recent, clean_recent, recent_labels

RECENT_FILES_KEY = "open/recentFiles"
OPEN_RECENT_MENU = "Open Recent"
CLEAR_MENU = "Clear Menu"
FILE_NOT_FOUND = "File not found"
NOT_FOUND_SUFFIX = " (not found)"
# How many checks may sit in stat at once, across every window. A check
# thread exists only while it holds one of these slots, so a hung mount
# cannot park a thread per row (#153).
CHECK_THREADS = 2
_check_slots = threading.Semaphore(CHECK_THREADS)
_check_lock = threading.Lock()


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


def recent_stat(entry: str) -> os.stat_result:
    """``os.stat`` for the checks. Tests replace it with a slow fake."""
    return os.stat(entry)


class FileChecker(QObject):
    """Check on daemon threads whether recent files still exist.

    ``checked`` carries the entry and whether it is a file. Across every
    window, at most :data:`CHECK_THREADS` checks sit in ``stat`` at once,
    and each of those holds the only thread its slot gets. Further checks
    wait in a queue until a slot is free. An entry has at most one check
    running per menu.

    The threads are not Qt's: Open's reader never waits behind a stuck
    check, and quitting never waits for one either, since Python leaves a
    daemon thread behind at exit. ``stat_file`` defaults to
    :func:`recent_stat`, looked up when each check runs. Destroying a menu
    drops the checks still queued for it. A check already in ``stat`` is
    left to return; the thread is not killed.
    """

    checked = Signal(str, bool)
    _finished = Signal(str, bool)

    def __init__(
        self,
        parent: QObject | None = None,
        stat_file: Callable[[str], os.stat_result] | None = None,
    ) -> None:
        super().__init__(parent)
        self._stat_file = stat_file
        self._running: set[str] = set()
        self._token = object()
        # Queued from a check thread; ``checked`` then goes out on this one.
        self._finished.connect(self._done)
        # A bound method on this object's own destroyed signal is never
        # called. The lambda holds the token, not this checker, so a closed
        # window is not kept alive by the queue.
        token = self._token
        self.destroyed.connect(lambda *_args, token=token: _cancel_queued(token))

    def check(self, entries: list[str]) -> None:
        _enqueue(self, entries)

    def busy(self) -> bool:
        """True while any check has not come back."""
        return bool(self._running)

    def _done(self, entry: str, found: bool) -> None:
        self._running.discard(entry)
        self.checked.emit(entry, found)


class _Pending:
    """One file check waiting for a free slot."""

    __slots__ = ("checker", "entry", "stat_file", "token")

    def __init__(self, checker: FileChecker, entry: str) -> None:
        self.token = checker._token
        self.entry = entry
        # None means :func:`recent_stat`, read when the check runs.
        self.stat_file = checker._stat_file
        self.checker = weakref.ref(checker)


_pending: deque[_Pending] = deque()


def _cancel_queued(token: object) -> None:
    """Drop checks still waiting for a slot after their menu is gone."""
    with _check_lock:
        kept = deque(job for job in _pending if job.token is not token)
        _pending.clear()
        _pending.extend(kept)


def _enqueue(checker: FileChecker, entries: list[str]) -> None:
    fresh: list[_Pending] = []
    for entry in entries:
        if entry in checker._running:
            continue
        checker._running.add(entry)
        fresh.append(_Pending(checker, entry))
    if not fresh:
        return
    with _check_lock:
        _pending.extend(fresh)
        _fill_slots()


def _fill_slots() -> None:
    """Start one thread per free slot that has a check waiting.

    Caller holds ``_check_lock``. The acquire does not block, so a check
    never parks a thread on the semaphore.
    """
    while _pending and _check_slots.acquire(blocking=False):
        threading.Thread(
            target=_worker,
            name="Open Recent check",
            daemon=True,
        ).start()


def _take_pending() -> _Pending | None:
    """Pop the next check whose checker still exists. Caller holds the lock."""
    while _pending:
        job = _pending.popleft()
        if job.checker() is not None:
            return job
    return None


def _live(job: _Pending) -> FileChecker | None:
    checker = job.checker()
    if checker is None or not isValid(checker):
        return None
    return checker


def _run_job(job: _Pending) -> None:
    if _live(job) is None:
        return
    stat_file = job.stat_file or recent_stat
    try:
        found = stat.S_ISREG(stat_file(job.entry).st_mode)
    except Exception:
        # Any failure still ends the check; Open reports the real error.
        found = False
    checker = _live(job)
    if checker is None:
        return
    try:
        checker._finished.emit(job.entry, found)
    except RuntimeError:
        # The menu closed with its window while the check ran.
        return


def _worker() -> None:
    try:
        while True:
            with _check_lock:
                job = _take_pending()
                if job is None:
                    return
            _run_job(job)
    finally:
        # Free the slot even when a check raises. A job queued after this
        # thread saw an empty queue still needs a thread; the slot is free
        # only once released, so refill under the same lock.
        with _check_lock:
            _check_slots.release()
            _fill_slots()


class RecentFilesMenu(QMenu):
    """The Open Recent submenu, rebuilt from settings each time it opens.

    Entries show the file name, and the full path only in the tooltip. A
    missing file stays listed but disabled, reads "name.json (not found)"
    so dimming is not the only cue, and has the tooltip "File not found".
    ``open_requested(Path)`` reports the entry the user chose.

    The menu never waits on the disk. A row shows the last check's answer,
    or is enabled before its first check, and a check runs for every row
    each time the menu is built. Opening a file that has gone since still
    gets Open's "Couldn't find" message. Known limits: a check that started
    before a file was deleted can call it found until the next build, a row
    whose stat never returns stays enabled, and any two stuck checks hold
    both slots, whichever windows they come from, so the other rows keep
    their last state until one returns.
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
