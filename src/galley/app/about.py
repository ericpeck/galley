"""About Galley dialog.

Versions come from the running interpreter. The copyright line stays in
``COPYRIGHT_NOTICE`` until Eric chooses the wording.
"""

import html
import sys
from pathlib import Path

import PySide6
from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

import galley

# TODO: Eric has not chosen the copyright wording. Replace this placeholder
# when he supplies it. Do not invent a holder name.
COPYRIGHT_NOTICE = "© 2026 [copyright holder TBD]"

SOURCE_CODE_URL = "https://github.com/ericpeck/galley"
# Used when the LICENSES folder is not on disk. A frozen app still fails
# --self-check in that case; this link is only for the About dialog.
LICENSE_TEXTS_URL = "https://github.com/ericpeck/galley/tree/main/LICENSES"
TAGLINE = "Format and validate JSON and YAML."
_LICENSE_NAME_MARKERS = ("license", "licence", "copying", "lgpl", "gpl")

# src/galley/app/about.py is four parents below the repository root.
_SOURCE_PARENTS = 4
_LINK_SCHEMES = frozenset({"file", "http", "https"})


def licenses_directory() -> Path | None:
    """Return the LICENSES directory, or None when it is not on disk.

    A source checkout is the repository ``LICENSES`` folder. A PyInstaller
    bundle is ``sys._MEIPASS / LICENSES``, the application bundle's
    ``Contents/Resources/LICENSES``, or the folder next to the executable.
    """
    groups = (
        (_bundle_candidates(), _source_candidates())
        if getattr(sys, "frozen", False)
        else (_source_candidates(), _bundle_candidates())
    )
    for group in groups:
        found = _first_directory(group)
        if found is not None:
            return found
    return None


def about_text() -> str:
    """Rich text for the About dialog, read from the running app."""
    app_version = galley.__version__
    qt_version = qVersion()
    pyside_version = PySide6.__version__
    license_part = _license_markup(licenses_directory())
    source_href = html.escape(SOURCE_CODE_URL, quote=True)
    return (
        "<p><b>Galley</b></p>"
        f"<p>Version {html.escape(app_version)}</p>"
        f"<p>{html.escape(TAGLINE)}</p>"
        f"<p>{html.escape(COPYRIGHT_NOTICE)}</p>"
        "<p>Built with Qt "
        f"{html.escape(qt_version)} and PySide6 "
        f"{html.escape(pyside_version)}, used under the LGPLv3.</p>"
        f'<p>{license_part} · <a href="{source_href}">Source code</a></p>'
    )


def create_about_dialog(parent: QWidget | None = None) -> QMessageBox:
    """Build the About dialog. The caller runs it."""
    dialog = QMessageBox(parent)
    dialog.setObjectName("aboutDialog")
    # QMessageBox.setWindowTitle ignores the title on macOS. QWidget stores it.
    QWidget.setWindowTitle(dialog, "About Galley")
    dialog.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    dialog.setIcon(QMessageBox.Icon.NoIcon)
    dialog.setTextFormat(Qt.TextFormat.RichText)
    dialog.setText(about_text())
    dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
    dialog.setDefaultButton(QMessageBox.StandardButton.Ok)
    dialog.setEscapeButton(QMessageBox.StandardButton.Ok)
    _connect_links(dialog)
    return dialog


def show_about_dialog(parent: QWidget | None = None) -> None:
    """Show About Galley and wait until it closes."""
    dialog = create_about_dialog(parent)
    dialog.exec()
    dialog.deleteLater()


def open_about_url(url: str) -> None:
    """Open a license folder or the source repository."""
    target = QUrl(url)
    if target.scheme().lower() not in _LINK_SCHEMES:
        return
    QDesktopServices.openUrl(target)


def licenses_self_check() -> str | None:
    """Return None when a frozen app has its bundled license files.

    A development checkout skips this. The About dialog can fall back to
    ``LICENSE_TEXTS_URL``; the frozen check still fails when the folder
    inside the app is missing or has no license files.
    """
    if not getattr(sys, "frozen", False):
        return None
    tried = ", ".join(str(path) for path in _bundle_candidates())
    found = licenses_directory()
    bundled = {path.resolve() for path in _bundle_candidates() if path.is_dir()}
    if found is None or found.resolve() not in bundled:
        if found is None:
            return f"LICENSES folder was not found (tried {tried})"
        return f"LICENSES folder is outside the app ({found}; tried {tried})"
    if not license_file_names(found):
        return f"LICENSES folder has no license files ({found})"
    return None


def license_file_names(folder: Path) -> list[str]:
    """File names in ``folder`` that carry a license text."""
    names: list[str] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        folded = path.name.casefold()
        if any(marker in folded for marker in _LICENSE_NAME_MARKERS):
            names.append(path.name)
    return names


def _license_markup(licenses: Path | None) -> str:
    label = "License texts"
    if licenses is None:
        href = html.escape(LICENSE_TEXTS_URL, quote=True)
    else:
        href = html.escape(_file_url(licenses), quote=True)
    return f'<a href="{href}">{label}</a>'


def _file_url(path: Path) -> str:
    return QUrl.fromLocalFile(str(path)).toString(
        QUrl.ComponentFormattingOption.FullyEncoded
    )


def _connect_links(dialog: QMessageBox) -> None:
    flags = Qt.TextInteractionFlag.TextBrowserInteraction
    for label in dialog.findChildren(QLabel):
        label.setTextInteractionFlags(flags)
        label.linkActivated.connect(open_about_url)


def _source_candidates() -> list[Path]:
    origin = Path(__file__).resolve()
    return [parent / "LICENSES" for parent in list(origin.parents)[:_SOURCE_PARENTS]]


def _bundle_candidates() -> list[Path]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        root = Path(meipass)
        candidates.append(root / "LICENSES")
        # Bundle data files live in Contents/Resources. sys._MEIPASS may
        # point at Contents/Frameworks instead.
        candidates.append(root.parent / "Resources" / "LICENSES")
    executable = Path(sys.executable).resolve()
    candidates.append(executable.parent / "LICENSES")
    candidates.append(executable.parent.parent / "Resources" / "LICENSES")
    return candidates


def _first_directory(candidates: list[Path]) -> Path | None:
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            return resolved
    return None
