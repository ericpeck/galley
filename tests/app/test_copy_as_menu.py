"""Copy As › Original, Masked and Random fake (#66). All data here is fake."""

import functools
import json
from collections import Counter

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible, QAction, QColor, QTextCursor
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMenu
from ruamel.yaml import YAML

import galley.app.main_window as main_window_module
import galley.app.parse_session as parse_session_module
from galley.app.actions import UNBUILT_ACTIONS, is_unbuilt
from galley.app.copy_as import (
    COPIED_ORIGINAL,
    COPY_AS_BUSY_MESSAGE,
    COPY_AS_BUSY_NAME,
    COPY_AS_FAILED_MESSAGE,
    COPY_AS_PROGRESS_NAME,
    COPY_CANCELED_MESSAGE,
    MASK_HELP,
    copied_text,
    copy_as_inline_limit,
)
from galley.app.editor import CodeEditor
from galley.app.limits import JSON_COPY_AS_INLINE_BYTES, YAML_COPY_AS_INLINE_BYTES
from galley.app.main import create_main_window
from galley.app.parse_session import ParseSession
from galley.app.theme import contrast_ratio
from galley.core.format import format_document
from galley.core.mask import MASK, CopyAsError, copy_as_text
from galley.core.worker import CopyAsFailureEvent, CopyAsJob

_SPAWN_TIMEOUT_MS = 20000
_LABELS = ["Original", "Masked", "Random fake"]
_SOURCE = """{
  "id": 4021,
  "name": "Pat Example",
  "email": "pat.example@example.com",
  "profile": {"phone": "555-0199", "address": {"street": "100 Example Street"}},
  "contacts": [{"label": "work", "workEmail": "work.desk@example.com"}],
  "api_key": "fake-not-a-real-token",
  "tags": ["alpha", "beta"]
}"""
_HIDDEN = (
    "Pat Example",
    "pat.example@example.com",
    "555-0199",
    "100 Example Street",
    "work.desk@example.com",
    "fake-not-a-real-token",
)
_HIDDEN_COUNT = 6
_UNTOUCHED = "clipboard before Copy As"


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _editors(window) -> tuple[CodeEditor, CodeEditor]:
    found = []
    for name in ("inputEditor", "outputEditor"):
        editor = window.findChild(CodeEditor, name)
        assert isinstance(editor, CodeEditor), name
        found.append(editor)
    return found[0], found[1]


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _copy_as_menu(window) -> QMenu:
    menu = window.findChild(QMenu, "menuCopyAs")
    assert isinstance(menu, QMenu)
    return menu


def _labels(menu: QMenu) -> list[str]:
    return ["|" if item.isSeparator() else item.text() for item in menu.actions()]


def _with_output(qtbot, fmt: str = "JSON", text: str | None = None):
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "outputFormat")
    assert isinstance(picker, QComboBox)
    picker.setCurrentText(fmt)
    _source, output = _editors(window)
    if text is None:
        text = format_document(_SOURCE, "json", fmt)
    output.setPlainText(text)
    QApplication.clipboard().setText(_UNTOUCHED)
    return window, output


def _copy(window, label: str) -> str:
    _action(window, label).trigger()
    return QApplication.clipboard().text()


def _load(text: str, fmt: str) -> object:
    if fmt == "JSON":
        return json.loads(text)
    return YAML(typ="safe", pure=True).load(text)


def test_copy_as_is_in_the_edit_menu_and_the_output_menu(qtbot) -> None:
    window = _show(qtbot)
    menu = _copy_as_menu(window)
    assert menu.title() == "Copy As"
    assert _labels(menu) == _LABELS
    assert "Placeholders" not in _labels(menu)
    edit = window.findChild(QMenu, "menuEdit")
    assert isinstance(edit, QMenu)
    assert menu.parent() is edit
    edit_labels = _labels(edit)
    assert edit_labels.index("Copy As") == edit_labels.index("Copy") + 1
    output_menu = window.findChild(QMenu, "outputEditorMenu")
    assert isinstance(output_menu, QMenu)
    output_labels = _labels(output_menu)
    assert output_labels.index("Copy As") == output_labels.index("Copy") + 1
    assert menu.menuAction() in output_menu.actions()
    input_menu = window.findChild(QMenu, "editorMenu")
    assert isinstance(input_menu, QMenu)
    assert "Copy As" not in _labels(input_menu)


