"""Keep Qt settings from one app test out of the next."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_settings(qapp, tmp_path: Path) -> None:
    """Point QSettings at a throwaway directory for this test."""
    del qapp
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path),
    )
