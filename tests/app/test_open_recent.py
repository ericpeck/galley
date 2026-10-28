"""File › Open Recent lists opened files and reopens them through Open (#85)."""

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QThreadPool
from PySide6.QtGui import QAccessible, QAction, QColor, QImage, QPalette
from PySide6.QtWidgets import QLabel, QMenu, QMessageBox

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problems_settings import problems_settings
from galley.app.recent_files import (
    RECENT_FILES_KEY,
    load_recent_files,
    remember_recent_file,
)
from galley.app.theme import contrast_ratio

_SAMPLE = '{"a":1}'


def test_file_menu_has_open_recent_after_open(qtbot) -> None:
    window = _show(qtbot)

    file_menu = window.findChild(QMenu, "menuFile")
    assert isinstance(file_menu, QMenu)
    assert [action.text() for action in file_menu.actions()] == [
        "Open file…",
        "Open Recent",
    ]
    recent = _recent(window)
    assert file_menu.actions()[1].menu() is recent
    assert _labels(recent) == ["Clear Menu"]
    assert not _clear(window).isEnabled()


def test_opening_a_file_adds_it_first(qtbot, tmp_path, monkeypatch) -> None:
    first = _write(tmp_path / "first.json")
    second = _write(tmp_path / "second.yaml", "a: 1\n")
    window = _show(qtbot)

    _open_with_dialog(qtbot, monkeypatch, window, first, _SAMPLE)
    # Input still matches first.json, so no Replace prompt.
    _open_with_dialog(qtbot, monkeypatch, window, second, "a: 1\n")

    assert _labels(_recent(window)) == ["second.yaml", "first.json", "|", "Clear Menu"]
    assert load_recent_files() == [str(second), str(first)]
    assert _clear(window).isEnabled()


def test_reopening_a_file_moves_it_to_the_top_once(
    qtbot, tmp_path, monkeypatch
) -> None:
    a = _write(tmp_path / "a.json")
    b = _write(tmp_path / "b.json")
    remember_recent_file(a)
    remember_recent_file(b)
    window = _show(qtbot)
    assert _labels(_recent(window))[:2] == ["b.json", "a.json"]

    _open_with_dialog(qtbot, monkeypatch, window, a, _SAMPLE)

    assert _labels(_recent(window)) == ["a.json", "b.json", "|", "Clear Menu"]


def test_the_menu_keeps_ten_files(qtbot, tmp_path) -> None:
    paths = [_write(tmp_path / f"file{index}.json") for index in range(12)]
    for path in paths:
        remember_recent_file(path)
    window = _show(qtbot)

    names = _labels(_recent(window))[:-2]

    assert names == [f"file{index}.json" for index in range(11, 1, -1)]


def test_matching_names_show_their_folder_and_paths_stay_in_tooltips(
    qtbot, tmp_path
) -> None:
    staging = _write(tmp_path / "staging" / "data.json")
    prod = _write(tmp_path / "prod" / "data.json")
    remember_recent_file(staging)
    remember_recent_file(prod)
    window = _show(qtbot)

    entries = _entries(window)

    assert [action.text() for action in entries] == [
        "data.json — prod",
        "data.json — staging",
    ]
    assert [Path(action.toolTip()) for action in entries] == [prod, staging]
    assert _recent(window).toolTipsVisible()
    for action in entries:
        assert str(tmp_path) not in action.text()
        assert action.statusTip() == ""


def test_an_ampersand_in_a_name_shows_as_typed(qtbot, tmp_path) -> None:
    remember_recent_file(_write(tmp_path / "R&D.json"))
    window = _show(qtbot)

    (entry,) = _entries(window)

    assert entry.text() == "R&&D.json"
    assert entry.iconText() == "R&D.json"


def test_choosing_an_entry_opens_it_through_open(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "again.json")
    remember_recent_file(path)
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("typed")

    _entries(window)[0].trigger()

    # Unsaved Input gets the same Replace prompt as File › Open.
    box = _require(window, "replaceInput")
    assert box.text() == "Replace the current input with again.json?"
    default = box.defaultButton()
    assert default is not None
    default.click()
    qtbot.waitUntil(lambda: editor.toPlainText() == _SAMPLE)
    assert window.windowTitle() == "again.json — Galley"
    assert _status(window).text() == "Opened again.json (7 B)"