def test_masked_and_random_fake_carry_the_help_text(qtbot) -> None:
    window = _show(qtbot)
    assert _copy_as_menu(window).toolTipsVisible()
    for label in ("Masked", "Random fake"):
        assert _action(window, label).toolTip() == MASK_HELP
        # The macOS menu bar shows no tooltips; the status bar shows this.
        assert _action(window, label).statusTip() == MASK_HELP
    assert _action(window, "Original").toolTip() != MASK_HELP
    assert _action(window, "Original").statusTip() == ""


def test_hovering_masked_shows_the_help_in_the_status_bar(qtbot) -> None:
    window, _output = _with_output(qtbot)
    edit = window.findChild(QMenu, "menuEdit")
    assert isinstance(edit, QMenu)
    submenu = _open_copy_as_from(qtbot, window, edit)
    qtbot.keyClick(submenu, Qt.Key.Key_Down)
    assert submenu.activeAction() is _action(window, "Masked")
    qtbot.waitUntil(lambda: window.statusBar().currentMessage() == MASK_HELP)
    submenu.close()
    edit.close()


def test_copy_as_is_built_and_not_on_either_release_list(qtbot) -> None:
    window = _show(qtbot)
    actions = window._actions
    items = list(actions.copy_as_actions().values())
    assert [item.text() for item in items] == _LABELS
    assert not {"Copy As", *_LABELS} & set(UNBUILT_ACTIONS)
    pending = {id(item) for item in [*actions.stubs(), *actions._not_yet_actions()]}
    for item in items:
        assert not is_unbuilt(item)
        assert id(item) not in pending
        assert item.toolTip() != "Coming soon"


def test_copy_as_is_disabled_while_output_is_empty(qtbot) -> None:
    window = _show(qtbot)
    _source, output = _editors(window)
    menu = _copy_as_menu(window)
    items = [_action(window, label) for label in _LABELS]
    assert not menu.menuAction().isEnabled()
    assert not any(item.isEnabled() for item in items)
    QApplication.clipboard().setText(_UNTOUCHED)
    items[0].trigger()
    assert QApplication.clipboard().text() == _UNTOUCHED
    output.setPlainText("{}\n")
    assert menu.menuAction().isEnabled()
    assert all(item.isEnabled() for item in items)
    output.setPlainText("")
    assert not menu.menuAction().isEnabled()
    assert not any(item.isEnabled() for item in items)


def test_copy_as_is_disabled_while_format_runs(qtbot, monkeypatch) -> None:
    window, _output = _with_output(qtbot)
    source, _ = _editors(window)
    source.setPlainText('{"a": 1}')
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    _action(window, "Format").trigger()
    assert not _copy_as_menu(window).menuAction().isEnabled()
    for label in _LABELS:
        assert not _action(window, label).isEnabled()
    window._cancel_format()
    assert _copy_as_menu(window).menuAction().isEnabled()
    assert all(_action(window, label).isEnabled() for label in _LABELS)


@pytest.mark.parametrize("fmt", ["JSON", "YAML"])
def test_original_copies_the_unmasked_text(qtbot, fmt: str) -> None:
    window, output = _with_output(qtbot, fmt)
    shown = output.toPlainText()
    # Original's source is the unmasked text (#66). It must stay so when
    # Output can show masked values.
    assert window._copy_as_source() == shown
    copied = _copy(window, "Original")
    assert copied == shown
    for hidden in _HIDDEN:
        assert hidden in copied
    assert MASK not in copied
    assert window._status.state_text() == COPIED_ORIGINAL
    assert output.toPlainText() == shown


@pytest.mark.parametrize("fmt", ["JSON", "YAML"])
def test_masked_copies_the_output_with_matched_values_hidden(qtbot, fmt) -> None:
    window, output = _with_output(qtbot, fmt)
    shown = output.toPlainText()
    undo = output.document().isUndoAvailable()
    copied = _copy(window, "Masked")
    for hidden in _HIDDEN:
        assert hidden not in copied
    value = _load(copied, fmt)
    assert isinstance(value, dict)
    assert value["email"] == MASK
    assert value["profile"] == {"phone": MASK, "address": {"street": MASK}}
    assert value["contacts"] == [{"label": "work", "workEmail": MASK}]
    assert value["tags"] == ["alpha", "beta"]
    assert value["id"] == 4021
    assert copied.count("\n") == shown.count("\n")
    assert window._status.state_text() == copied_text("masked", _HIDDEN_COUNT)
    assert window._status.state_text() == "Copied masked (6 values hidden)"
    assert output.toPlainText() == shown
    assert output.document().isUndoAvailable() == undo


