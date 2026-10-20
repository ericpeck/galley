"""Open loads a UTF-8 file into Input and does not format it."""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QStandardPaths, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QImage,
    QKeySequence,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QWidget,
)

from galley.app.actions import COMING_SOON
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import OPEN_REVEAL_MS, YAML_WARNING_BYTES
from galley.app.open_file import (
    MAX_RECENT,
    OPEN_FILE_FILTER,
    load_last_open_dir,
    load_recent_files,
    remember_recent_file,
    save_last_open_dir,
)
from galley.app.pane import PaneRole, PaneWidget
from galley.app.problems_settings import problems_settings
from galley.app.theme import composite, contrast_ratio
from galley.core.open_text import MAX_OPEN_BYTES, OpenedText

_KEEP = '{"keep": true}\n'
_SAMPLE = '{"a":1,"b":[true,null]}'


def test_open_file_button_loads_text_without_formatting(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    editor = _input(window)
    output = _output(window)
    output.setPlainText("old output\n")
    seen = _choose(monkeypatch, path)
    button = window.findChild(QPushButton, "emptyOpenButton")
    assert isinstance(button, QPushButton)
    assert button.isEnabled()
    assert button.toolTip() != COMING_SOON
    action = window._actions.open_file
    assert action.isEnabled()
    assert action.toolTip() != COMING_SOON

    button.click()
    _wait_loaded(qtbot, window, _SAMPLE)

    assert editor.textCursor().position() == 0
    assert editor.textCursor().anchor() == 0
    assert output.toPlainText() == ""
    assert _status(window).text() == "Opened sample.json (23 B)"
    assert not window._formatting
    assert window.windowTitle() == "sample.json — Galley"
    assert Path(window.windowFilePath()) == path
    assert _format_name(window) == "JSON"
    assert _message(window, "replaceInput") is None
    assert _message(window, "sizeWarning") is None
    assert seen["parent"] is window
    assert seen["caption"] == "Open"
    assert seen["filter"] == OPEN_FILE_FILTER
    assert OPEN_FILE_FILTER == "JSON and YAML (*.json *.yaml *.yml);;All Files (*)"
    assert Path(str(seen["directory"])) == Path(_default_folder())
    qtbot.waitUntil(lambda: editor.hasFocus())

    editor.undo()
    assert editor.toPlainText() == ""
    editor.redo()
    assert editor.toPlainText() == _SAMPLE


def test_full_path_stays_out_of_the_visible_status(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    _wait_loaded(qtbot, window, _SAMPLE)
    status = _status(window)
    assert str(path.parent) not in status.text()
    assert str(path.parent) not in status.displayed_text()
    assert status.toolTip() == str(path)
    assert status.accessibleDescription() == str(path)


def test_opened_status_returns_to_ready_after_four_seconds(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "sample.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    _wait_loaded(qtbot, window, _SAMPLE)
    timer = window._opened_status_timer
    assert timer.isActive()
    assert timer.interval() == 4000
    timer.stop()
    timer.timeout.emit()
    assert _status(window).text() == "Ready"
    assert _status(window).toolTip() == ""


def test_open_shortcut_is_the_standard_open_key(qtbot, tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.yaml"
    _write(path, "a: 1\n")
    window = _show(qtbot)
    editor = _input(window)
    qtbot.waitUntil(lambda: editor.hasFocus())
    _choose(monkeypatch, path)
    action = window._actions.open_file
    assert action.shortcuts() == QKeySequence.keyBindings(QKeySequence.StandardKey.Open)
    qtbot.keySequence(editor, action.shortcuts()[0])
    _wait_loaded(qtbot, window, "a: 1\n")
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
    assert box.text() == "Replace the current input with keep.json?"
    assert box.informativeText() == ""
    replace = box.defaultButton()
    cancel = box.escapeButton()
    assert replace is not None and replace.text() == "Replace"
    assert cancel is not None and cancel.text() == "Cancel"
    assert replace.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert cancel.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert not window._actions.open_file.isEnabled()
    cancel.click()
    qtbot.waitUntil(lambda: not box.isVisible())
    assert editor.toPlainText() == "typed"
    assert output.toPlainText() == "stay\n"
    assert window.windowTitle() == "Galley"
    assert window._actions.open_file.isEnabled()
    qtbot.waitUntil(lambda: editor.hasFocus())

    window._actions.open_file.trigger()
    again = _require(window, "replaceInput")
    confirmed = again.defaultButton()
    assert confirmed is not None
    confirmed.click()
    _wait_loaded(qtbot, window, _KEEP)
    assert output.toPlainText() == ""
    assert window.windowTitle() == "keep.json — Galley"

    # Input that still matches the opened file is replaced without asking.
    window._actions.open_file.trigger()
    assert _message(window, "replaceInput") is None
    _wait_loaded(qtbot, window, '{"next": 1}\n')
    assert window.windowTitle() == "next.json — Galley"

    editor.setPlainText(editor.toPlainText() + " ")
    window._actions.open_file.trigger()
    dirty = _require(window, "replaceInput")
    assert dirty.text() == "Replace the current input with next.json?"
    declined = dirty.escapeButton()
    assert declined is not None
    declined.click()
    qtbot.waitUntil(lambda: not dirty.isVisible())
    assert editor.toPlainText() == '{"next": 1}\n '
    assert window.windowTitle() == "next.json — Galley"


def test_cancelled_dialog_does_nothing(qtbot, monkeypatch) -> None:
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
    assert _status(window).text() == "Ready"
    assert _message(window, "replaceInput") is None
    assert window._loading_path is None
    qtbot.waitUntil(lambda: editor.hasFocus())


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "not_found",
            "Couldn't find config.yaml. It may have been moved or deleted.",
        ),
        ("is_directory", "config.yaml is a folder. Choose a file to open."),
        (
            "permission",
            "Couldn't open config.yaml. You don't have permission to read it.",
        ),
        (
            "not_utf8",
            "config.yaml isn't UTF-8 text (line 3), so Galley can't open it.",
        ),
        ("utf16", "config.yaml is UTF-16 text. Save it as UTF-8 and try again."),
        ("os_error", "Couldn't read config.yaml. Try again, or check the disk."),
    ],
)
def test_errors_show_in_the_status_bar_and_keep_input(
    qtbot, tmp_path, monkeypatch, kind: str, expected: str
) -> None:
    path = tmp_path / "config.yaml"
    match kind:
        case "is_directory":
            path.mkdir()
        case "permission" | "os_error":
            _write(path, "a: 1\n")
            _fail_reading(monkeypatch, path, kind)
        case "not_utf8":
            path.write_bytes(b"a: 1\nb: 2\nc: caf\xe9\n")
        case "utf16":
            path.write_bytes("a: 1\n".encode("utf-16"))
    _assert_open_error(qtbot, monkeypatch, path, expected)


def test_a_file_over_100_mb_is_refused(qtbot, tmp_path, monkeypatch) -> None:
    path = tmp_path / "huge.json"
    with path.open("wb") as handle:
        handle.truncate(MAX_OPEN_BYTES + 1)
    assert MAX_OPEN_BYTES == 100_000_000
    _assert_open_error(
        qtbot, monkeypatch, path, "huge.json is too large to open (over 100 MB)."
    )


def test_opening_shows_after_500_ms_and_blocks_open_and_format(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "slow.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    editor = _input(window)
    jobs: list[int] = []
    monkeypatch.setattr(window._reader, "read", lambda job, _path: jobs.append(job))
    _choose(monkeypatch, path)

    window._actions.open_file.trigger()
    assert jobs == [window._open_job]
    assert OPEN_REVEAL_MS == 500
    assert window._open_reveal_timer.interval() == 500
    assert not window._actions.open_file.isEnabled()
    assert not window._actions.format.isEnabled()
    button = window.findChild(QPushButton, "emptyOpenButton")
    assert isinstance(button, QPushButton) and not button.isEnabled()
    qtbot.wait(300)
    assert _status(window).text() == "Ready"
    assert not _spinner(window).isVisible()

    qtbot.waitUntil(lambda: _status(window).text() == "Opening slow.json…")
    assert _spinner(window).isVisible()
    assert _spinner(window).accessibleName() == "Opening"
    assert _message(window, "sizeWarning") is None

    window._reader.finished.emit(jobs[0], OpenedText(_SAMPLE, len(_SAMPLE)))
    _wait_loaded(qtbot, window, _SAMPLE)
    assert not _spinner(window).isVisible()
    assert _spinner(window).accessibleName() != "Opening"
    assert window._actions.open_file.isEnabled()
    assert window._actions.format.isEnabled()
    assert _status(window).text() == "Opened slow.json (23 B)"
    assert editor.textCursor().position() == 0


def test_large_file_opens_without_a_prompt_and_format_still_asks(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "big.yaml"
    payload = "y" * YAML_WARNING_BYTES
    _write(path, payload)
    window = _show(qtbot)
    _choose(monkeypatch, path)

    window._actions.open_file.trigger()
    _wait_loaded(qtbot, window, payload)
    assert _message(window, "sizeWarning") is None
    assert not window._formatting
    assert _status(window).text() == "Opened big.yaml (352 KB)"
    assert _format_name(window) == "YAML"

    started: list[str] = []
    window._start_format = lambda text: started.append(text)  # type: ignore[method-assign]
    window._actions.format.trigger()
    warning = _require(window, "sizeWarning")
    assert warning.text() == "Format this 352 KB document?"
    assert started == []
    cancel = warning.escapeButton()
    assert cancel is not None
    cancel.click()
    qtbot.waitUntil(lambda: _message(window, "sizeWarning") is None)
    assert started == []


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
    _wait_loaded(qtbot, window, "a: 1\n")
    window._actions.open_file.trigger()
    _wait_loaded(qtbot, window, "{}\n")
    assert [Path(item) for item in directories] == [
        Path(_default_folder()),
        Path(_default_folder()),
        folder,
    ]


def test_missing_last_folder_falls_back_to_documents(tmp_path: Path) -> None:
    save_last_open_dir(tmp_path / "gone")
    assert load_last_open_dir() == _default_folder()
    folder = tmp_path / "keep"
    folder.mkdir()
    save_last_open_dir(folder)
    assert Path(load_last_open_dir()) == folder


def test_open_settings_stay_in_the_test_folder(tmp_path: Path) -> None:
    folder = tmp_path / "keep"
    folder.mkdir()
    save_last_open_dir(folder)
    remember_recent_file(folder / "a.json")
    stored = Path(problems_settings().fileName())
    assert stored.is_file()
    # Resolve both: Windows can give the temp folder its short 8.3 name.
    assert stored.resolve().is_relative_to(tmp_path.resolve())


def test_extension_then_content_picks_the_format(qtbot, tmp_path, monkeypatch) -> None:
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
    _choose_sequence(monkeypatch, paths)
    expected_text = {"list.yml": "b: 2\n"}
    for path, (_name, text, expected) in zip(paths, files, strict=True):
        window._actions.open_file.trigger()
        assert _message(window, "replaceInput") is None
        _wait_loaded(qtbot, window, expected_text.get(path.name, text))
        assert _format_name(window) == expected
        assert window.windowTitle() == f"{path.name} — Galley"


@pytest.mark.parametrize("mode", ["JSON", "YAML"])
def test_unsettled_content_keeps_the_current_mode(
    qtbot, tmp_path, monkeypatch, mode: str
) -> None:
    path = tmp_path / "notes.txt"
    _write(path, "just some words\n")
    window = _show(qtbot)
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    combo.setCurrentText(mode)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    _wait_loaded(qtbot, window, "just some words\n")
    assert _format_name(window) == mode


def test_utf8_bom_is_dropped(qtbot, tmp_path, monkeypatch) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf{}\n")
    window = _show(qtbot)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    _wait_loaded(qtbot, window, "{}\n")


def test_open_recent_lists_files_newest_first(qtbot, tmp_path, monkeypatch) -> None:
    first = tmp_path / "a" / "config.json"
    second = tmp_path / "b" / "config.json"
    third = tmp_path / "b" / "other.yaml"
    for path in (first, second, third):
        path.parent.mkdir(exist_ok=True)
        _write(path, "{}\n")
    window = _show(qtbot)
    _choose_sequence(monkeypatch, [first, second, third])
    for _path in range(3):
        window._actions.open_file.trigger()
        qtbot.waitUntil(lambda: window._loading_path is None)
    menu = _recent_menu(window)
    entries = _menu_entries(menu)
    assert [item.text() for item in entries] == [
        "other.yaml",
        "config.json — b",
        "config.json — a",
        "Clear Menu",
    ]
    assert [item.toolTip() for item in entries[:3]] == [
        str(third),
        str(second),
        str(first),
    ]
    assert all(item.isEnabled() for item in entries)

    first.unlink()
    menu.aboutToShow.emit()
    missing = _menu_entries(menu)[2]
    assert missing.text() == "config.json — a"
    assert not missing.isEnabled()
    assert missing.toolTip() == "File not found"

    _write(third, "picked: 1\n")
    _menu_entries(menu)[0].trigger()
    _wait_loaded(qtbot, window, "picked: 1\n")
    assert window.windowTitle() == "other.yaml — Galley"

    _menu_entries(menu)[-1].trigger()
    entries = _menu_entries(menu)
    assert [item.text() for item in entries] == ["Clear Menu"]
    assert not entries[0].isEnabled()
    assert load_recent_files() == []


def test_open_recent_keeps_ten_entries(tmp_path: Path) -> None:
    for index in range(MAX_RECENT + 2):
        remember_recent_file(tmp_path / f"file{index}.json")
    recent = load_recent_files()
    assert len(recent) == MAX_RECENT == 10
    assert recent[0] == tmp_path / "file11.json"
    remember_recent_file(tmp_path / "file5.json")
    recent = load_recent_files()
    assert recent[0] == tmp_path / "file5.json"
    assert recent.count(tmp_path / "file5.json") == 1


def test_dropping_a_file_on_input_opens_it(qtbot, tmp_path) -> None:
    path = tmp_path / "dropped.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    pane = _input_pane(window)
    viewport = _input(window).viewport()

    enter = _drag_enter(viewport, [path])
    assert enter.isAccepted()
    assert pane.property("paneDropTarget") is True
    QApplication.sendEvent(viewport, QDragLeaveEvent())
    assert pane.property("paneDropTarget") is False

    _drag_enter(viewport, [path])
    drop = _drop(viewport, [path])
    assert drop.isAccepted()
    assert pane.property("paneDropTarget") is False
    _wait_loaded(qtbot, window, _SAMPLE)
    assert window.windowTitle() == "dropped.json — Galley"
    assert _status(window).text() == "Opened dropped.json (23 B)"


def test_dropping_on_the_window_opens_without_an_outline(qtbot, tmp_path) -> None:
    path = tmp_path / "dropped.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    pane = _input_pane(window)
    output = _output(window).viewport()
    _drag_enter(output, [path])
    assert pane.property("paneDropTarget") is not True
    _drop(output, [path])
    _wait_loaded(qtbot, window, _SAMPLE)


def test_dropping_several_files_opens_the_first(qtbot, tmp_path) -> None:
    paths = [tmp_path / f"part{index}.json" for index in range(3)]
    for index, path in enumerate(paths):
        _write(path, f'{{"part": {index}}}\n')
    window = _show(qtbot)
    viewport = _input(window).viewport()
    _drag_enter(viewport, paths)
    _drop(viewport, paths)
    _wait_loaded(qtbot, window, '{"part": 0}\n')
    assert _status(window).text() == "Opened the first of 3 files."


def test_dropping_text_is_left_to_the_editor(qtbot) -> None:
    window = _show(qtbot)
    viewport = _input(window).viewport()
    mime = QMimeData()
    mime.setText("plain")
    event = QDragEnterEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)
    assert _input_pane(window).property("paneDropTarget") is not True


def test_formatting_blocks_open_and_says_why(qtbot, tmp_path) -> None:
    path = tmp_path / "dropped.json"
    _write(path, _SAMPLE)
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("typed")
    window._formatting = True
    window._sync_format_button()
    action = window._actions.open_file
    assert not action.isEnabled()
    assert action.toolTip() == "Wait for formatting to finish"
    assert not _recent_menu(window).isEnabled()

    viewport = editor.viewport()
    enter = _drag_enter(viewport, [path])
    assert enter.isAccepted()
    assert _input_pane(window).property("paneDropTarget") is not True
    _drop(viewport, [path])
    assert _status(window).text() == "Can't open a file while formatting."
    assert _message(window, "replaceInput") is None
    assert editor.toPlainText() == "typed"

    window._formatting = False
    window._sync_format_button()
    assert action.isEnabled()
    assert action.toolTip() != "Wait for formatting to finish"


def test_empty_input_heading_names_the_open_shortcut(qtbot) -> None:
    window = _show(qtbot)
    heading = window.findChild(QLabel, "inputEmptyTitle")
    assert isinstance(heading, QLabel)
    shortcut = QKeySequence(QKeySequence.StandardKey.Open).toString(
        QKeySequence.SequenceFormat.NativeText
    )
    assert heading.text() == (
        f"Paste JSON or YAML, or drop a file here. {shortcut} to open."
    )


def test_drop_outline_reaches_three_to_one(qtbot, scheme_palette) -> None:
    pane = PaneWidget(PaneRole.INPUT)
    qtbot.addWidget(pane)
    pane.resize(480, 520)
    pane.show()
    qtbot.waitExposed(pane)
    background = scheme_palette.color(QPalette.ColorRole.Window)
    base = composite(scheme_palette.color(QPalette.ColorRole.Base), background)
    resting = _left_edge(pane)
    pane.set_drop_target(True)
    outline = _left_edge(pane)
    pane.set_drop_target(False)
    assert outline != resting
    assert contrast_ratio(outline, background) >= 3
    assert contrast_ratio(outline, base) >= 3


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_open_status_text_reaches_four_and_a_half_to_one(
    qtbot, tmp_path, monkeypatch, install_scheme, scheme: str
) -> None:
    path = tmp_path / "config.yaml"
    window = _show(qtbot)
    palette = install_scheme(scheme)
    _choose(monkeypatch, path)
    window._actions.open_file.trigger()
    qtbot.waitUntil(lambda: "Couldn't find" in _status(window).text())
    status = _status(window)
    image = status.grab().toImage()
    # The left margin is bare status bar, so its corner is the fill.
    fill = image.pixelColor(1, 1)
    assert fill == palette.color(QPalette.ColorRole.Window)
    assert _strongest_contrast(image, fill) >= 4.5


def _assert_open_error(qtbot, monkeypatch, path: Path, expected: str) -> None:
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
    qtbot.waitUntil(lambda: _status(window).text() == expected)
    status = _status(window)
    assert window._loading_path is None
    assert str(path.parent) not in status.text()
    assert status.toolTip() == str(path)
    assert status.accessibleDescription() == str(path)
    assert not window.findChildren(QMessageBox, "openError")
    assert editor.toPlainText() == "keep"
    assert output.toPlainText() == "leave\n"
    assert window.windowTitle() == "Galley"
    assert load_recent_files() == []
    assert window._actions.open_file.isEnabled()
    qtbot.waitUntil(lambda: editor.hasFocus())


def _fail_reading(monkeypatch, path: Path, kind: str) -> None:
    real_read = Path.read_bytes
    error = (
        PermissionError(13, "Permission denied")
        if kind == "permission"
        else OSError(5, "Input/output error")
    )

    def _fail(self: Path) -> bytes:
        if self == path:
            raise error
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _fail)


def _default_folder() -> str:
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    if documents and Path(documents).is_dir():
        return documents
    return str(Path.home())


def _drag_enter(target: QWidget, paths: list[Path]) -> QDragEnterEvent:
    # The event does not own its mime data, so keep it alive until sent.
    mime = _file_mime(paths)
    event = QDragEnterEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, event)
    return event