def test_cancel_on_the_replace_prompt_keeps_the_input(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "again.json")
    remember_recent_file(path)
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("typed")

    _entries(window)[0].trigger()
    box = _require(window, "replaceInput")
    box.reject()

    qtbot.waitUntil(lambda: _message(window, "replaceInput") is None)
    assert editor.toPlainText() == "typed"
    assert window._loading_path is None


def test_a_missing_file_stays_listed_but_disabled(qtbot, tmp_path) -> None:
    kept = _write(tmp_path / "kept.json")
    gone = _write(tmp_path / "gone.json")
    remember_recent_file(gone)
    remember_recent_file(kept)
    gone.unlink()
    window = _show(qtbot)

    kept_action, gone_action = _entries(window)

    # Dimming is not the only cue: the text says so too.
    assert gone_action.text() == "gone.json (not found)"
    assert not gone_action.isEnabled()
    assert gone_action.toolTip() == "File not found"
    assert kept_action.isEnabled()
    assert load_recent_files() == [str(kept), str(gone)]


def test_the_menu_rechecks_files_each_time_it_opens(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "flaky.json")
    remember_recent_file(path)
    window = _show(qtbot)
    recent = _recent(window)
    assert _entries(window)[0].isEnabled()

    path.unlink()
    recent.aboutToShow.emit()
    # The menu shows the last answer until the new check comes back.
    assert _entries(window)[0].isEnabled()
    _settle(qtbot, window)
    assert not _entries(window)[0].isEnabled()
    assert _entries(window)[0].text() == "flaky.json (not found)"

    _write(path)
    recent.aboutToShow.emit()
    assert not _entries(window)[0].isEnabled()
    _settle(qtbot, window)
    assert _entries(window)[0].isEnabled()
    assert _entries(window)[0].text() == "flaky.json"


def test_a_file_deleted_after_the_menu_opened_reports_it(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "moved.json")
    remember_recent_file(path)
    window = _show(qtbot)
    editor = _input(window)
    entry = _entries(window)[0]
    path.unlink()

    entry.trigger()

    qtbot.waitUntil(lambda: "Couldn't find" in _status(window).text())
    assert _status(window).text() == (
        "Couldn't find moved.json. It may have been moved or deleted."
    )
    assert editor.toPlainText() == ""
    assert load_recent_files() == [str(path)]
    _recent(window).aboutToShow.emit()
    _settle(qtbot, window)
    assert not _entries(window)[0].isEnabled()
    assert _entries(window)[0].text() == "moved.json (not found)"


def test_a_missing_file_with_a_shared_name_ends_with_the_suffix(
    qtbot, tmp_path
) -> None:
    staging = _write(tmp_path / "staging" / "data.json")
    gone = _write(tmp_path / "Q&A" / "data.json")
    remember_recent_file(gone)
    remember_recent_file(staging)
    gone.unlink()
    window = _show(qtbot)

    staging_action, gone_action = _entries(window)

    assert staging_action.text() == "data.json — staging"
    assert gone_action.text() == "data.json — Q&&A (not found)"
    assert gone_action.iconText() == "data.json — Q&A (not found)"
    assert not gone_action.isEnabled()
    assert gone_action.toolTip() == "File not found"


def test_clear_menu_empties_the_list(qtbot, tmp_path) -> None:
    remember_recent_file(_write(tmp_path / "a.json"))
    remember_recent_file(_write(tmp_path / "b.json"))
    window = _show(qtbot)

    _clear(window).trigger()

    qtbot.waitUntil(lambda: _labels(_recent(window)) == ["Clear Menu"])
    assert not _clear(window).isEnabled()
    assert load_recent_files() == []
    assert problems_settings().value(RECENT_FILES_KEY, []) in ([], None)


def test_the_list_persists_into_a_new_window(qtbot, tmp_path, monkeypatch) -> None:
    path = _write(tmp_path / "kept.json")
    first = _show(qtbot)
    _open_with_dialog(qtbot, monkeypatch, first, path, _SAMPLE)
    first.close()

    second = _show(qtbot)

    assert _labels(_recent(second)) == ["kept.json", "|", "Clear Menu"]


def test_another_window_sees_new_files_when_its_menu_opens(
    qtbot, tmp_path, monkeypatch
) -> None:
    path = _write(tmp_path / "shared.json")
    first = _show(qtbot)
    second = _show(qtbot)

    _open_with_dialog(qtbot, monkeypatch, first, path, _SAMPLE)
    _recent(second).aboutToShow.emit()

    assert _labels(_recent(second))[0] == "shared.json"