@pytest.mark.parametrize("fmt", ["JSON", "YAML"])
def test_random_fake_copies_new_values_of_the_same_type(qtbot, fmt) -> None:
    window, output = _with_output(qtbot, fmt)
    shown = output.toPlainText()
    first = _copy(window, "Random fake")
    second = _copy(window, "Random fake")
    assert first != second
    original = _load(shown, fmt)
    for copied in (first, second):
        for hidden in _HIDDEN:
            assert hidden not in copied
        assert MASK not in copied
        value = _load(copied, fmt)
        assert isinstance(value, dict) and isinstance(original, dict)
        assert value.keys() == original.keys()
        assert value["email"].endswith("@example.com")
        assert type(value["profile"]["address"]["street"]) is str
        assert value["tags"] == ["alpha", "beta"]
        assert copied.count("\n") == shown.count("\n")
    assert window._status.state_text() == "Copied random fake (6 values replaced)"
    assert output.toPlainText() == shown


def test_one_value_reads_in_the_singular() -> None:
    assert copied_text("masked", 1) == "Copied masked (1 value hidden)"
    assert copied_text("fake", 1) == "Copied random fake (1 value replaced)"
    assert copied_text("masked", 0) == "Copied masked (0 values hidden)"
    assert copied_text("masked", 1200) == "Copied masked (1,200 values hidden)"


def _select(output: CodeEditor, start_text: str, end_text: str) -> None:
    text = output.toPlainText()
    start = text.index(start_text)
    end = text.index(end_text, start) + len(end_text)
    cursor = output.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)


def test_a_selection_copies_only_the_selected_value(qtbot) -> None:
    text = (
        '{\n  "id": 7,\n  "address": {\n'
        '    "street": "100 Example Street",\n    "unit": 7\n  }\n}\n'
    )
    window, output = _with_output(qtbot, "JSON", text)
    _select(output, '{\n    "street"', "  }")
    masked = _copy(window, "Masked")
    assert json.loads(masked) == {"street": MASK, "unit": 7}
    assert window._status.state_text() == "Copied masked (1 value hidden)"
    original = _copy(window, "Original")
    assert original == output.textCursor().selection().toPlainText()
    assert " " not in original
    assert output.toPlainText() == text


def test_a_selection_that_is_not_a_whole_value_copies_nothing(qtbot) -> None:
    window, output = _with_output(qtbot, "JSON")
    _select(output, '"email"', '.com"')
    for label in ("Masked", "Random fake"):
        assert _copy(window, label) == _UNTOUCHED
        assert window._status.state_text() == COPY_AS_FAILED_MESSAGE
    assert window._status.state_text() == (
        "Nothing copied. Galley couldn't start the copy. Try again."
    )


def test_output_that_does_not_parse_copies_nothing(qtbot) -> None:
    broken = '{"email": "pat@example.com",\n'
    window, output = _with_output(qtbot, "JSON", broken)
    assert _copy(window, "Masked") == _UNTOUCHED
    assert window._status.state_text() == COPY_AS_FAILED_MESSAGE
    assert _copy(window, "Original") == broken
    assert output.toPlainText() == broken


def test_a_masking_failure_copies_nothing(qtbot) -> None:
    text = "email: !!pairs\n- a: pat@example.com\n"
    window, _output = _with_output(qtbot, "YAML", text)
    assert _copy(window, "Random fake") == _UNTOUCHED
    assert window._status.state_text() == COPY_AS_FAILED_MESSAGE


_SECRET = "fake-not-a-real-token"
# Invalid YAML whose parse error quotes the line with a fake secret on it.
_BROKEN_YAML = f"name: Pat Example\napi_key: [{_SECRET}\nemail: pat@example.com\n"