def _drop(target: QWidget, paths: list[Path]) -> QDropEvent:
    mime = _file_mime(paths)
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, event)
    return event


def _file_mime(paths: list[Path]) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def _left_edge(pane: PaneWidget) -> QColor:
    image = pane.grab().toImage()
    return image.pixelColor(0, image.height() // 2)


def _strongest_contrast(image: QImage, background: QColor) -> float:
    return max(
        contrast_ratio(image.pixelColor(x, y), background)
        for y in range(image.height())
        for x in range(image.width())
    )


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
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _input_pane(window) -> PaneWidget:
    pane = window.findChild(PaneWidget, "inputPane")
    assert isinstance(pane, PaneWidget)
    return pane


def _output(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _status(window) -> QLabel:
    status = window.findChild(QLabel, "statusMessage")
    assert isinstance(status, QLabel)
    return status


def _spinner(window) -> QProgressBar:
    spinner = window.findChild(QProgressBar, "formatSpinner")
    assert isinstance(spinner, QProgressBar)
    return spinner


def _recent_menu(window) -> QMenu:
    menu = window.findChild(QMenu, "menuOpenRecent")
    assert isinstance(menu, QMenu)
    return menu


def _menu_entries(menu: QMenu) -> list[QAction]:
    return [item for item in menu.actions() if not item.isSeparator()]


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
