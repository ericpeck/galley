"""Open loads a UTF-8 file into Input and does not format it."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QMessageBox, QPushButton

from galley.app.actions import COMING_SOON
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import YAML_WARNING_BYTES
from galley.app.open_file import (
    OPEN_ERROR_UNREADABLE,
    OPEN_ERROR_UTF8,
    OPEN_FILE_FILTER,
    load_last_open_dir,
    save_last_open_dir,
)

_KEEP = '{"keep": true}\n'
_SAMPLE = '{"a":1,"b":[true,null]}'


def test_open_loads_file_text_without_formatting(qtbot, tmp_path, monkeypatch) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    editor = _input(window)
    output = _output(window)
    output.setPlainText("leave\n")
    status = _status(window)
    seen = _choose(monkeypatch, path)
    button = window.findChild(QPushButton, "emptyOpenButton")
    assert isinstance(button, QPushButton)
    assert button.isEnabled()
    assert button.toolTip() != COMING_SOON
    action = window._actions.open_file
    assert action.isEnabled()
    assert action.toolTip() != COMING_SOON

    button.click()

    assert editor.toPlainText() == _SAMPLE
    assert editor.textCursor().position() == 0
    assert editor.textCursor().anchor() == 0
    assert output.toPlainText() == "leave\n"
    assert status.text() == "Ready"
    assert not window._formatting
    assert window.windowTitle() == "sample.json — Galley"
    assert "\u2014" in window.windowTitle()
    assert _format_name(window) == "JSON"
    assert _message(window, "replaceInput") is None
    assert _message(window, "openError") is None
    assert _message(window, "sizeWarning") is None
    assert seen["parent"] is window
    assert seen["caption"] == "Open"
    assert seen["filter"] == OPEN_FILE_FILTER
    assert OPEN_FILE_FILTER == ("JSON and YAML (*.json *.yaml *.yml);;All files (*)")
    assert Path(str(seen["directory"])) == Path.home()
    qtbot.waitUntil(lambda: editor.hasFocus())

    editor.undo()
    assert editor.toPlainText() == ""
    editor.redo()
    assert editor.toPlainText() == _SAMPLE


def test_open_shortcut_loads_the_file(qtbot, tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.yaml"
    _write(path, "a: 1\n")
    window = _show(qtbot)
    editor = _input(window)
    qtbot.waitUntil(lambda: editor.hasFocus())
    _choose(monkeypatch, path)
    shortcut = window._actions.open_file.shortcuts()[0]
    qtbot.keySequence(editor, shortcut)
    qtbot.waitUntil(lambda: editor.toPlainText() == "a: 1\n")
    assert window.windowTitle() == "config.yaml — Galley"
    assert _format_name(window) == "YAML"
    assert not window._formatting


def test_unsaved_input_asks_before_replace(qtbot, tmp_path, monkeypatch) -> None:
    first = tmp_path / "keep.json"
    second = tmp_path / "next.json"
    _write(first, _KEEP)
    _write(second, '{"next": 1}\n')
    window = _show(qtbot)
    editor = _input(window)
    output = _output(window)
    output.setPlainText("stay\n")
    editor.setPlainText("typed")
    _choose_sequence(monkeypatch, [first, first, second, second])

    window._actions.open_file.trigger()
    box = _require(window, "replaceInput")
    assert box.windowTitle() == "Galley"
    assert box.text() == "Replace the current input?"
    assert box.informativeText() == (
        "Your current input will be replaced with the file's contents."
    )
    assert box.icon() == QMessageBox.Icon.Warning
    replace = box.defaultButton()
    cancel = box.escapeButton()
    assert replace is not None and replace.text() == "Replace"
    assert cancel is not None and cancel.text() == "Cancel"
    assert replace.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert cancel.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert _message(window, "openError") is None
    cancel.click()
    qtbot.waitUntil(lambda: not box.isVisible())
    assert editor.toPlainText() == "typed"
    assert output.toPlainText() == "stay\n"
    assert window.windowTitle() == "Galley"
    assert not window._formatting
    qtbot.waitUntil(lambda: editor.hasFocus())

    window._actions.open_file.trigger()
    again = _require(window, "replaceInput")
    confirmed = again.defaultButton()
    assert confirmed is not None
    confirmed.click()
    qtbot.waitUntil(lambda: editor.toPlainText() == _KEEP)
    assert output.toPlainText() == "stay\n"
    assert window.windowTitle() == "keep.json — Galley"
    assert status_ready(window)
    assert not window._formatting

    window._actions.open_file.trigger()
    assert _message(window, "replaceInput") is None
    assert editor.toPlainText() == '{"next": 1}\n'
    assert window.windowTitle() == "next.json — Galley"

    editor.setPlainText(editor.toPlainText() + " ")
    window._actions.open_file.trigger()
    dirty = _require(window, "replaceInput")
    declined = dirty.escapeButton()
    assert declined is not None
    declined.click()
    qtbot.waitUntil(lambda: not dirty.isVisible())
    assert editor.toPlainText() == '{"next": 1}\n '
    assert window.windowTitle() == "next.json — Galley"


def test_cancelled_dialog_does_nothing(qtbot, tmp_path, monkeypatch) -> None:
    window = _show(qtbot)
    editor = _input(window)
    output = _output(window)
    editor.setPlainText("typed")
    output.setPlainText("out\n")
    calls: list[object] = []

    def _cancel(*args: object, **kwargs: object) -> tuple[str, str]:
        calls.append((args, kwargs))
        return "", ""

    monkeypatch.setattr("galley.app.main_window.QFileDialog.getOpenFileName", _cancel)
    window._actions.open_file.trigger()
    assert calls
    assert editor.toPlainText() == "typed"
    assert output.toPlainText() == "out\n"
    assert window.windowTitle() == "Galley"
    assert _message(window, "replaceInput") is None
    assert _message(window, "openError") is None
    assert _message(window, "sizeWarning") is None
    assert not window._formatting
    qtbot.waitUntil(lambda: editor.hasFocus())


def test_non_utf8_file_names_the_file_and_keeps_input(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "notes.json"
    path.write_bytes(b"caf\xe9")
    _assert_open_error(
        qtbot,
        monkeypatch,
        path,
        OPEN_ERROR_UTF8,
        "This file isn't UTF-8 text.",
    )


def test_missing_file_names_the_file_and_keeps_input(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "config.yaml"
    _assert_open_error(
        qtbot,
        monkeypatch,
        path,
        OPEN_ERROR_UNREADABLE,
        "Galley doesn't have permission to read this file.",
    )


def test_unreadable_file_names_the_file_and_keeps_input(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "config.yaml"
    _write(path, "a: 1\n")
    real_read = Path.read_bytes

    def _deny(self: Path) -> bytes:
        if self == path:
            raise PermissionError(13, "Permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _deny)
    _assert_open_error(
        qtbot,
        monkeypatch,
        path,
        OPEN_ERROR_UNREADABLE,
        "Galley doesn't have permission to read this file.",
    )


def test_open_dialog_remembers_the_last_folder(qtbot, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "projects"
    folder.mkdir()
    chosen = folder / "config.yaml"
    _write(chosen, "a: 1\n")
    other = folder / "next.json"
    _write(other, "{}\n")
    window = _show(qtbot)
    directories = _choose_sequence(monkeypatch, [None, chosen, other])

    window._actions.open_file.trigger()
    window._actions.open_file.trigger()
    assert _input(window).toPlainText() == "a: 1\n"
    window._actions.open_file.trigger()
    assert _input(window).toPlainText() == "{}\n"
    assert [Path(item) for item in directories] == [
        Path.home(),
        Path.home(),
        folder,
    ]


def test_missing_last_folder_falls_back_to_home(tmp_path: Path) -> None:
    save_last_open_dir(tmp_path / "gone")
    assert Path(load_last_open_dir()) == Path.home()
    folder = tmp_path / "keep"
    folder.mkdir()
    save_last_open_dir(folder)
    assert Path(load_last_open_dir()) == folder


def test_extension_picks_the_format(qtbot, tmp_path, monkeypatch) -> None:
    files = [
        ("object.json", "a: 1\n", "JSON"),
        ("flow.yaml", '{"a": 1}\n', "YAML"),
        ("list.yml", "b: 2\n", "YAML"),
        ("DATA.YML", "c: 3\n", "YAML"),
        ("notes.txt", '  {"a": 1}\n', "JSON"),
        ("notes2.txt", "b: 2\n", "YAML"),
        ("array.txt", "[1]\n", "JSON"),
    ]
    paths = []
    for name, text, _expected in files:
        path = tmp_path / name
        if name == "list.yml":
            path.write_bytes(b"b: 2\r\n")
        else:
            _write(path, text)
        paths.append(path)
    window = _show(qtbot)
    editor = _input(window)
    _choose_sequence(monkeypatch, paths)
    expected_text = {
        "list.yml": "b: 2\n",
    }
    for path, (_name, text, expected) in zip(paths, files, strict=True):
        window._actions.open_file.trigger()
        assert _message(window, "replaceInput") is None
        assert editor.toPlainText() == expected_text.get(path.name, text)
        assert _format_name(window) == expected
        assert window.windowTitle() == f"{path.name} — Galley"
        assert not window._formatting
        assert _output(window).toPlainText() == ""


def test_large_yaml_warns_before_loading_and_does_not_format(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "big.yaml"
    payload = "y" * YAML_WARNING_BYTES
    _write(path, payload)
    window = _show(qtbot)
    editor = _input(window)
    output = _output(window)
    output.setPlainText("leave\n")
    editor.setPlainText("keep")
    _choose(monkeypatch, path)

    window._actions.open_file.trigger()
    replace = _require(window, "replaceInput")
    confirmed = replace.defaultButton()
    assert confirmed is not None
    confirmed.click()
    qtbot.waitUntil(lambda: _message(window, "sizeWarning") is not None)
    warning = _require(window, "sizeWarning")
    assert editor.toPlainText() == "keep"
    assert warning.text() == "Format this 352 KB document?"
    assert warning.informativeText() == (
        "Large documents can take a while to format. You can cancel while it runs."
    )
    cancel = warning.escapeButton()
    assert cancel is not None and cancel.text() == "Cancel"
    cancel.click()
    qtbot.waitUntil(lambda: _message(window, "sizeWarning") is None)
    assert editor.toPlainText() == "keep"
    assert output.toPlainText() == "leave\n"
    assert not window._formatting
    assert not window._size_warning_accepted
    qtbot.waitUntil(lambda: editor.hasFocus())

    window._actions.open_file.trigger()
    replace = _require(window, "replaceInput")
    confirmed = replace.defaultButton()
    assert confirmed is not None
    confirmed.click()
    qtbot.waitUntil(lambda: _message(window, "sizeWarning") is not None)
    warning = _require(window, "sizeWarning")
    anyway = warning.defaultButton()
    assert anyway is not None and anyway.text() == "Format anyway"
    anyway.click()
    qtbot.waitUntil(lambda: editor.toPlainText() == payload)
    assert _message(window, "sizeWarning") is None
    assert output.toPlainText() == "leave\n"
    assert not window._formatting
    assert status_ready(window)
    assert window.windowTitle() == "big.yaml — Galley"
    assert _format_name(window) == "YAML"
    assert editor.textCursor().position() == 0

    started: list[str] = []
    window._start_format = lambda text: started.append(text)  # type: ignore[method-assign]
    window._actions.format.trigger()
    assert started == [payload]
    assert _message(window, "sizeWarning") is None
    assert not window._formatting


def test_yaml_under_the_limit_loads_without_a_warning(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "small.yaml"
    payload = "y" * (YAML_WARNING_BYTES - 1)
    _write(path, payload)
    window = _show(qtbot)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    assert _message(window, "sizeWarning") is None
    assert _input(window).toPlainText() == payload
    assert not window._formatting


def test_large_json_below_the_json_limit_loads_without_a_warning(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "big.json"
    payload = "y" * YAML_WARNING_BYTES
    _write(path, payload)
    window = _show(qtbot)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    assert _message(window, "sizeWarning") is None
    assert _input(window).toPlainText() == payload
    assert _format_name(window) == "JSON"
    assert not window._formatting
    assert not window._size_warning_accepted


def _assert_open_error(qtbot, monkeypatch, path: Path, detail_constant, detail) -> None:
    assert detail_constant == detail
    window = _show(qtbot)
    editor = _input(window)
    output = _output(window)
    output.setPlainText("leave\n")
    editor.setPlainText("keep")
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    replace = _require(window, "replaceInput")
    assert _message(window, "openError") is None
    declined = replace.escapeButton()
    assert declined is not None
    declined.click()
    qtbot.waitUntil(lambda: not replace.isVisible())
    assert editor.toPlainText() == "keep"
    assert _message(window, "openError") is None

    window._actions.open_file.trigger()
    replace = _require(window, "replaceInput")
    confirmed = replace.defaultButton()
    assert confirmed is not None
    confirmed.click()
    qtbot.waitUntil(lambda: _message(window, "openError") is not None)
    error = _require(window, "openError")
    assert error.windowTitle() == "Galley"
    assert error.text() == f"Couldn't open {path.name}."
    assert error.informativeText() == detail
    assert str(path.parent) not in error.text()
    assert str(path.parent) not in error.informativeText()
    assert error.icon() == QMessageBox.Icon.Warning
    ok = error.button(QMessageBox.StandardButton.Ok)
    assert ok is not None
    assert error.defaultButton() is ok
    assert error.escapeButton() is ok
    assert ok.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert editor.toPlainText() == "keep"
    assert output.toPlainText() == "leave\n"
    assert window.windowTitle() == "Galley"
    assert not window._formatting
    ok.click()
    qtbot.waitUntil(lambda: _message(window, "openError") is None)
    assert editor.toPlainText() == "keep"
    assert output.toPlainText() == "leave\n"
    qtbot.waitUntil(lambda: editor.hasFocus())


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _input(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _output(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _status(window) -> QLabel:
    status = window.findChild(QLabel, "statusMessage")
    assert isinstance(status, QLabel)
    return status


def status_ready(window) -> bool:
    return _status(window).text() == "Ready"


def _format_name(window) -> str:
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    return combo.currentText()


def _message(window, name: str) -> QMessageBox | None:
    found = [
        box
        for box in window.findChildren(QMessageBox)
        if box.objectName() == name and box.isVisible()
    ]
    if not found:
        return None
    assert len(found) == 1
    return found[0]


def _require(window, name: str) -> QMessageBox:
    box = _message(window, name)
    assert box is not None
    return box


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _choose(monkeypatch, path: Path) -> dict[str, object]:
    seen: dict[str, object] = {}

    def _fake(parent, caption, directory, file_filter, *_args, **_kwargs):
        seen["parent"] = parent
        seen["caption"] = caption
        seen["directory"] = directory
        seen["filter"] = file_filter
        return str(path), OPEN_FILE_FILTER

    monkeypatch.setattr("galley.app.main_window.QFileDialog.getOpenFileName", _fake)
    return seen


def _choose_sequence(monkeypatch, paths: list[Path | None]) -> list[str]:
    directories: list[str] = []
    remaining = list(paths)

    def _fake(_parent, _caption, directory, _file_filter, *_args, **_kwargs):
        directories.append(directory)
        chosen = remaining.pop(0)
        if chosen is None:
            return "", ""
        return str(chosen), OPEN_FILE_FILTER

    monkeypatch.setattr("galley.app.main_window.QFileDialog.getOpenFileName", _fake)
    return directories