def _status_texts(window) -> list[str]:
    """Everything the status bar shows or reads out, temporary tips included."""
    label = window.findChild(QLabel, "statusMessage")
    assert isinstance(label, QLabel)
    return [
        window._status.state_text(),
        label.text(),
        label.toolTip(),
        label.accessibleName(),
        label.accessibleDescription(),
        window.statusBar().currentMessage(),
    ]


def test_the_yaml_error_is_never_shown(qtbot) -> None:
    with pytest.raises(CopyAsError) as caught:
        copy_as_text(_BROKEN_YAML, "yaml", "masked")
    assert _SECRET in str(caught.value)
    window, output = _with_output(qtbot, "YAML", _BROKEN_YAML)
    for label in ("Masked", "Random fake"):
        assert _copy(window, label) == _UNTOUCHED
        assert window._status.state_text() == COPY_AS_FAILED_MESSAGE
        for text in _status_texts(window):
            assert _SECRET not in text
            assert "pat@example.com" not in text
    assert output.toPlainText() == _BROKEN_YAML


def test_the_yaml_error_is_never_shown_from_the_worker(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "copy_as_inline_limit", lambda _fmt: 0)
    window, _output = _with_output(qtbot, "YAML", _BROKEN_YAML)
    try:
        _action(window, "Masked").trigger()
        assert window._copy_as_job is not None
        qtbot.waitUntil(lambda: window._copy_as_job is None, timeout=_SPAWN_TIMEOUT_MS)
        assert QApplication.clipboard().text() == _UNTOUCHED
        assert window._status.state_text() == COPY_AS_FAILED_MESSAGE
        for text in _status_texts(window):
            assert _SECRET not in text
    finally:
        window.shutdown_parse_worker()


def test_only_a_tiny_output_is_copied_on_the_ui_thread(qtbot) -> None:
    assert copy_as_inline_limit("JSON") == JSON_COPY_AS_INLINE_BYTES == 32_000
    assert copy_as_inline_limit("YAML") == YAML_COPY_AS_INLINE_BYTES == 2_000
    rows = [
        {"email": f"user{index}@example.com", "phone": "555-0142"}
        for index in range(800)
    ]
    text = json.dumps(rows, indent=2)
    assert len(text.encode()) > JSON_COPY_AS_INLINE_BYTES
    window, _output = _with_output(qtbot, "JSON", text)
    try:
        _action(window, "Masked").trigger()
        assert window._copy_as_job is not None
        assert QApplication.clipboard().text() == _UNTOUCHED
        qtbot.waitUntil(lambda: window._copy_as_job is None, timeout=_SPAWN_TIMEOUT_MS)
        copied = QApplication.clipboard().text()
        assert "@example.com" not in copied
        assert window._status.state_text() == "Copied masked (1,600 values hidden)"
    finally:
        window.shutdown_parse_worker()


