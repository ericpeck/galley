"""Sort keys remembers its state between launches (#59 follow-up).

Settings live in the throwaway store from ``isolated_settings``.
"""

import json
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QMenu, QToolButton, QWidget

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import SORTED_KEYS_SUFFIX
from galley.app.problems_settings import load_sort_keys, problems_settings
from galley.core.format import format_json

_SPAWN_TIMEOUT_MS = 20000
_UNSORTED = '{"b": 1, "a": {"d": 2, "c": [3, 1]}}'
_KEY = "format/sortKeys"


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _sort_button(window) -> QToolButton:
    buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is window._actions.sort_keys
    ]
    assert len(buttons) == 1
    return buttons[0]


def _overflow_entry(qtbot, window):
    """Narrow the window until Sort keys moves into the » menu. Return its entry."""
    toolbar = window.findChild(QWidget, "mainToolbar")
    menu = window.findChild(QMenu, "toolbarOverflowMenu")
    assert toolbar is not None
    assert isinstance(menu, QMenu)
    action = window._actions.sort_keys
    window.setMinimumWidth(280)
    width = 1024
    while width > 280:
        window.resize(width, 800)
        qtbot.waitUntil(lambda: toolbar.width() == window.width())
        entries = [item for item in menu.actions() if item is action]
        if entries:
            return entries[0]
        width -= 20
    raise AssertionError("Sort keys never moved into the overflow menu")


def _first_format(qtbot, window) -> tuple[str, str]:
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert isinstance(status, QLabel)
    editor.setPlainText(_UNSORTED)
    window._actions.format.trigger()
    qtbot.waitUntil(
        lambda: output.toPlainText() != "" and not window._formatting,
        timeout=_SPAWN_TIMEOUT_MS,
    )
    return output.toPlainText(), status.text()


def _restart(qtbot, window):
    window.close()
    return _show(qtbot)


def _next_launch(tmp_path: Path) -> None:
    """Point settings at a copy of the INI file, as a new launch reads it.

    Qt keeps the values this process wrote in memory, typed as written, so
    reading the same file back hands out a bool for a bool. A copy at a new
    path is parsed from disk, where every value is text (#127).
    """
    written = Path(problems_settings().fileName())
    store = tmp_path / "next-launch"
    if written.exists():
        # <store>/<organization>/<application>.ini, as Qt lays it out.
        copy = store / written.parent.name / written.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(written, copy)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(store))


def test_fresh_settings_default_to_off(qtbot) -> None:
    assert problems_settings().value(_KEY) is None
    assert load_sort_keys() is False
    window = _show(qtbot)
    assert not window._actions.sort_keys.isChecked()
    assert not _sort_button(window).isChecked()
    output, status = _first_format(qtbot, window)
    assert output == format_json(_UNSORTED)
    assert status == "Valid JSON"


def test_flip_is_saved_at_once(qtbot) -> None:
    window = _show(qtbot)
    action = window._actions.sort_keys
    action.setChecked(True)
    assert load_sort_keys() is True
    assert problems_settings().value(_KEY) in (True, "true")
    action.setChecked(False)
    assert load_sort_keys() is False
    assert problems_settings().value(_KEY) in (False, "false")
    action.trigger()
    assert load_sort_keys() is True


def test_on_survives_a_restart(qtbot) -> None:
    window = _show(qtbot)
    window._actions.sort_keys.setChecked(True)
    window = _restart(qtbot, window)
    action = window._actions.sort_keys
    assert action.isChecked()
    assert _sort_button(window).isChecked()
    output, status = _first_format(qtbot, window)
    assert output == format_json(_UNSORTED, sort_keys=True)
    assert list(json.loads(output)) == ["a", "b"]
    assert status == f"Valid JSON {SORTED_KEYS_SUFFIX}"
    entry = _overflow_entry(qtbot, window)
    assert entry.isCheckable()
    assert entry.isChecked()


