"""About Galley dialog, menu role, and license-folder resolution."""

import ast
import re
import sys
from pathlib import Path

import PySide6
from PySide6.QtCore import Qt, QTimer, qVersion
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMenu, QMessageBox

import galley
import galley.app.about as about
from galley.app.about import (
    COPYRIGHT_NOTICE,
    SOURCE_CODE_URL,
    about_text,
    license_texts_url,
    licenses_directory,
)
from galley.app.main import create_main_window
from galley.app.main_window import NOT_YET_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[2]
ABOUT_SOURCE = REPO_ROOT / "src" / "galley" / "app" / "about.py"
REPO_LICENSES = REPO_ROOT / "LICENSES"
_HREF = re.compile(r'href="([^"]+)"')


def _text_label(dialog: QMessageBox) -> QLabel:
    for label in dialog.findChildren(QLabel):
        if "Galley" in label.text():
            return label
    raise AssertionError("about text")


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _about_action(window) -> QAction:
    action = window.findChild(QAction, "aboutGalley")
    assert isinstance(action, QAction)
    return action


def _run_about(qtbot, window, inspect) -> None:
    errors: list[BaseException] = []

    def _inspect() -> None:
        dialog = window.findChild(QMessageBox, "aboutDialog")
        try:
            assert isinstance(dialog, QMessageBox)
            inspect(dialog)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if isinstance(dialog, QMessageBox) and dialog.isVisible():
                dialog.done(0)

    QTimer.singleShot(0, _inspect)
    _about_action(window).trigger()
    if errors:
        raise errors[0]


def test_about_action_uses_about_role_on_help(qtbot) -> None:
    window = _show(qtbot)
    about = _about_action(window)
    help_menu = window.findChild(QMenu, "menuHelp")
    galley_menu = window.findChild(QMenu, "menuGalley")
    assert isinstance(help_menu, QMenu)
    assert isinstance(galley_menu, QMenu)
    assert about.text() == "About Galley"
    assert about.menuRole() == QAction.MenuRole.AboutRole
    help_names = [action.objectName() for action in help_menu.actions()]
    galley_names = [action.objectName() for action in galley_menu.actions()]
    assert help_names[-1] == "aboutGalley"
    assert "aboutGalley" not in galley_names


def test_about_dialog_shows_runtime_versions(qtbot) -> None:
    window = _show(qtbot)
    window.statusBar().clearMessage()
    seen: dict[str, object] = {}

    def inspect(dialog: QMessageBox) -> None:
        text = dialog.text()
        seen["text"] = text
        assert dialog.windowTitle() == "About Galley"
        assert dialog.testOption(QMessageBox.Option.DontUseNativeDialog)
        assert isinstance(dialog, QMessageBox)
        assert "<b>Galley</b>" in text
        assert galley.__version__
        assert galley.__version__ in text
        assert qVersion()
        assert qVersion() in text
        assert PySide6.__version__
        assert PySide6.__version__ in text
        assert "Format and validate JSON and YAML." in text
        assert COPYRIGHT_NOTICE in text
        assert "Built with Qt" in text
        assert "PySide6" in text
        assert "used under the LGPLv3" in text
        assert dialog.textFormat() == Qt.TextFormat.RichText
        flags = dialog.textInteractionFlags()
        browser = Qt.TextInteractionFlag.TextBrowserInteraction
        assert (flags & browser) == browser
        assert dialog.standardButtons() == QMessageBox.StandardButton.Ok
        assert len(dialog.buttons()) == 1
        folder = licenses_directory()
        assert folder is not None and folder.is_dir()
        hrefs = _HREF.findall(text)
        assert any(href.startswith("file:") and "LICENSES" in href for href in hrefs)
        assert SOURCE_CODE_URL in hrefs
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)
        # macOS animates the escape button before the dialog closes.
        qtbot.waitUntil(lambda: not dialog.isVisible(), timeout=1000)

    _run_about(qtbot, window, inspect)
    assert "text" in seen
    assert window.statusBar().currentMessage() != NOT_YET_MESSAGE
    assert "Not yet" not in window.statusBar().currentMessage()
    assert COPYRIGHT_NOTICE == "© 2026 [copyright holder TBD]"


def test_missing_licenses_links_to_github(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(galley, "__version__", "9.9.9")
    monkeypatch.setattr("galley.app.about.licenses_directory", lambda: None)
    expected = "https://github.com/ericpeck/galley/tree/v9.9.9/LICENSES"
    window = _show(qtbot)

    def inspect(dialog: QMessageBox) -> None:
        text = dialog.text()
        hrefs = _HREF.findall(text)
        assert ">License texts</a>" in text
        assert expected in hrefs
        assert "tree/main/LICENSES" not in text
        assert not any(href.startswith("file:") for href in hrefs)
        assert SOURCE_CODE_URL in hrefs
        assert "used under the LGPLv3" in text
        assert _text_label(dialog).text() == text

    _run_about(qtbot, window, inspect)
    plain = about_text()
    assert f'href="{expected}">License texts</a>' in plain
    assert license_texts_url() == expected


def test_license_link_opens_the_folder(qtbot, monkeypatch) -> None:
    opened: list[object] = []

    def capture(url) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("galley.app.about.QDesktopServices.openUrl", capture)
    window = _show(qtbot)

    def inspect(dialog: QMessageBox) -> None:
        label = _text_label(dialog)
        hrefs = _HREF.findall(label.text())
        license_href = next(href for href in hrefs if href.startswith("file:"))
        label.linkActivated.emit(license_href)
        assert opened
        local = Path(opened[0].toLocalFile())
        assert local.is_dir()
        assert local.name == "LICENSES"

    _run_about(qtbot, window, inspect)


def test_versions_are_not_hardcoded_literals() -> None:
    tree = ast.parse(ABOUT_SOURCE.read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert galley.__version__ not in literals
    assert PySide6.__version__ not in literals
    assert qVersion() not in literals


def test_licenses_directory_is_the_source_checkout() -> None:
    found = licenses_directory()
    assert found == REPO_LICENSES.resolve()
    assert found.is_dir()


def test_frozen_meipass_licenses_win(monkeypatch, tmp_path: Path) -> None:
    bundle = tmp_path / "LICENSES"
    bundle.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert licenses_directory() == bundle.resolve()


def test_frozen_app_resources_licenses(monkeypatch, tmp_path: Path) -> None:
    frameworks = tmp_path / "Galley.app" / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True)
    resources = tmp_path / "Galley.app" / "Contents" / "Resources" / "LICENSES"
    resources.mkdir(parents=True)
    executable = tmp_path / "Galley.app" / "Contents" / "MacOS" / "Galley"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert licenses_directory() == resources.resolve()


def test_frozen_licenses_next_to_executable(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "Galley.exe"
    executable.write_text("", encoding="utf-8")
    folder = tmp_path / "LICENSES"
    folder.mkdir()
    empty = tmp_path / "empty-meipass"
    empty.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(empty), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert licenses_directory() == folder.resolve()


def test_missing_licenses_directory(monkeypatch, tmp_path: Path) -> None:
    origin = tmp_path / "pkg" / "galley" / "app" / "about.py"
    origin.parent.mkdir(parents=True)
    monkeypatch.setattr(about, "__file__", str(origin))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert licenses_directory() is None