def test_a_large_output_is_copied_on_the_worker(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "copy_as_inline_limit", lambda _fmt: 0)
    window, output = _with_output(qtbot, "YAML")
    source, _ = _editors(window)
    source.setPlainText('{"a": 1}')
    shown = output.toPlainText()
    try:
        _action(window, "Masked").trigger()
        assert window._copy_as_job is not None
        assert QApplication.clipboard().text() == _UNTOUCHED
        assert not _action(window, "Format").isEnabled()
        assert not _copy_as_menu(window).menuAction().isEnabled()
        window._show_copying()
        spinner = window._status._spinner
        assert spinner.isVisible()
        assert spinner.accessibleName() == COPY_AS_BUSY_NAME
        assert window._status.state_text() == COPY_AS_BUSY_MESSAGE
        qtbot.waitUntil(
            lambda: QApplication.clipboard().text() != _UNTOUCHED,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        copied = QApplication.clipboard().text()
        for hidden in _HIDDEN:
            assert hidden not in copied
        assert _load(copied, "YAML")["email"] == MASK  # type: ignore[index]
        assert window._copy_as_job is None
        assert not spinner.isVisible()
        assert window._status.state_text() == "Copied masked (6 values hidden)"
        assert _action(window, "Format").isEnabled()
        assert _copy_as_menu(window).menuAction().isEnabled()
        assert output.toPlainText() == shown
    finally:
        window.shutdown_parse_worker()


def test_a_worker_that_stops_mid_copy_copies_nothing(qtbot) -> None:
    window, _output = _with_output(qtbot)
    window._copy_as_job = 41
    window._sync_format_button()
    assert not _action(window, "Masked").isEnabled()
    stale = CopyAsFailureEvent(40, False)
    window._session.copy_as_failed.emit(40, stale)
    assert window._copy_as_job == 41
    stopped = CopyAsFailureEvent(41, False)
    window._session.copy_as_failed.emit(41, stopped)
    assert window._copy_as_job is None
    assert QApplication.clipboard().text() == _UNTOUCHED
    assert window._status.state_text() == COPY_AS_FAILED_MESSAGE
    assert _action(window, "Masked").isEnabled()


def _open_copy_as_from(qtbot, window, menu: QMenu) -> QMenu:
    """Open ``menu`` and reach Copy As with the keyboard."""
    menu.popup(window.mapToGlobal(QPoint(40, 40)))
    qtbot.waitUntil(menu.isVisible)
    submenu = _copy_as_menu(window)
    while menu.activeAction() is not submenu.menuAction():
        qtbot.keyClick(menu, Qt.Key.Key_Down)
    qtbot.keyClick(menu, Qt.Key.Key_Right)
    qtbot.waitUntil(submenu.isVisible)
    return submenu


@pytest.mark.parametrize("menu_name", ["menuEdit", "outputEditorMenu"])
def test_copy_as_works_from_the_keyboard(qtbot, menu_name: str) -> None:
    window, output = _with_output(qtbot)
    menu = window.findChild(QMenu, menu_name)
    assert isinstance(menu, QMenu)
    submenu = _open_copy_as_from(qtbot, window, menu)
    assert submenu.activeAction() is _action(window, "Original")
    qtbot.keyClick(submenu, Qt.Key.Key_Down)
    assert submenu.activeAction() is _action(window, "Masked")
    qtbot.keyClick(submenu, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: QApplication.clipboard().text() != _UNTOUCHED)
    assert "pat.example@example.com" not in QApplication.clipboard().text()
    assert window._status.state_text() == "Copied masked (6 values hidden)"
    assert output.toPlainText() != QApplication.clipboard().text()


def test_copy_as_has_accessible_names(qtbot) -> None:
    window, _output = _with_output(qtbot)
    edit = window.findChild(QMenu, "menuEdit")
    assert isinstance(edit, QMenu)
    submenu = _open_copy_as_from(qtbot, window, edit)
    parent = QAccessible.queryAccessibleInterface(edit)
    assert parent is not None
    names = [
        parent.child(index).text(QAccessible.Text.Name)
        for index in range(parent.childCount())
    ]
    assert "Copy As" in names
    menu = QAccessible.queryAccessibleInterface(submenu)
    assert menu is not None
    names = [
        menu.child(index).text(QAccessible.Text.Name)
        for index in range(menu.childCount())
    ]
    assert names == _LABELS
    submenu.close()
    edit.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_copy_as_menu_text_is_readable(qtbot, install_scheme, scheme) -> None:
    window, _output = _with_output(qtbot)
    palette = install_scheme(scheme)
    edit = window.findChild(QMenu, "menuEdit")
    assert isinstance(edit, QMenu)
    submenu = _open_copy_as_from(qtbot, window, edit)
    submenu.setActiveAction(None)  # type: ignore[arg-type]
    qtbot.wait(20)
    image = submenu.grab().toImage()
    pixels = [
        QColor(image.pixel(x, y))
        for x in range(image.width())
        for y in range(image.height())
    ]
    fill = QColor(Counter(color.rgb() for color in pixels).most_common(1)[0][0])
    assert (fill.lightness() < 128) == (scheme == "dark")
    assert fill.name() == palette.window().color().name()
    ink = max(contrast_ratio(color, fill) for color in pixels)
    assert ink >= 4.5
    submenu.close()
    edit.close()


def _held_copy(qtbot, monkeypatch):
    """A window whose next Masked or Random fake copy waits on the worker.

    The job sleeps 30 s before building the copy, so a test can cancel it
    mid-copy. Cancel and the progress bar show at once.
    """
    monkeypatch.setattr(main_window_module, "copy_as_inline_limit", lambda _fmt: 0)
    monkeypatch.setattr(main_window_module, "SPINNER_REVEAL_MS", 0)
    monkeypatch.setattr(main_window_module, "PROGRESS_BAR_REVEAL_MS", 0)
    monkeypatch.setattr(
        parse_session_module, "CopyAsJob", functools.partial(CopyAsJob, hold_s=30.0)
    )
    window, output = _with_output(qtbot, "YAML")
    source, _ = _editors(window)
    source.setPlainText('{"email": "pat@example.com", "phone": "555-0142"}')
    return window, output


def _wait_until_copying(qtbot, window) -> int:
    """Wait for the job to reach the worker. Return the worker's pid."""
    session = window._session
    qtbot.waitUntil(
        lambda: (
            session.process is not None
            and session.process.pid is not None
            and not session.has_pending_job
        ),
        timeout=_SPAWN_TIMEOUT_MS,
    )
    cancel = window._status._cancel
    qtbot.waitUntil(cancel.isVisible)
    assert window._status._progress.accessibleName() == COPY_AS_PROGRESS_NAME
    assert window._status.state_text() == COPY_AS_BUSY_MESSAGE
    return session.process.pid


def _assert_canceled(window, output: CodeEditor, shown: str) -> None:
    assert window._copy_as_job is None
    assert QApplication.clipboard().text() == _UNTOUCHED
    assert window._status.state_text() == COPY_CANCELED_MESSAGE
    assert window._status.state_text() == "Copy canceled. Nothing copied."
    assert not window._status._cancel.isVisible()
    assert not window._status._progress.isVisible()
    assert output.toPlainText() == shown
    assert _copy_as_menu(window).menuAction().isEnabled()
    assert _action(window, "Format").isEnabled()


def _format_on_a_fresh_worker(qtbot, window, output: CodeEditor, old_pid: int):
    _action(window, "Format").trigger()
    expected = format_document(
        '{"email": "pat@example.com", "phone": "555-0142"}', "json", "yaml"
    )
    qtbot.waitUntil(lambda: output.toPlainText() == expected, timeout=_SPAWN_TIMEOUT_MS)
    process = window._session.process
    assert process is not None and process.pid != old_pid
    assert QApplication.clipboard().text() == _UNTOUCHED


def test_cancel_stops_a_large_copy_and_copies_nothing(qtbot, monkeypatch) -> None:
    window, output = _held_copy(qtbot, monkeypatch)
    shown = output.toPlainText()
    try:
        _action(window, "Random fake").trigger()
        pid = _wait_until_copying(qtbot, window)
        assert not _action(window, "Format").isEnabled()
        qtbot.mouseClick(window._status._cancel, Qt.MouseButton.LeftButton)
        _assert_canceled(window, output, shown)
        _format_on_a_fresh_worker(qtbot, window, output, pid)
        assert window._status.state_text() != COPY_CANCELED_MESSAGE
    finally:
        window.shutdown_parse_worker()


def test_esc_stops_a_large_copy_started_from_the_keyboard(qtbot, monkeypatch):
    window, output = _held_copy(qtbot, monkeypatch)
    shown = output.toPlainText()
    edit = window.findChild(QMenu, "menuEdit")
    assert isinstance(edit, QMenu)
    try:
        submenu = _open_copy_as_from(qtbot, window, edit)
        qtbot.keyClick(submenu, Qt.Key.Key_Down)
        assert submenu.activeAction() is _action(window, "Masked")
        qtbot.keyClick(submenu, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: not submenu.isVisible() and not edit.isVisible())
        pid = _wait_until_copying(qtbot, window)
        output.setFocus()
        qtbot.keyClick(output, Qt.Key.Key_Escape)
        _assert_canceled(window, output, shown)
        _format_on_a_fresh_worker(qtbot, window, output, pid)
    finally:
        window.shutdown_parse_worker()


def test_esc_with_no_copy_running_changes_nothing(qtbot) -> None:
    window, output = _with_output(qtbot)
    shown = output.toPlainText()
    _copy(window, "Masked")
    status = window._status.state_text()
    qtbot.keyClick(output, Qt.Key.Key_Escape)
    assert window._status.state_text() == status
    assert output.toPlainText() == shown