def test_the_menu_opens_without_waiting_on_a_slow_disk(
    qtbot, tmp_path, slow_stat
) -> None:
    kept = _write(tmp_path / "kept.json")
    gone = _write(tmp_path / "gone.json")
    remember_recent_file(gone)
    remember_recent_file(kept)
    slow_stat.hold(kept, gone)
    window = _show(qtbot, settle=False)
    recent = _recent(window)
    gone.unlink()

    recent.aboutToShow.emit()

    # Built while every stat is still stuck: both rows, both enabled.
    assert _labels(recent) == ["kept.json", "gone.json", "|", "Clear Menu"]
    assert all(action.isEnabled() for action in _entries(window))
    assert recent.checking()
    qtbot.waitUntil(lambda: len(slow_stat.calls) == 2)
    assert slow_stat.main_thread_calls == []

    slow_stat.release()
    _settle(qtbot, window)

    kept_action, gone_action = _entries(window)
    assert kept_action.text() == "kept.json"
    assert kept_action.isEnabled()
    assert gone_action.text() == "gone.json (not found)"
    assert not gone_action.isEnabled()
    assert gone_action.toolTip() == "File not found"
    assert slow_stat.main_thread_calls == []


def test_a_stuck_file_holds_one_check_and_not_the_others(
    qtbot, tmp_path, slow_stat
) -> None:
    stuck = _write(tmp_path / "stuck.json")
    gone = _write(tmp_path / "gone.json")
    remember_recent_file(gone)
    remember_recent_file(stuck)
    gone.unlink()
    slow_stat.hold(stuck)
    window = _show(qtbot, settle=False)
    recent = _recent(window)

    # The missing file's answer arrives while stuck.json's stat hangs.
    qtbot.waitUntil(lambda: _entries(window)[1].text() == "gone.json (not found)")
    for _ in range(3):
        recent.aboutToShow.emit()
    assert _entries(window)[1].text() == "gone.json (not found)"
    assert _entries(window)[0].isEnabled()
    assert recent.checking()
    assert slow_stat.calls == [stuck]

    slow_stat.release()
    _settle(qtbot, window)
    assert _entries(window)[0].text() == "stuck.json"


def test_choosing_a_row_before_its_check_returns_opens_it(
    qtbot, tmp_path, slow_stat
) -> None:
    path = _write(tmp_path / "early.json")
    remember_recent_file(path)
    slow_stat.hold(path)
    window = _show(qtbot, settle=False)

    _entries(window)[0].trigger()

    # Open starts at once with its own off-thread read; it does not wait
    # for the menu's check. Both reach the stuck stat.
    assert window._loading_path == path
    assert _recent(window).checking()
    slow_stat.release()
    qtbot.waitUntil(lambda: _input(window).toPlainText() == _SAMPLE)
    _settle(qtbot, window)
    assert slow_stat.main_thread_calls == []


def test_stuck_checks_do_not_delay_opening_a_healthy_file(
    qtbot, tmp_path, monkeypatch, slow_stat
) -> None:
    stuck = [_write(tmp_path / "share" / f"lost{index}.json") for index in range(8)]
    for path in stuck:
        remember_recent_file(path)
    slow_stat.hold(*stuck)
    healthy = _write(tmp_path / "local.json")
    # A small laptop: Open's reader shares no threads with recent checks.
    pool = QThreadPool.globalInstance()
    cores = pool.maxThreadCount()
    pool.setMaxThreadCount(2)
    try:
        window = _show(qtbot, settle=False)
        _recent(window).aboutToShow.emit()
        qtbot.waitUntil(lambda: len(slow_stat.calls) >= 2)

        _open_with_dialog(qtbot, monkeypatch, window, healthy, _SAMPLE)

        assert _recent(window).checking()
    finally:
        slow_stat.release()
        pool.setMaxThreadCount(cores)