def test_off_survives_a_restart(qtbot) -> None:
    window = _show(qtbot)
    window._actions.sort_keys.setChecked(True)
    window = _restart(qtbot, window)
    assert window._actions.sort_keys.isChecked()
    window._actions.sort_keys.setChecked(False)
    window = _restart(qtbot, window)
    action = window._actions.sort_keys
    assert not action.isChecked()
    assert not _sort_button(window).isChecked()
    output, status = _first_format(qtbot, window)
    assert output == format_json(_UNSORTED)
    assert list(json.loads(output)) == ["b", "a"]
    assert status == "Valid JSON"
    entry = _overflow_entry(qtbot, window)
    assert entry.isCheckable()
    assert not entry.isChecked()


def test_restoring_on_does_not_save_or_flash(qtbot, monkeypatch) -> None:
    """The restore sets the state before the window shows, without a save."""
    problems_settings().setValue(_KEY, True)
    problems_settings().sync()
    saved: list[bool] = []
    monkeypatch.setattr(
        "galley.app.main_window.save_sort_keys", lambda value: saved.append(value)
    )
    window = create_main_window()
    qtbot.addWidget(window)
    assert not window.isVisible()
    assert window._actions.sort_keys.isChecked()
    assert _sort_button(window).isChecked()
    assert saved == []


@pytest.mark.parametrize("saved", [False, True])
def test_keyboard_and_accessible_names_are_unchanged(qtbot, saved: bool) -> None:
    problems_settings().setValue(_KEY, saved)
    problems_settings().sync()
    window = _show(qtbot)
    action = window._actions.sort_keys
    button = _sort_button(window)
    assert action.isChecked() is saved
    assert action.text() == "Sort keys"
    assert action.shortcut().isEmpty()
    assert action.objectName() == "sortKeys"
    assert button.accessibleName() in ("", "Sort keys")
    assert button.text() == "Sort keys"


_MISSING = object()


@pytest.mark.parametrize(("raw", "restored"), [(1, True), (2, True), (0, False)])
def test_integer_stored_value_restores(raw: int, restored: bool) -> None:
    """A store that keeps types, like this process's own cache, can hand out an
    integer; nonzero is on (#127)."""
    problems_settings().setValue(_KEY, raw)
    stored = problems_settings().value(_KEY)
    assert isinstance(stored, int)
    assert stored == raw
    assert load_sort_keys() is restored


@pytest.mark.parametrize(
    ("raw", "restored"),
    [
        ("true", True),
        ("false", False),
        # Not a value Galley writes: the fallback is off, the default.
        ("maybe", False),
        # Other forms _as_bool accepts (#127).
        (" TRUE ", True),
        ("1", True),
        ("yes", True),
        (True, True),
        (False, False),
        (_MISSING, False),
    ],
    ids=[
        "str-true",
        "str-false",
        "str-maybe",
        "str-padded-upper-true",
        "str-1",
        "str-yes",
        "bool-true",
        "bool-false",
        "missing",
    ],
)
def test_raw_stored_value_restores_at_launch(
    qtbot, tmp_path: Path, raw: object, restored: bool
) -> None:
    """Windows QSettings can hand values back as strings; each form restores.

    Every case is read from the file, so a bool comes back as text too.
    """
    if raw is not _MISSING:
        settings = problems_settings()
        settings.setValue(_KEY, raw)
        settings.sync()
    _next_launch(tmp_path)
    if raw is not _MISSING:
        expected_text = str(raw).lower() if isinstance(raw, bool) else raw
        assert problems_settings().value(_KEY) == expected_text
    assert load_sort_keys() is restored
    window = _show(qtbot)
    assert window._actions.sort_keys.isChecked() is restored
    assert _sort_button(window).isChecked() is restored
    output, status = _first_format(qtbot, window)
    assert output == format_json(_UNSORTED, sort_keys=restored)
    expected = f"Valid JSON {SORTED_KEYS_SUFFIX}" if restored else "Valid JSON"
    assert status == expected
    entry = _overflow_entry(qtbot, window)
    assert entry.isChecked() is restored
    if raw is _MISSING:
        assert problems_settings().value(_KEY) is None
