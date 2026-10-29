"""Compare slice 3e (#65): Open Right Document…, Link Scrolling and Format.

- An empty Right shows "Paste or open the JSON or YAML to compare with."
  with Open Right Document…, which loads Right.
- File › Open…, Open Recent and a drop outside the panes ask Left, Right
  (the default) or Cancel. A drop on Left or Right loads that side. Each
  file is read once. XML and HTML are refused with the worker's "XML can't
  be compared." Left replaces Input as one undo step, so Undo after
  leaving Compare brings the old Input back.
- Link Scrolling, on by default, keeps matching lines level on both sides.
- Format reformats both sides, one Format job each, Left first. It ignores
  Sort keys and uses the indent size from before Compare, with YAML always
  in spaces. Both results are held; if either side has errors or changed
  meanwhile, neither changes.

Compares and formats run in-process (``inline_compare``, ``inline_format``)
behind the same ``ParseSession`` signals, except the last test, which uses
the spawn worker. File dialogs are replaced; nothing native opens.
"""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)
from tests.app.compare_inline import inline_compare as inline_compare
from tests.app.compare_inline import inline_format as inline_format

from galley.app.changes_pane import ADD_SECOND_DOCUMENT, ChangesPane
from galley.app.compare_mode import (
    FIX_ERRORS_TO_FORMAT,
    FORMATTED_BOTH,
    FORMATTED_LEFT,
    RIGHT_PLACEHOLDER,
    open_side_text,
)
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import CANCELLED_MESSAGE, FORMATTING_MESSAGE
from galley.app.recent_files import load_recent_files
from galley.core.compare_report import ChangeRow

# Fake data only.
_LEFT = '{\n  "orders": [{"id": "A-1001", "total": 40}],\n  "region": "north"\n}\n'
_RIGHT = '{\n  "orders": [{"id": "A-1001", "total": 45}],\n  "region": "north"\n}\n'
_SAMPLE = '{"a":1,"b":[true,null]}'
_YAML = "name: sample\nitems:\n- 1\n- 2\n"
_XML = "<root><item>1</item></root>\n"
_HTML = "<!doctype html><p>sample</p>\n"
_BROKEN = '{\n  "orders": [\n'
_WAIT_MS = 5_000
_SPAWN_TIMEOUT_MS = 30_000


# --- Helpers ---------------------------------------------------------------


