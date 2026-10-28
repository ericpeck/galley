"""The Qt side of Open: the dialog settings, the document model, the reader.

Reading and decoding live in :mod:`galley.core.load`, and the words in
:mod:`galley.app.open_text`. This module remembers the last folder,
describes the file now in Input, and reads files off the UI thread.
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QStandardPaths, QThreadPool, Signal

from galley.app.problems_settings import problems_settings
from galley.core.load import LoadedDocument, LoadError, LoadErrorKind, read_document

LAST_DIR_KEY = "open/lastDir"
# Bill's wording (#135). Every extension Open maps through the table.
OPEN_FILE_FILTER = (
    "JSON, YAML, and XML "
    "(*.json *.geojson *.yaml *.yml *.xml *.xsd *.xsl *.xslt *.svg *.xhtml)"
    ";;All Files (*)"
)
OPEN_DIALOG_CAPTION = "Open"
REPLACE_BUTTON = "Replace"
OPEN_WHILE_FORMATTING_TOOLTIP = "Wait for formatting to finish"
OPENED_STATUS_MS = 4000


@dataclass(frozen=True)
class InputDocument:
    """The file last opened into a source pane.

    ``opened_text`` is the text as loaded, for the unsaved-edits check.
    ``had_bom`` and ``newline`` keep the file's original encoding details
    for a later Save.
    """

    path: Path
    opened_text: str
    had_bom: bool
    newline: str


def input_needs_replace(current: str, document: InputDocument | None) -> bool:
    """Return True when opening would discard unsaved Input text.

    Empty Input, and Input that still matches the file last loaded, open
    without asking.
    """
    if current == "":
        return False
    return document is None or current != document.opened_text


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


class FileReader(QObject):
    """Read files in a thread pool so the window keeps painting.

    Network drives and cloud placeholders can block a read. ``finished``
    carries the job number and a :class:`LoadedDocument` or a
    :class:`LoadError`. The window ignores jobs it has moved past, which is
    how a cancelled read is dropped.
    """

    finished = Signal(int, object)

    def read(self, job: int, path: Path) -> None:
        # The global pool, so closing a window never waits on a stuck read.
        QThreadPool.globalInstance().start(_ReadTask(self, job, path))


class _ReadTask(QRunnable):
    def __init__(self, reader: FileReader, job: int, path: Path) -> None:
        super().__init__()
        self._reader = reader
        self._job = job
        self._path = path

    def run(self) -> None:
        result: LoadedDocument | LoadError
        try:
            result = read_document(self._path)
        except LoadError as exc:
            result = exc
        except Exception:
            # Any other failure still ends the job, so Open never hangs.
            result = LoadError(LoadErrorKind.OS_ERROR)
        try:
            self._reader.finished.emit(self._job, result)
        except RuntimeError:
            # The window closed while the file was being read.
            return
