"""Which files a drag carries (#86).

A dropped file opens through ``MainWindow.open_path``. A drag with no local
file or folder in it, such as selected text, is left to the editor's own drop.
"""

from pathlib import Path

from PySide6.QtCore import QMimeData


def dropped_paths(mime: QMimeData | None) -> list[Path]:
    """The local files and folders in ``mime``, in drag order.

    Web links are skipped.
    """
    if mime is None or not mime.hasUrls():
        return []
    return [
        Path(url.toLocalFile())
        for url in mime.urls()
        if url.isLocalFile() and url.toLocalFile()
    ]


def dropped_files(mime: QMimeData | None) -> list[Path]:
    """The dropped paths that Open can take. Folders are left out."""
    return [path for path in dropped_paths(mime) if not path.is_dir()]