@pytest.mark.perf
def test_building_the_menu_with_a_slow_disk_is_within_budget(
    qtbot, tmp_path, slow_stat, perf_budget
) -> None:
    """10 ms to build the menu while every file's stat hangs."""
    paths = [_write(tmp_path / f"file{index}.json") for index in range(10)]
    for path in paths:
        remember_recent_file(path)
    slow_stat.hold(*paths)
    window = _show(qtbot, settle=False)
    recent = _recent(window)

    perf_budget.within(
        _SLOW_DISK_BUILD_S,
        recent.aboutToShow.emit,
        fixture="10 recent files, every stat stuck",
    )

    assert len(_entries(window)) == 10
    assert slow_stat.main_thread_calls == []
    slow_stat.release()
    _settle(qtbot, window)


def test_settings_junk_is_ignored(qtbot, tmp_path) -> None:
    good = _write(tmp_path / "good.json")
    settings = problems_settings()
    settings.setValue(RECENT_FILES_KEY, ["", str(good), str(good)])
    settings.sync()
    window = _show(qtbot)
    assert _labels(_recent(window)) == ["good.json", "|", "Clear Menu"]

    settings.setValue(RECENT_FILES_KEY, 42)
    settings.sync()
    assert load_recent_files() == []


def test_open_recent_is_disabled_while_formatting(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "next.json")
    remember_recent_file(path)
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("typed")
    submenu = _recent(window).menuAction()
    assert submenu.isEnabled()

    window._formatting = True
    window._sync_format_button()

    assert not submenu.isEnabled()
    assert submenu.toolTip() == "Wait for formatting to finish"
    # Even a stray trigger can't swap Input under a running job.
    _entries(window)[0].trigger()
    assert _message(window, "replaceInput") is None
    assert window._loading_path is None
    assert editor.toPlainText() == "typed"

    window._formatting = False
    window._sync_format_button()
    assert submenu.isEnabled()
    assert submenu.toolTip() != "Wait for formatting to finish"


def test_open_recent_is_disabled_while_a_file_loads(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "next.json")
    remember_recent_file(path)
    window = _show(qtbot)
    submenu = _recent(window).menuAction()

    _entries(window)[0].trigger()
    assert window._loading_path is not None
    assert not submenu.isEnabled()

    qtbot.waitUntil(lambda: window._loading_path is None)
    assert submenu.isEnabled()


def test_open_recent_is_disabled_while_the_replace_prompt_is_up(
    qtbot, tmp_path
) -> None:
    path = _write(tmp_path / "next.json")
    remember_recent_file(path)
    window = _show(qtbot)
    _input(window).setPlainText("typed")
    submenu = _recent(window).menuAction()

    _entries(window)[0].trigger()
    box = _require(window, "replaceInput")
    assert not submenu.isEnabled()

    box.reject()
    qtbot.waitUntil(lambda: submenu.isEnabled())


def test_keyboard_reaches_and_opens_a_recent_file(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "keys.json")
    remember_recent_file(path)
    window = _show(qtbot)
    file_menu = window.findChild(QMenu, "menuFile")
    assert isinstance(file_menu, QMenu)
    recent = _recent(window)

    file_menu.popup(window.mapToGlobal(QPoint(20, 20)))
    qtbot.waitExposed(file_menu)
    qtbot.keyClick(file_menu, Qt.Key.Key_Down)
    qtbot.keyClick(file_menu, Qt.Key.Key_Down)
    assert file_menu.activeAction() is recent.menuAction()
    qtbot.keyClick(file_menu, Qt.Key.Key_Right)
    qtbot.waitExposed(recent)
    assert recent.activeAction() is _entries(window)[0]
    qtbot.keyClick(recent, Qt.Key.Key_Return)

    qtbot.waitUntil(lambda: _input(window).toPlainText() == _SAMPLE)


