"""Drag a file onto the window to open it (#86).

Each drag is sent to the widget under the pointer, as the platform would.
Qt passes a refused drag up to the window, and the window opens the file
through ``open_path``. A test-built drag event does not own its
``QMimeData``, so each test keeps the mime object alive until the drag ends.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

from galley.app.editor import CodeEditor
from galley.app.file_drop import dropped_files
from galley.app.main import create_main_window
from galley.app.pane import PaneRole, PaneWidget
from galley.app.theme import composite, contrast_ratio

_SAMPLE = '{"a":1,"b":[true,null]}'


def test_dropped_files_keeps_local_files_in_order(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.yaml"
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(first)),
            QUrl("https://example.com/remote.json"),
            QUrl.fromLocalFile(str(second)),
        ]
    )
    assert dropped_files(mime) == [first, second]
    assert dropped_files(None) == []
    text = QMimeData()
    text.setText("plain text")
    assert dropped_files(text) == []


def test_file_dropped_on_input_opens_through_open_path(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    opened = _spy_open_path(monkeypatch, window)
    pane = _input_pane(window)
    viewport = pane.editor.viewport()
    # Finder and Explorer also put the path in as text. It must not be typed in.
    mime = _files(path)
    mime.setText(str(path))

    assert _drag(viewport, "enter", mime).isAccepted()
    assert pane.drop_highlighted()
    assert not _output_pane(window).drop_highlighted()
    assert _drag(viewport, "move", mime).isAccepted()
    assert pane.drop_highlighted()
    assert _drag(viewport, "drop", mime).isAccepted()

    assert not pane.drop_highlighted()
    assert opened == [(path, 1)]
    _wait_loaded(qtbot, window, _SAMPLE)
    assert _status(window).text() == "Opened sample.json (23 B)"
    assert window.windowTitle() == "sample.json — Galley"


@pytest.mark.parametrize("where", ["output", "toolbar"])
def test_file_dropped_elsewhere_in_the_window_opens_into_input(
    qtbot, tmp_path, monkeypatch, where
) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    opened = _spy_open_path(monkeypatch, window)
    pane = _input_pane(window)
    target = (
        _output_pane(window).editor.viewport()
        if where == "output"
        else window.findChild(QWidget, "mainToolbar")
    )
    assert isinstance(target, QWidget)
    mime = _files(path)

    assert _drag(target, "enter", mime).isAccepted()
    # The outline shows only while the file is over Input itself.
    assert not pane.drop_highlighted()
    assert not _output_pane(window).drop_highlighted()
    assert _drag(target, "drop", mime).isAccepted()

    assert opened == [(path, 1)]
    _wait_loaded(qtbot, window, _SAMPLE)
    assert _output(window).toPlainText() == ""


def test_outline_follows_the_pointer_and_clears_on_leave(qtbot, tmp_path) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    pane = _input_pane(window)
    mime = _files(path)
    output = _output_pane(window).editor.viewport()

    assert _drag(output, "enter", mime).isAccepted()
    assert not pane.drop_highlighted()
    assert _drag(pane.editor.viewport(), "move", mime).isAccepted()
    assert pane.drop_highlighted()
    assert _drag(output, "move", mime).isAccepted()
    assert not pane.drop_highlighted()
    assert _drag(pane.editor.viewport(), "move", mime).isAccepted()
    assert pane.drop_highlighted()

    _drag(pane.editor.viewport(), "leave", mime)
    assert not pane.drop_highlighted()
    assert _input(window).toPlainText() == ""


def test_several_files_open_the_first_and_say_so(qtbot, tmp_path, monkeypatch) -> None:
    paths = [tmp_path / name for name in ("one.json", "two.json", "three.yaml")]
    for index, path in enumerate(paths):
        _write(path, f'{{"file": {index}}}')
    window = _show(qtbot)
    opened = _spy_open_path(monkeypatch, window)
    mime = _files(*paths)
    viewport = _input(window).viewport()

    assert _drag(viewport, "enter", mime).isAccepted()
    assert _drag(viewport, "drop", mime).isAccepted()

    assert opened == [(paths[0], 3)]
    _wait_loaded(qtbot, window, '{"file": 0}')
    status = _status(window)
    assert status.text() == "Opened the first of 3 files."
    assert status.toolTip() == str(paths[0])
    assert window.windowTitle() == "one.json — Galley"

    # The next single Open goes back to the usual status.
    _write(paths[1], _SAMPLE)
    window.open_path(paths[1])
    _wait_loaded(qtbot, window, _SAMPLE)
    assert status.text() == "Opened two.json (23 B)"


def test_dropped_folder_says_to_choose_a_file(qtbot, tmp_path) -> None:
    folder = tmp_path / "configs"
    folder.mkdir()
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("keep")
    mime = _files(folder)
    viewport = editor.viewport()

    assert _drag(viewport, "enter", mime).isAccepted()
    assert _drag(viewport, "drop", mime).isAccepted()
    # Typed text asks before it is replaced, folder or not.
    box = _require(window, "replaceInput")
    _click(box, "Replace")

    qtbot.waitUntil(lambda: window._loading_path is None)
    qtbot.waitUntil(
        lambda: _status(window).text() == "configs is a folder. Choose a file to open."
    )
    assert editor.toPlainText() == "keep"


def test_drop_over_typed_input_asks_before_replacing(qtbot, tmp_path) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("typed")
    mime = _files(path)
    viewport = editor.viewport()

    _drag(viewport, "enter", mime)
    _drag(viewport, "drop", mime)
    box = _require(window, "replaceInput")
    assert box.text() == "Replace the current input with sample.json?"
    _click(box, "Cancel")
    qtbot.waitUntil(lambda: _message(window, "replaceInput") is None)
    assert editor.toPlainText() == "typed"
    assert window._loading_path is None

    _drag(viewport, "enter", mime)
    _drag(viewport, "drop", mime)
    _click(_require(window, "replaceInput"), "Replace")
    _wait_loaded(qtbot, window, _SAMPLE)


def test_drop_while_formatting_gets_no_outline_and_says_why(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    opened = _spy_open_path(monkeypatch, window)
    editor = _input(window)
    editor.setPlainText("typed")
    window._formatting = True
    window._sync_format_button()
    pane = _input_pane(window)
    mime = _files(path)
    viewport = editor.viewport()

    # Taken, so the drop can say why, but never outlined.
    assert _drag(viewport, "enter", mime).isAccepted()
    assert not pane.drop_highlighted()
    assert _drag(viewport, "move", mime).isAccepted()
    assert not pane.drop_highlighted()
    assert _drag(viewport, "drop", mime).isAccepted()

    assert _status(window).text() == "Can't open a file while formatting."
    assert opened == []
    assert window._loading_path is None
    assert _message(window, "replaceInput") is None
    assert editor.toPlainText() == "typed"
    window._formatting = False
    window._sync_format_button()


def test_drop_while_a_file_loads_is_refused(qtbot, tmp_path, monkeypatch) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    pane = _input_pane(window)
    loading = tmp_path / "loading.json"
    monkeypatch.setattr(window, "_loading_path", loading)
    assert not window.can_open()
    mime = _files(path)
    viewport = pane.editor.viewport()

    assert not _drag(viewport, "enter", mime).isAccepted()
    assert not pane.drop_highlighted()
    assert window._loading_path == loading
    assert pane.editor.toPlainText() == ""


def test_text_dropped_on_input_is_inserted_as_text(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    opened = _spy_open_path(monkeypatch, window)
    pane = _input_pane(window)
    mime = QMimeData()
    mime.setText('{"dropped": true}')
    viewport = pane.editor.viewport()

    assert _drag(viewport, "enter", mime).isAccepted()
    assert not pane.drop_highlighted()
    assert _drag(viewport, "move", mime).isAccepted()
    assert _drag(viewport, "drop", mime).isAccepted()

    assert pane.editor.toPlainText() == '{"dropped": true}'
    assert opened == []
    assert window._loading_path is None


def test_drags_without_a_local_file_are_refused_by_the_window(
    qtbot, monkeypatch
) -> None:
    window = _show(qtbot)
    opened = _spy_open_path(monkeypatch, window)
    pane = _input_pane(window)
    text = QMimeData()
    text.setText("plain text")
    web = QMimeData()
    web.setUrls([QUrl("https://example.com/remote.json")])
    toolbar = window.findChild(QWidget, "mainToolbar")
    assert isinstance(toolbar, QWidget)

    # Away from the editors, the window takes only local files.
    for mime in (text, web):
        assert not _drag(toolbar, "enter", mime).isAccepted()
        assert not pane.drop_highlighted()
    # Over an editor, a web link is the editor's own drop, never an Open.
    viewport = pane.editor.viewport()
    _drag(viewport, "enter", web)
    assert not pane.drop_highlighted()
    _drag(viewport, "leave", web)
    assert opened == []
    assert window._loading_path is None


def test_drop_outline_reaches_three_to_one_and_thickens(qtbot, scheme_palette) -> None:
    pane = PaneWidget(PaneRole.INPUT)
    qtbot.addWidget(pane)
    pane.resize(480, 520)
    pane.show()
    qtbot.waitExposed(pane)
    window = scheme_palette.color(QPalette.ColorRole.Window)
    base = composite(scheme_palette.color(QPalette.ColorRole.Base), window)

    edge, inner = _edge_pixels(pane)
    pane.set_drop_highlight(True)
    ring, ring_inner = _edge_pixels(pane)
    pane.set_drop_highlight(False)
    assert _edge_pixels(pane) == (edge, inner)

    # Not color alone: the 1 px edge becomes a 2 px ring.
    assert ring != edge
    assert inner != edge
    assert ring_inner == ring
    assert contrast_ratio(ring, window) >= 3
    assert contrast_ratio(ring, base) >= 3

    # The same ring as keyboard focus, so no new color is added.
    pane.set_keyboard_focus(True)
    assert _edge_pixels(pane) == (ring, ring_inner)


def _edge_pixels(pane: PaneWidget) -> tuple[QColor, QColor]:
    """The pane's top row and the one under it, above the tab title."""
    image = pane.grab().toImage()
    ratio = image.devicePixelRatio()
    x = image.width() - round(4 * ratio)
    return image.pixelColor(x, 0), image.pixelColor(x, round(ratio))


