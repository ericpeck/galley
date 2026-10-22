"""Which files a drag carries (#86).

A dropped file opens through ``MainWindow.open_path``. A drag with no local
file in it, such as selected text, is left to the editor's own drop.
"""

from pathlib import Path

from PySide6.QtCore import QMimeData


def dropped_files(mime: QMimeData | None) -> list[Path]:
    """The local files in ``mime``, in drag order. Web links are skipped."""
    if mime is None or not mime.hasUrls():
        return []
    return [
        Path(url.toLocalFile())
        for url in mime.urls()
        if url.isLocalFile() and url.toLocalFile()
    ]
