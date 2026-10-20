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


@pytest.fixture(autouse=True)
def cancelled_file_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests off the platform file dialog. Open tests replace this."""

    def _cancel(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return "", ""

    monkeypatch.setattr(
        "galley.app.main_window.QFileDialog.getOpenFileName",
        _cancel,
    )