def _drag(widget: QWidget, kind: str, mime: QMimeData) -> QDropEvent | QDragLeaveEvent:
    """Send one drag step to ``widget`` at its center, through Qt's routing."""
    point = widget.rect().center()
    copy = Qt.DropAction.CopyAction
    left = Qt.MouseButton.LeftButton
    none = Qt.KeyboardModifier.NoModifier
    if kind == "enter":
        event = QDragEnterEvent(point, copy, mime, left, none)
    elif kind == "move":
        event = QDragMoveEvent(point, copy, mime, left, none)
    elif kind == "drop":
        event = QDropEvent(QPoint(point).toPointF(), copy, mime, left, none)
    else:
        event = QDragLeaveEvent()
    QApplication.sendEvent(widget, event)
    return event


def _files(*paths: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def _spy_open_path(monkeypatch, window) -> list[tuple[Path, int]]:
    calls: list[tuple[Path, int]] = []
    real = window.open_path

    def _spy(path: Path, *, of_files: int = 1) -> None:
        calls.append((path, of_files))
        real(path, of_files=of_files)

    monkeypatch.setattr(window, "open_path", _spy)
    return calls


def _click(box: QMessageBox, text: str) -> None:
    for button in box.buttons():
        if button.text() == text:
            button.click()
            return
    raise AssertionError(f"no {text} button")


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _wait_loaded(qtbot, window, text: str) -> None:
    qtbot.waitUntil(
        lambda: window._loading_path is None and _input(window).toPlainText() == text
    )


def _input(window) -> CodeEditor:
    return _input_pane(window).editor


def _output(window) -> CodeEditor:
    return _output_pane(window).editor


def _input_pane(window) -> PaneWidget:
    pane = window.findChild(PaneWidget, "inputPane")
    assert isinstance(pane, PaneWidget)
    return pane


def _output_pane(window) -> PaneWidget:
    pane = window.findChild(PaneWidget, "outputPane")
    assert isinstance(pane, PaneWidget)
    return pane


def _status(window) -> QLabel:
    status = window.findChild(QLabel, "statusMessage")
    assert isinstance(status, QLabel)
    return status


def _message(window, name: str) -> QMessageBox | None:
    found = [
        box
        for box in window.findChildren(QMessageBox)
        if box.objectName() == name and box.isVisible()
    ]
    return found[0] if found else None


def _require(window, name: str) -> QMessageBox:
    box = _message(window, name)
    assert box is not None
    return box


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))