def test_entries_have_plain_accessible_names(qtbot, tmp_path) -> None:
    gone = _write(tmp_path / "gone.json")
    remember_recent_file(gone)
    remember_recent_file(_write(tmp_path / "R&D.json"))
    remember_recent_file(_write(tmp_path / "plain.yaml"))
    gone.unlink()
    window = _show(qtbot)
    recent = _recent(window)
    recent.popup(window.mapToGlobal(QPoint(20, 20)))
    qtbot.waitExposed(recent)

    menu = QAccessible.queryAccessibleInterface(recent)
    assert menu is not None
    names = [
        menu.child(index).text(QAccessible.Text.Name)
        for index in range(menu.childCount())
    ]

    assert names == [
        "plain.yaml",
        "R&D.json",
        "gone.json (not found)",
        "",
        "Clear Menu",
    ]
    recent.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_recent_entries_reach_four_and_a_half_to_one(
    qtbot, tmp_path, install_scheme, scheme: str
) -> None:
    kept = _write(tmp_path / "kept.json")
    gone = _write(tmp_path / "gone.json")
    remember_recent_file(gone)
    remember_recent_file(kept)
    gone.unlink()
    window = _show(qtbot)
    palette = install_scheme(scheme)
    recent = _recent(window)
    recent.popup(window.mapToGlobal(QPoint(20, 20)))
    qtbot.waitExposed(recent)
    recent.setActiveAction(None)

    image = recent.grab().toImage()
    fill = palette.color(QPalette.ColorRole.Window)
    kept_action, gone_action = _entries(window)
    kept_row = _row(image, recent, kept_action)
    gone_row = _row(image, recent, gone_action)

    assert kept_row.pixelColor(1, kept_row.height() // 2) == fill
    kept_contrast = _strongest_contrast(kept_row, fill)
    gone_contrast = _strongest_contrast(gone_row, fill)
    assert kept_contrast >= 4.5
    # The missing file is dimmed but still legible; its tooltip says why.
    assert 3 <= gone_contrast < kept_contrast
    recent.close()


# Worst CI median 1.0 ms (Windows) x 1.25, rounded up to the next 0.01 s:
# budgets under 0.1 s round up that way (Isaac).
_SLOW_DISK_BUILD_S = 0.01


class _SlowStat:
    """``Path.stat`` that blocks on held paths until released."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._gate = threading.Event()
        self._lock = threading.Lock()
        self.calls: list[Path] = []
        self.main_thread_calls: list[Path] = []

    def hold(self, *paths: Path) -> None:
        self._held.update(os.fspath(path) for path in paths)

    def release(self) -> None:
        self._gate.set()

    def wait_if_held(self, path: Path) -> None:
        if os.fspath(path) not in self._held:
            return
        if threading.current_thread() is threading.main_thread():
            # Never block the test: record it so the test fails instead.
            self.main_thread_calls.append(path)
            return
        with self._lock:
            self.calls.append(path)
        # A hang guard, not a timing: a check never outlives the test.
        self._gate.wait(10)


@pytest.fixture
def slow_stat(monkeypatch) -> Iterator[_SlowStat]:
    slow = _SlowStat()
    real_stat = Path.stat

    def _stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        slow.wait_if_held(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _stat)
    yield slow
    slow.release()
    QThreadPool.globalInstance().waitForDone(10_000)


def _settle(qtbot, window) -> None:
    qtbot.waitUntil(lambda: not _recent(window).checking())


def _row(image: QImage, menu: QMenu, action: QAction) -> QImage:
    rect = menu.actionGeometry(action)
    ratio = image.devicePixelRatio()
    return image.copy(
        int(rect.x() * ratio),
        int(rect.y() * ratio),
        int(rect.width() * ratio),
        int(rect.height() * ratio),
    )


def _strongest_contrast(image: QImage, background: QColor) -> float:
    return max(
        contrast_ratio(image.pixelColor(x, y), background)
        for y in range(image.height())
        for x in range(image.width())
    )


def _open_with_dialog(qtbot, monkeypatch, window, path: Path, text: str) -> None:
    monkeypatch.setattr(
        "galley.app.main_window.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: (str(path), ""),
    )
    window._actions.open_file.trigger()
    qtbot.waitUntil(
        lambda: window._loading_path is None and _input(window).toPlainText() == text
    )


def _show(qtbot, *, settle: bool = True):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    if settle:
        _settle(qtbot, window)
    return window


def _recent(window) -> QMenu:
    menu = window.findChild(QMenu, "menuOpenRecent")
    assert isinstance(menu, QMenu)
    return menu


def _clear(window) -> QAction:
    found = [
        action
        for action in _recent(window).actions()
        if action.objectName() == "clearRecentFiles"
    ]
    assert len(found) == 1
    return found[0]


def _entries(window) -> list[QAction]:
    return [action for action in _recent(window).actions() if action.data()]


def _labels(menu: QMenu) -> list[str]:
    return ["|" if action.isSeparator() else action.text() for action in menu.actions()]


def _input(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


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
    if not found:
        return None
    assert len(found) == 1
    return found[0]


def _require(window, name: str) -> QMessageBox:
    box = _message(window, name)
    assert box is not None
    return box


def _write(path: Path, text: str = _SAMPLE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path
