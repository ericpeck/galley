"""Words and settings for loading one file into the Input editor.

Reading and decoding live in :mod:`galley.core.open_text`. This module
names the file in the status bar, remembers the last folder and the
recent files, and labels the Open Recent menu. Visible text names the file
only; the full path goes in tooltips and accessible descriptions.
"""

import threading
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Signal

from galley.app.problems_settings import problems_settings
from galley.core.open_text import (
    MAX_OPEN_MB,
    OpenedText,
    OpenErrorKind,
    OpenTextError,
    read_text,
)

LAST_DIR_KEY = "open/lastDir"
RECENT_KEY = "open/recentFiles"
MAX_RECENT = 10
OPEN_FILE_FILTER = "JSON and YAML (*.json *.yaml *.yml);;All Files (*)"
OPEN_DIALOG_CAPTION = "Open"
REPLACE_BUTTON = "Replace"
CLEAR_RECENT = "Clear Menu"
RECENT_MISSING_TOOLTIP = "File not found"
OPEN_WHILE_FORMATTING_TOOLTIP = "Wait for formatting to finish"
DROP_WHILE_FORMATTING = "Can't open a file while formatting."
OPENED_STATUS_MS = 4000


def replace_input_text(file_name: str) -> str:
    """The question asked before unsaved Input is replaced."""
    return f"Replace the current input with {file_name}?"


def opening_text(file_name: str) -> str:
    """Busy text shown once Open has taken longer than the reveal delay."""
    return f"Opening {file_name}…"


def opened_text(file_name: str, size: str) -> str:
    """Status after a file loads. ``size`` is already in SI units."""
    return f"Opened {file_name} ({size})"


def opened_first_text(count: int) -> str:
    """Status after a drop of several files, of which the first was opened."""
    return f"Opened the first of {count} files."


def open_error_text(file_name: str, error: OpenTextError) -> str:
    """Status-bar sentence for a file that was not opened."""
    match error.kind:
        case OpenErrorKind.NOT_FOUND:
            return f"Couldn't find {file_name}. It may have been moved or deleted."
        case OpenErrorKind.IS_DIRECTORY:
            return f"{file_name} is a folder. Choose a file to open."
        case OpenErrorKind.PERMISSION:
            return f"Couldn't open {file_name}. You don't have permission to read it."
        case OpenErrorKind.NOT_UTF8:
            return (
                f"{file_name} isn't UTF-8 text (line {error.line}), "
                "so Galley can't open it."
            )
        case OpenErrorKind.UTF16:
            return f"{file_name} is UTF-16 text. Save it as UTF-8 and try again."
        case OpenErrorKind.TOO_LARGE:
            return f"{file_name} is too large to open (over {MAX_OPEN_MB} MB)."
        case OpenErrorKind.OS_ERROR:
            return f"Couldn't read {file_name}. Try again, or check the disk."


def input_window_title(file_name: str) -> str:
    """Window title for a file loaded into Input."""
    return f"{file_name} — Galley"


def input_needs_replace(current: str, opened: str | None) -> bool:
    """Return True when opening would discard unsaved Input text.

    Empty Input, and Input that still matches the file last loaded, open
    without asking.
    """
    if current == "":
        return False
    return current != opened


def load_last_open_dir() -> str:
    """Return the last folder used by Open, or Documents, or home."""
    value = problems_settings().value(LAST_DIR_KEY, "")
    if isinstance(value, str) and value:
        directory = Path(value)
        if directory.is_dir():
            return str(directory)
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    if documents and Path(documents).is_dir():
        return documents
    return str(Path.home())


def save_last_open_dir(directory: Path) -> None:
    """Remember ``directory`` as the folder Open starts in next time."""
    if not directory.is_dir():
        return
    settings = problems_settings()
    settings.setValue(LAST_DIR_KEY, str(directory))
    settings.sync()


def load_recent_files() -> list[Path]:
    """Recent files, newest first. Missing files stay on the list."""
    value = problems_settings().value(RECENT_KEY, [])
    # Ini settings read a one-item list back as a plain string.
    if isinstance(value, str):
        value = [value] if value else []
    if not isinstance(value, list):
        return []
    paths = [Path(item) for item in value if isinstance(item, str) and item]
    return paths[:MAX_RECENT]


def remember_recent_file(path: Path) -> None:
    """Put ``path`` first on the recent list and keep at most 10 entries."""
    kept = [item for item in load_recent_files() if item != path]
    _save_recent([path, *kept][:MAX_RECENT])


def clear_recent_files() -> None:
    """Empty the recent list."""
    _save_recent([])


def recent_labels(paths: list[Path]) -> list[str]:
    """Menu text for each recent file.

    Each entry is the file name. Entries that share a name add their
    parent folder's name, never the full path.
    """
    counts = Counter(path.name for path in paths)
    labels = []
    for path in paths:
        if counts[path.name] > 1 and path.parent.name:
            labels.append(f"{path.name} — {path.parent.name}")
        else:
            labels.append(path.name)
    return labels


def _save_recent(paths: list[Path]) -> None:
    settings = problems_settings()
    settings.setValue(RECENT_KEY, [str(path) for path in paths])
    settings.sync()


class FileReader(QObject):
    """Read a file on a background thread so the window keeps painting.

    ``finished`` carries the job number and an :class:`OpenedText` or an
    :class:`OpenTextError`. The window ignores jobs it has moved past.
    """

    finished = Signal(int, object)

    def read(self, job: int, path: Path) -> None:
        thread = threading.Thread(
            target=self._run, args=(job, path), name="galley-open", daemon=True
        )
        thread.start()

    def _run(self, job: int, path: Path) -> None:
        result: OpenedText | OpenTextError
        try:
            result = read_text(path)
        except OpenTextError as exc:
            result = exc
        except Exception:
            # Any other failure still ends the job, so Open never hangs.
            result = OpenTextError(OpenErrorKind.OS_ERROR)
        try:
            self.finished.emit(job, result)
        except RuntimeError:
            # The window closed while the file was being read.
            return