def _show(qtbot, width: int = 1400):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(width, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _editor(window, name: str) -> CodeEditor:
    editor = window.findChild(CodeEditor, name)
    assert isinstance(editor, CodeEditor), name
    return editor


def _left(window) -> CodeEditor:
    return _editor(window, "inputEditor")


def _right(window) -> CodeEditor:
    return _editor(window, "rightEditor")


def _changes(window) -> ChangesPane:
    pane = window.findChild(ChangesPane, "changesPage")
    assert isinstance(pane, ChangesPane)
    return pane


def _type_input(window, text: str) -> None:
    editor = _left(window)
    editor.setFocus()
    editor.setPlainText("")
    editor.insertPlainText(text)


def _paste_right(window, text: str) -> None:
    QApplication.clipboard().setText(text)
    _action(window, "Paste").trigger()


def _enter(qtbot, window, left: str = _LEFT) -> None:
    _type_input(window, left)
    _action(window, "Compare").trigger()
    assert _action(window, "Compare").isChecked()


def _compare(qtbot, window, left: str = _LEFT, right: str = _RIGHT) -> None:
    _enter(qtbot, window, left)
    _paste_right(window, right)
    _wait_idle(qtbot, window)


def _wait_idle(qtbot, window) -> None:
    qtbot.waitUntil(lambda: not window._compare.is_running(), timeout=_WAIT_MS)


def _status(window) -> str:
    return window._status.state_text()


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _choose_file(monkeypatch, path: Path | None) -> list[int]:
    """Make the Open dialog answer ``path`` (None cancels). Returns the calls."""
    calls: list[int] = []

    def _fake(*_args, **_kwargs):
        calls.append(1)
        return ("" if path is None else str(path), "")

    monkeypatch.setattr("galley.app.main_window.QFileDialog.getOpenFileName", _fake)
    return calls


def _box(window, name: str) -> QMessageBox | None:
    box = window.findChild(QMessageBox, name)
    return box if isinstance(box, QMessageBox) and box.isVisible() else None


def _wait_box(qtbot, window, name: str) -> QMessageBox:
    qtbot.waitUntil(lambda: _box(window, name) is not None, timeout=_WAIT_MS)
    box = _box(window, name)
    assert box is not None
    return box


def _side_sheet(qtbot, window) -> QMessageBox:
    return _wait_box(qtbot, window, "compareOpenSide")


def _box_button(box: QMessageBox, text: str) -> QPushButton:
    for button in box.buttons():
        if isinstance(button, QPushButton) and button.text() == text:
            return button
    raise AssertionError(text)


def _box_texts(box: QMessageBox) -> list[str]:
    buttons = box.findChildren(QPushButton)
    # The order the sheet shows them in, left to right.
    ordered = sorted(
        (button for button in buttons if button.isVisible()),
        key=lambda button: button.mapTo(box, QPoint(0, 0)).x(),
    )
    return [button.text() for button in ordered]


def _placeholder(window) -> QWidget:
    overlay = window.findChild(QWidget, "emptyRight")
    assert isinstance(overlay, QWidget)
    return overlay


def _wait_right(qtbot, window, text: str) -> None:
    qtbot.waitUntil(lambda: _right(window).toPlainText() == text, timeout=_WAIT_MS)


def _wait_left(qtbot, window, text: str) -> None:
    qtbot.waitUntil(lambda: _left(window).toPlainText() == text, timeout=_WAIT_MS)


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


def _drop(widget: QWidget, mime: QMimeData) -> bool:
    assert _drag(widget, "enter", mime).isAccepted()
    assert _drag(widget, "move", mime).isAccepted()
    return _drag(widget, "drop", mime).isAccepted()


def _toolbar(window) -> QWidget:
    toolbar = window.findChild(QWidget, "mainToolbar")
    assert isinstance(toolbar, QWidget)
    return toolbar


def _indent_picker(window) -> QComboBox:
    picker = window.findChild(QComboBox, "indentSize")
    assert isinstance(picker, QComboBox)
    return picker


def _set_picker(window, name: str, label: str) -> None:
    combo = window.findChild(QComboBox, name)
    assert isinstance(combo, QComboBox)
    combo.setCurrentText(label)


# --- Empty Right -----------------------------------------------------------


def test_empty_right_shows_the_placeholder_and_open_right_document(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _enter(qtbot, window)
    overlay = _placeholder(window)
    assert overlay.isVisible()
    heading = overlay.findChild(QLabel, "rightEmptyTitle")
    assert isinstance(heading, QLabel)
    assert heading.text() == RIGHT_PLACEHOLDER
    assert RIGHT_PLACEHOLDER == "Paste or open the JSON or YAML to compare with."
    buttons = overlay.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["Open Right Document…"]
    assert buttons[0].objectName() == "openRightButton"
    assert buttons[0].isEnabled()
    # The Changes pane keeps its own line.
    assert _changes(window).message() == ADD_SECOND_DOCUMENT
    _paste_right(window, _RIGHT)
    assert not overlay.isVisible()
    _wait_idle(qtbot, window)
    _action(window, "Compare").trigger()
    assert not overlay.isVisible()
    _action(window, "Compare").trigger()
    # Right kept its text, so no placeholder.
    assert not overlay.isVisible()


def test_the_placeholder_button_is_open_right_document(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    calls = _choose_file(monkeypatch, path)
    _enter(qtbot, window)
    button = window.findChild(QPushButton, "openRightButton")
    assert isinstance(button, QPushButton)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    _wait_right(qtbot, window, _SAMPLE)
    assert calls == [1]
    assert not _placeholder(window).isVisible()


# --- Open Right Document… --------------------------------------------------


def test_open_right_document_loads_right_and_compares(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "other.yaml", _YAML)
    window = _show(qtbot)
    _enter(qtbot, window)
    left = _left(window)
    steps = left.document().availableUndoSteps()
    _choose_file(monkeypatch, path)
    _action(window, "Open Right Document…").trigger()
    _wait_right(qtbot, window, _YAML)
    combo = window.findChild(QComboBox, "outputFormat")
    assert isinstance(combo, QComboBox)
    assert combo.currentText() == "YAML"
    assert inline_compare.sent
    assert inline_compare.sent[-1].right_text == _YAML
    assert inline_compare.sent[-1].right_format == "YAML"
    _wait_idle(qtbot, window)
    assert _status(window) == "Opened other.yaml (28 B)"
    assert left.toPlainText() == _LEFT
    assert left.document().availableUndoSteps() == steps
    # The Output editor behind Right is untouched.
    assert _editor(window, "outputEditor").toPlainText() == ""
    assert str(path.absolute()) in load_recent_files()


def test_canceling_the_dialog_changes_nothing(
    qtbot, inline_compare, monkeypatch
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _choose_file(monkeypatch, None)
    sent = len(inline_compare.sent)
    _action(window, "Open Right Document…").trigger()
    assert _right(window).toPlainText() == _RIGHT
    assert len(inline_compare.sent) == sent


@pytest.mark.parametrize(
    ("name", "text", "label"),
    [("page.xml", _XML, "XML"), ("page.html", _HTML, "HTML")],
)
def test_open_right_document_refuses_xml_and_html(
    qtbot, inline_compare, monkeypatch, tmp_path, name, text, label
) -> None:
    path = _write(tmp_path / name, text)
    window = _show(qtbot)
    _compare(qtbot, window)
    _choose_file(monkeypatch, path)
    sent = len(inline_compare.sent)
    _action(window, "Open Right Document…").trigger()
    qtbot.waitUntil(
        lambda: _status(window) == f"{label} can't be compared.", timeout=_WAIT_MS
    )
    assert _right(window).toPlainText() == _RIGHT
    assert len(inline_compare.sent) == sent


def test_xml_text_in_a_txt_file_is_refused_too(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    # No extension in the table, so detect_format reads the text.
    path = _write(tmp_path / "notes.txt", _XML)
    window = _show(qtbot)
    _compare(qtbot, window)
    _choose_file(monkeypatch, path)
    _action(window, "Open Right Document…").trigger()
    qtbot.waitUntil(
        lambda: _status(window) == "XML can't be compared.", timeout=_WAIT_MS
    )
    assert _right(window).toPlainText() == _RIGHT


def test_an_unreadable_file_says_why_with_the_open_wording(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _choose_file(monkeypatch, tmp_path / "gone.json")
    _action(window, "Open Right Document…").trigger()
    qtbot.waitUntil(
        lambda: (
            _status(window)
            == "Couldn't find gone.json. It may have been moved or deleted."
        ),
        timeout=_WAIT_MS,
    )
    assert _right(window).toPlainText() == _RIGHT


def test_open_right_document_outside_compare_turns_compare_on(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _type_input(window, _LEFT)
    _choose_file(monkeypatch, path)
    _action(window, "Open Right Document…").trigger()
    assert _action(window, "Compare").isChecked()
    _wait_right(qtbot, window, _SAMPLE)
    qtbot.waitUntil(lambda: _changes(window).count() > 0, timeout=_WAIT_MS)


def test_canceling_the_dialog_outside_compare_stays_out(
    qtbot, inline_compare, monkeypatch
) -> None:
    window = _show(qtbot)
    _type_input(window, _LEFT)
    status = _status(window)
    calls = _choose_file(monkeypatch, None)
    _action(window, "Open Right Document…").trigger()
    assert calls == [1]
    assert not _action(window, "Compare").isChecked()
    assert not window._compare.is_on()
    assert _left(window).toPlainText() == _LEFT
    assert not _left(window).isReadOnly()
    assert window._compare.right_editor().toPlainText() == ""
    assert _editor(window, "outputEditor").isVisible()
    assert inline_compare.sent == []
    assert _status(window) == status


def _count_reads(monkeypatch) -> list[Path]:
    import galley.app.open_file as open_file

    reads: list[Path] = []
    real = open_file.read_document

    def _spy(path: Path, *args, **kwargs):
        reads.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(open_file, "read_document", _spy)
    return reads


@pytest.mark.parametrize("side", ["Left", "Right"])
def test_each_file_is_read_exactly_once(
    qtbot, inline_compare, monkeypatch, tmp_path, side
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    reads = _count_reads(monkeypatch)
    _choose_file(monkeypatch, path)
    _action(window, "Open file…").trigger()
    _box_button(_side_sheet(qtbot, window), side).click()
    if side == "Left":
        replace = _wait_box(qtbot, window, "replaceInput")
        default = replace.defaultButton()
        assert default is not None
        default.click()
        _wait_left(qtbot, window, _SAMPLE)
    else:
        _wait_right(qtbot, window, _SAMPLE)
    _wait_idle(qtbot, window)
    assert reads == [path]


@pytest.mark.parametrize("label", ["XML", "HTML"])
def test_open_right_document_follows_compare_for_markup_input(qtbot, label) -> None:
    window = _show(qtbot)
    action = _action(window, "Open Right Document…")
    assert action.isEnabled()
    _set_picker(window, "inputFormat", label)
    assert not action.isEnabled()
    assert action.toolTip() == f"Not available for {label}"
    _set_picker(window, "inputFormat", "JSON")
    assert action.isEnabled()


def test_a_large_right_file_asks_before_comparing(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    big = json.dumps({"rows": ["x" * 90] * 23_000})
    assert len(big.encode("utf-8")) > 2_000_000
    path = _write(tmp_path / "big.json", big)
    window = _show(qtbot)
    _enter(qtbot, window)
    sent = len(inline_compare.sent)
    _choose_file(monkeypatch, path)
    _action(window, "Open Right Document…").trigger()
    box = _wait_box(qtbot, window, "compareSizeWarning")
    assert len(inline_compare.sent) == sent
    _box_button(box, "Cancel").click()


# --- The Left / Right / Cancel sheet --------------------------------------


def test_file_open_while_comparing_asks_which_side(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    open_file = _action(window, "Open file…")
    assert open_file.isEnabled()
    _choose_file(monkeypatch, path)
    open_file.trigger()
    box = _side_sheet(qtbot, window)
    assert box.text() == open_side_text("sample.json")
    assert box.text() == "Open “sample.json” on the left or the right?"
    assert box.windowModality() == Qt.WindowModality.WindowModal
    # Eric's names, in place of the brief's Open on left and Open on right.
    assert sorted(_box_texts(box)) == sorted(["Left", "Right", "Cancel"])
    default = box.defaultButton()
    assert default is not None
    assert default.text() == "Right"
    escape = box.escapeButton()
    assert isinstance(escape, QPushButton)
    assert escape.text() == "Cancel"
    _box_button(box, "Cancel").click()
    assert _right(window).toPlainText() == _RIGHT
    assert _left(window).toPlainText() == _LEFT


def test_open_on_right_loads_right(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    _choose_file(monkeypatch, path)
    _action(window, "Open file…").trigger()
    box = _side_sheet(qtbot, window)
    default = box.defaultButton()
    assert default is not None
    default.click()
    _wait_right(qtbot, window, _SAMPLE)
    assert _left(window).toPlainText() == _LEFT
    _wait_idle(qtbot, window)
    assert inline_compare.sent[-1].right_text == _SAMPLE


def test_open_on_left_replaces_input_as_one_undo_step(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    left = _left(window)
    _choose_file(monkeypatch, path)
    _action(window, "Open file…").trigger()
    _box_button(_side_sheet(qtbot, window), "Left").click()
    # Typed Input gets the same Replace prompt as File › Open.
    replace = _wait_box(qtbot, window, "replaceInput")
    default = replace.defaultButton()
    assert default is not None
    default.click()
    _wait_left(qtbot, window, _SAMPLE)
    assert left.isReadOnly()
    assert _right(window).toPlainText() == _RIGHT
    assert window.windowTitle() == "sample.json — Galley"
    _wait_idle(qtbot, window)
    assert inline_compare.sent[-1].left_text == _SAMPLE
    assert inline_compare.sent[-1].right_text == _RIGHT
    _action(window, "Compare").trigger()
    assert left.toPlainText() == _SAMPLE
    assert not left.isReadOnly()
    # One undo step brings the old Input back.
    _action(window, "Undo").trigger()
    assert left.toPlainText() == _LEFT
    # Right kept its text for the session.
    _action(window, "Compare").trigger()
    assert _right(window).toPlainText() == _RIGHT


def test_open_on_left_compares_once(
    qtbot, inline_compare, monkeypatch, tmp_path
) -> None:
    path = _write(tmp_path / "sample.yaml", _YAML)
    window = _show(qtbot)
    _compare(qtbot, window)
    _left(window).document().setModified(False)
    window._input_pane.set_document(None)
    _choose_file(monkeypatch, path)
    sent = len(inline_compare.sent)
    _action(window, "Open file…").trigger()
    _box_button(_side_sheet(qtbot, window), "Left").click()
    replace = _wait_box(qtbot, window, "replaceInput")
    default = replace.defaultButton()
    assert default is not None
    default.click()
    _wait_left(qtbot, window, _YAML)
    _wait_idle(qtbot, window)
    # The picker change and the open ask for one compare, not two.
    assert len(inline_compare.sent) == sent + 1
    assert inline_compare.sent[-1].left_format == "YAML"


def test_open_on_left_refuses_xml(qtbot, inline_compare, monkeypatch, tmp_path) -> None:
    path = _write(tmp_path / "page.xml", _XML)
    window = _show(qtbot)
    _compare(qtbot, window)
    _choose_file(monkeypatch, path)
    _action(window, "Open file…").trigger()
    _box_button(_side_sheet(qtbot, window), "Left").click()
    qtbot.waitUntil(
        lambda: _status(window) == "XML can't be compared.", timeout=_WAIT_MS
    )
    assert _box(window, "replaceInput") is None
    assert _left(window).toPlainText() == _LEFT
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    assert combo.currentText() == "JSON"


def test_open_recent_while_comparing_asks_which_side(
    qtbot, inline_compare, tmp_path
) -> None:
    from galley.app.recent_files import remember_recent_file

    path = _write(tmp_path / "again.json", _SAMPLE)
    remember_recent_file(path)
    window = _show(qtbot)
    _compare(qtbot, window)
    recent = window._recent_menu
    assert recent.menuAction().isEnabled()
    recent.rebuild()
    entries = [item for item in recent.actions() if item.data()]
    assert entries
    entries[0].trigger()
    box = _side_sheet(qtbot, window)
    assert box.text() == open_side_text("again.json")
    default = box.defaultButton()
    assert default is not None
    default.click()
    _wait_right(qtbot, window, _SAMPLE)


# --- Drag and drop ---------------------------------------------------------


def test_a_file_dropped_on_right_loads_right(qtbot, inline_compare, tmp_path) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    mime = _files(path)
    viewport = _right(window).viewport()
    assert _drag(viewport, "enter", mime).isAccepted()
    assert window._output_pane.drop_highlighted()
    assert not window._input_pane.drop_highlighted()
    assert _drag(viewport, "move", mime).isAccepted()
    assert _drag(viewport, "drop", mime).isAccepted()
    assert not window._output_pane.drop_highlighted()
    assert _box(window, "compareOpenSide") is None
    _wait_right(qtbot, window, _SAMPLE)
    assert _left(window).toPlainText() == _LEFT


def test_a_file_dropped_on_left_loads_left(qtbot, inline_compare, tmp_path) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    _left(window).document().setModified(False)
    mime = _files(path)
    viewport = _left(window).viewport()
    assert _drag(viewport, "enter", mime).isAccepted()
    assert window._input_pane.drop_highlighted()
    assert not window._output_pane.drop_highlighted()
    assert _drag(viewport, "drop", mime).isAccepted()
    assert _box(window, "compareOpenSide") is None
    replace = _wait_box(qtbot, window, "replaceInput")
    default = replace.defaultButton()
    assert default is not None
    default.click()
    _wait_left(qtbot, window, _SAMPLE)
    assert _right(window).toPlainText() == _RIGHT


def test_a_drag_leaving_clears_the_outline(qtbot, inline_compare, tmp_path) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    mime = _files(path)
    viewport = _right(window).viewport()
    assert _drag(viewport, "enter", mime).isAccepted()
    assert window._output_pane.drop_highlighted()
    _drag(viewport, "leave", mime)
    assert not window._output_pane.drop_highlighted()
    assert not window._input_pane.drop_highlighted()


def test_a_file_dropped_elsewhere_asks_which_side(
    qtbot, inline_compare, tmp_path
) -> None:
    path = _write(tmp_path / "sample.json", _SAMPLE)
    window = _show(qtbot)
    _compare(qtbot, window)
    mime = _files(path)
    assert _drop(_toolbar(window), mime)
    box = _side_sheet(qtbot, window)
    assert box.text() == open_side_text("sample.json")
    _box_button(box, "Right").click()
    _wait_right(qtbot, window, _SAMPLE)


@pytest.mark.parametrize(
    ("side", "name", "text", "label"),
    [
        ("right", "page.xml", _XML, "XML"),
        ("left", "page.html", _HTML, "HTML"),
    ],
)
def test_xml_and_html_drops_are_refused(
    qtbot, inline_compare, tmp_path, side, name, text, label
) -> None:
    path = _write(tmp_path / name, text)
    window = _show(qtbot)
    _compare(qtbot, window)
    mime = _files(path)
    editor = _right(window) if side == "right" else _left(window)
    _drop(editor.viewport(), mime)
    qtbot.waitUntil(
        lambda: _status(window) == f"{label} can't be compared.", timeout=_WAIT_MS
    )
    assert _left(window).toPlainText() == _LEFT
    assert _right(window).toPlainText() == _RIGHT
    assert _box(window, "replaceInput") is None


def test_text_drags_are_still_refused_while_comparing(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    mime = QMimeData()
    mime.setText("typed")
    assert not _drag(_right(window).viewport(), "enter", mime).isAccepted()
    _drag(_right(window).viewport(), "leave", mime)
    assert _right(window).toPlainText() == _RIGHT


# --- Link Scrolling --------------------------------------------------------


def _keyed(extra: int, count: int = 200) -> str:
    """``extra`` added keys first, then ``count`` shared ones, one per line."""
    value: dict[str, object] = {f"extra{i:02}": i for i in range(extra)}
    value.update({f"k{i:03}": i for i in range(count)})
    return json.dumps(value, indent=2) + "\n"


def _top_text(editor: CodeEditor) -> str:
    return editor.firstVisibleBlock().text()


def _scroll_to_key(editor: CodeEditor, key: str) -> None:
    block = editor.document().find(f'"{key}"').block()
    assert block.isValid(), key
    editor.verticalScrollBar().setValue(block.firstLineNumber())


def test_link_scrolling_is_a_checkable_view_item_on_by_default(qtbot) -> None:
    window = _show(qtbot)
    link = _action(window, "Link Scrolling")
    assert link.isCheckable()
    assert link.isChecked()
    assert link.isEnabled()
    assert link.menuRole() == QAction.MenuRole.NoRole
    assert list(link.shortcuts()) == []


def test_scrolling_left_keeps_the_matching_right_line_level(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _action(window, "Word Wrap").setChecked(False)
    _compare(qtbot, window, _keyed(0), _keyed(40))
    left, right = _left(window), _right(window)
    for key in ("k050", "k100", "k150"):
        _scroll_to_key(left, key)
        assert f'"{key}"' in _top_text(left)
        assert f'"{key}"' in _top_text(right), key


def test_scrolling_right_keeps_the_matching_left_line_level(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _action(window, "Word Wrap").setChecked(False)
    _compare(qtbot, window, _keyed(0), _keyed(40))
    left, right = _left(window), _right(window)
    _scroll_to_key(right, "k120")
    assert '"k120"' in _top_text(right)
    assert '"k120"' in _top_text(left)


def test_link_scrolling_follows_word_wrap(qtbot, inline_compare) -> None:
    window = _show(qtbot, width=1000)
    assert _action(window, "Word Wrap").isChecked()
    long = {
        f"k{i:03}": ("word " * (40 if i % 3 == 0 else 1)).strip() for i in range(120)
    }
    left_text = json.dumps(long, indent=2) + "\n"
    right_value = {"extra": "x", **long}
    right_text = json.dumps(right_value, indent=2) + "\n"
    _compare(qtbot, window, left_text, right_text)
    left, right = _left(window), _right(window)
    _scroll_to_key(left, "k090")
    assert '"k090"' in _top_text(left)
    assert '"k090"' in _top_text(right)


def test_turning_link_scrolling_off_lets_each_side_scroll_alone(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _action(window, "Word Wrap").setChecked(False)
    _compare(qtbot, window, _keyed(0), _keyed(40))
    left, right = _left(window), _right(window)
    _action(window, "Link Scrolling").trigger()
    assert not _action(window, "Link Scrolling").isChecked()
    before = right.verticalScrollBar().value()
    _scroll_to_key(left, "k100")
    assert right.verticalScrollBar().value() == before
    # Turning it back on lines Right up with Left again.
    _action(window, "Link Scrolling").trigger()
    assert '"k100"' in _top_text(right)


def test_horizontal_scrolling_is_linked_too(qtbot, inline_compare) -> None:
    window = _show(qtbot, width=1000)
    _action(window, "Word Wrap").setChecked(False)
    wide = {f"k{i:03}": "v" * 400 for i in range(40)}
    text = json.dumps(wide, indent=2) + "\n"
    _compare(qtbot, window, text, text.replace('"k000"', '"k000x"'))
    left, right = _left(window), _right(window)
    left.horizontalScrollBar().setValue(300)
    assert right.horizontalScrollBar().value() == 300


def test_without_a_result_lines_are_matched_by_number(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _action(window, "Word Wrap").setChecked(False)
    inline_compare.hold = True
    _compare_texts = (_keyed(0), _keyed(40))
    _enter(qtbot, window, _compare_texts[0])
    _paste_right(window, _compare_texts[1])
    left, right = _left(window), _right(window)
    left.verticalScrollBar().setValue(60)
    assert right.verticalScrollBar().value() == 60


def test_scrolling_outside_compare_is_never_linked(qtbot) -> None:
    window = _show(qtbot)
    _action(window, "Word Wrap").setChecked(False)
    _type_input(window, _keyed(0))
    window._output_pane.editor.setPlainText(_keyed(40))
    output = _editor(window, "outputEditor")
    _left(window).verticalScrollBar().setValue(60)
    assert output.verticalScrollBar().value() == 0


# --- Format in Compare -----------------------------------------------------


_COMPACT_LEFT = '{"b": 1, "a": [1, 2]}'
_COMPACT_RIGHT = '{"b": 2, "a": [1, 2]}'


def test_format_is_not_greyed_out_while_comparing(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    action = _action(window, "Format")
    assert action.isEnabled()
    assert action.toolTip() != "Not available while comparing"
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(button, QPushButton)
    assert button.isEnabled()
    assert button.toolTip() != "Not available while comparing"


def test_format_reformats_both_sides_at_the_indent_size(
    qtbot, inline_compare, inline_format
) -> None:
    window = _show(qtbot)
    picker = _indent_picker(window)
    picker.setCurrentText("4 spaces")
    _compare(qtbot, window, _COMPACT_LEFT, _COMPACT_RIGHT)
    # The picker is greyed out while comparing and still shows its value.
    assert not picker.isEnabled()
    assert picker.currentText() == "4 spaces"
    compares = len(inline_compare.sent)
    _action(window, "Format").trigger()
    qtbot.waitUntil(
        lambda: _left(window).toPlainText().startswith('{\n    "b": 1'),
        timeout=_WAIT_MS,
    )
    _wait_idle(qtbot, window)
    assert _left(window).toPlainText() == json.dumps(
        {"b": 1, "a": [1, 2]}, indent=4
    ) + ("\n" if _left(window).toPlainText().endswith("\n") else "")
    assert _right(window).toPlainText().startswith('{\n    "b": 2')
    jobs = inline_format.sent
    assert [job.text for job in jobs] == [_COMPACT_LEFT, _COMPACT_RIGHT]
    assert [(job.source, job.target) for job in jobs] == [("JSON", "JSON")] * 2
    assert [job.indent for job in jobs] == ["    ", "    "]
    assert not any(job.sort_keys or job.minify or job.validate for job in jobs)
    # Then the two formatted sides are compared again.
    assert len(inline_compare.sent) == compares + 1
    last = inline_compare.sent[-1]
    assert last.left_text == _left(window).toPlainText()
    assert last.right_text == _right(window).toPlainText()
    assert _changes(window).count() == 1
    assert _status(window) == FORMATTED_BOTH
    assert FORMATTED_BOTH == "Formatted both sides."


def test_format_never_sorts_keys(qtbot, inline_compare, inline_format) -> None:
    window = _show(qtbot)
    sort_keys = _action(window, "Sort keys")
    sort_keys.setChecked(True)
    _compare(qtbot, window, _COMPACT_LEFT, _COMPACT_RIGHT)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: len(inline_format.sent) == 2, timeout=_WAIT_MS)
    _wait_idle(qtbot, window)
    assert not any(job.sort_keys for job in inline_format.sent)
    assert _left(window).toPlainText().index('"b"') < _left(window).toPlainText().index(
        '"a"'
    )
    assert sort_keys.isChecked()


def test_yaml_is_formatted_with_spaces_when_tab_is_chosen(
    qtbot, inline_compare, inline_format
) -> None:
    window = _show(qtbot)
    _indent_picker(window).setCurrentText("Tab")
    _enter(qtbot, window, _COMPACT_LEFT)
    _paste_right(window, "b: 2\na:\n- 1\n- 2\n")
    _wait_idle(qtbot, window)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: len(inline_format.sent) == 2, timeout=_WAIT_MS)
    _wait_idle(qtbot, window)
    left_job, right_job = inline_format.sent
    assert (left_job.source, left_job.indent) == ("JSON", "\t")
    assert (right_job.source, right_job.target) == ("YAML", "YAML")
    assert right_job.indent == "  "
    assert "\t" not in _right(window).toPlainText()


def test_the_left_format_is_one_undo_step_on_input(
    qtbot, inline_compare, inline_format
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, _COMPACT_LEFT, _COMPACT_RIGHT)
    left = _left(window)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: left.toPlainText() != _COMPACT_LEFT, timeout=_WAIT_MS)
    _wait_idle(qtbot, window)
    formatted = left.toPlainText()
    _action(window, "Compare").trigger()
    assert left.toPlainText() == formatted
    # One Undo after leaving brings the text from before Format back.
    _action(window, "Undo").trigger()
    assert left.toPlainText() == _COMPACT_LEFT
    _action(window, "Redo").trigger()
    assert left.toPlainText() == formatted


@pytest.mark.parametrize("broken", ["left", "right"])
def test_errors_on_either_side_change_neither(
    qtbot, inline_compare, inline_format, broken
) -> None:
    window = _show(qtbot)
    left_text = _BROKEN if broken == "left" else _COMPACT_LEFT
    right_text = _BROKEN if broken == "right" else _COMPACT_RIGHT
    _compare(qtbot, window, left_text, right_text)
    _action(window, "Format").trigger()
    qtbot.waitUntil(
        lambda: (
            _status(window) == FIX_ERRORS_TO_FORMAT and not window._compare.is_running()
        ),
        timeout=_WAIT_MS,
    )
    assert FIX_ERRORS_TO_FORMAT == "Fix errors to format."
    assert _left(window).toPlainText() == left_text
    assert _right(window).toPlainText() == right_text
    assert window._problems_pane.count() > 0
    expected = [left_text] if broken == "left" else [left_text, right_text]
    assert [job.text for job in inline_format.sent] == expected
    if broken == "right":
        assert _right(window).problem_lines()
    else:
        assert _left(window).problem_lines()


def test_formatting_shows_after_500_ms_with_cancel_and_cancel_changes_nothing(
    qtbot, inline_compare, inline_format
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, _COMPACT_LEFT, _COMPACT_RIGHT)
    inline_format.hold = True
    _action(window, "Format").trigger()
    assert window._compare.is_running()
    assert _status(window) != FORMATTING_MESSAGE
    qtbot.waitUntil(lambda: _status(window) == FORMATTING_MESSAGE, timeout=_WAIT_MS)
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(cancel, QPushButton)
    assert cancel.isVisible()
    # A second Format while one runs does nothing.
    _action(window, "Format").trigger()
    assert len(inline_format.sent) == 1
    # Left finishes; Cancel stops Right's job and drops Left's result.
    inline_format.release()
    assert len(inline_format.sent) == 2
    assert inline_format.sent[-1].text == _COMPACT_RIGHT
    cancel.click()
    assert not window._compare.is_running()
    assert _status(window) == CANCELLED_MESSAGE
    assert _left(window).toPlainText() == _COMPACT_LEFT
    assert _right(window).toPlainText() == _COMPACT_RIGHT
    # The last result stays.
    assert _changes(window).count() == 1


def test_format_with_right_empty_formats_left_only(
    qtbot, inline_compare, inline_format
) -> None:
    window = _show(qtbot)
    _enter(qtbot, window, _COMPACT_LEFT)
    _action(window, "Format").trigger()
    qtbot.waitUntil(
        lambda: _left(window).toPlainText() != _COMPACT_LEFT, timeout=_WAIT_MS
    )
    _wait_idle(qtbot, window)
    assert [job.text for job in inline_format.sent] == [_COMPACT_LEFT]
    assert _right(window).toPlainText() == ""
    assert _changes(window).message() == ADD_SECOND_DOCUMENT
    assert _status(window) == FORMATTED_LEFT
    assert FORMATTED_LEFT == "Formatted Left side."


@pytest.mark.parametrize("side", ["left", "right"])
def test_a_side_that_changed_meanwhile_drops_both_results(
    qtbot, inline_compare, inline_format, side
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, _COMPACT_LEFT, _COMPACT_RIGHT)
    compares = len(inline_compare.sent)
    inline_format.hold = True
    _action(window, "Format").trigger()
    inline_format.release()
    assert len(inline_format.sent) == 2
    # The side changes while Right's job runs, with no compare asked for.
    edited = '{"edited": true}'
    _left(window).setPlainText(edited) if side == "left" else _right(
        window
    ).setPlainText(edited)
    inline_format.release()
    assert not window._compare.is_running()
    expected_left = edited if side == "left" else _COMPACT_LEFT
    expected_right = edited if side == "right" else _COMPACT_RIGHT
    assert _left(window).toPlainText() == expected_left
    assert _right(window).toPlainText() == expected_right
    assert len(inline_compare.sent) == compares
    assert _status(window) not in (FORMATTED_BOTH, FORMATTED_LEFT)


def test_format_keeps_its_own_size_sheet(qtbot, inline_compare, inline_format) -> None:
    """Format's 352 kB YAML question still asks; the 2 MB one is for the compare."""
    big = "rows:\n" + "".join(f"- name: row {i:06}\n" for i in range(20_000))
    assert 352_000 <= len(big.encode("utf-8")) < 2_000_000
    window = _show(qtbot)
    _set_picker(window, "inputFormat", "YAML")
    _enter(qtbot, window, big)
    _paste_right(window, "rows: []\n")
    _wait_idle(qtbot, window)
    _action(window, "Format").trigger()
    box = _wait_box(qtbot, window, "sizeWarning")
    assert window.findChild(QMessageBox, "compareSizeWarning") is None
    assert inline_format.sent == []
    _box_button(box, "Cancel").click()
    assert inline_format.sent == []
    _action(window, "Format").trigger()
    box = _wait_box(qtbot, window, "sizeWarning")
    _box_button(box, "Format anyway").click()
    qtbot.waitUntil(lambda: len(inline_format.sent) == 2, timeout=_WAIT_MS)
    _wait_idle(qtbot, window)
    assert _status(window) == FORMATTED_BOTH


def test_format_on_the_spawn_worker_formats_both_sides(qtbot) -> None:
    window = _show(qtbot)
    try:
        _type_input(window, _COMPACT_LEFT)
        _action(window, "Compare").trigger()
        _paste_right(window, _COMPACT_RIGHT)
        qtbot.waitUntil(
            lambda: _changes(window).count() == 1, timeout=_SPAWN_TIMEOUT_MS
        )
        _action(window, "Format").trigger()
        qtbot.waitUntil(
            lambda: (
                _right(window).toPlainText().startswith('{\n  "b": 2')
                and not window._compare.is_running()
            ),
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert _left(window).toPlainText().startswith('{\n  "b": 1')
        qtbot.waitUntil(
            lambda: _changes(window).count() == 1, timeout=_SPAWN_TIMEOUT_MS
        )
        rows = [
            _changes(window).model().row_at(index)
            for index in range(_changes(window).model().rowCount())
        ]
        assert any(isinstance(row, ChangeRow) for row in rows)
    finally:
        window.shutdown_parse_worker()


def test_a_closed_window_leaves_no_compare_widgets_behind(qtbot) -> None:
    """Right's editor, its find bar and the Changes pane go with the window.

    They have no parent while Compare is off. A lambda connected to one of
    their signals kept them, Compare and the window's wrapper alive, so
    every later window in the process got slower.
    """
    import gc

    from PySide6.QtCore import QEvent

    names = {"rightEditor", "rightFindBar", "changesPage"}

    def _left_over() -> set[str]:
        gc.collect()
        return {
            widget.objectName()
            for widget in QApplication.topLevelWidgets()
            if widget.objectName() in names
        }

    before = _left_over()
    # A window that never enters Compare, as most are: Right and the
    # Changes pane are still parentless.
    window = create_main_window()
    window.show()
    qtbot.waitExposed(window)
    _type_input(window, _LEFT)
    window.close()
    window.deleteLater()
    del window
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()
    assert _left_over() == before
