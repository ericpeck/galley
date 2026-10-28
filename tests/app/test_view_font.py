"""View › Font: Default Font, a picked monospace family, and the size steps.

No test opens a real font dialog: ``QFontDialog.getFont`` is stubbed. No
test names a family the runner must have. A picked family is any second
monospace family the runner lists, and a test skips when there is none.
Settings go to the throwaway INI folder from ``tests/app/conftest.py``.
"""

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QFont, QFontDatabase, QFontInfo, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QFontDialog, QMenu
from tests.app.test_large_file_stress import _document, _first_block_is_colored
from tests.perf import time_runs

from galley.app.editor import CodeEditor
from galley.app.editor_font import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MIN_SIZE,
    application_editor_font,
)
from galley.app.fonts import code_font
from galley.app.main import create_main_window
from galley.app.problems_settings import problems_settings

CTRL = Qt.KeyboardModifier.ControlModifier
_FAMILY_KEY = "editor/font_family"
_SIZE_KEY = "editor/font_size"
_MISSING_FAMILY = "Galley Test Mono That Is Not Installed"
_JSON_LINES = "".join(f'{{"id": {n}, "name": "row {n}"}}\n' for n in range(300))
# A hang guard for coloring the 2 MB document, not a performance budget.
_COLORING_HANG_MS = 120_000


@pytest.fixture(autouse=True)
def no_font_dialog(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Fail a test that reaches the real dialog. Tests stub it with ``_pick``."""
    calls: list[tuple] = []

    def refuse(*args: object, **kwargs: object) -> tuple[bool, QFont]:
        calls.append((args, kwargs))
        raise AssertionError("the real QFontDialog must not open in tests")

    monkeypatch.setattr(QFontDialog, "getFont", staticmethod(refuse))
    return calls


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _font_menu(window) -> QMenu:
    menu = window.findChild(QMenu, "menuFont")
    assert menu is not None
    return menu


def _labels(window) -> list[str]:
    """Shown items of View › Font, top to bottom; "|" is a separator."""
    return [
        "|" if action.isSeparator() else action.text()
        for action in _font_menu(window).actions()
        if action.isVisible()
    ]


def _item(window, text: str) -> QAction:
    matches = [
        action
        for action in _font_menu(window).actions()
        if action.isVisible() and action.text() == text
    ]
    assert len(matches) == 1, text
    return matches[0]


def _checked(window) -> list[str]:
    return [
        action.text()
        for action in _font_menu(window).actions()
        if action.isVisible() and action.isCheckable() and action.isChecked()
    ]


def _editors(window) -> list[CodeEditor]:
    return [window._input_pane.editor, window._output_pane.editor]


def _settled(qtbot, window, size: int) -> None:
    """Wait for a size change to reach both editors."""
    qtbot.waitUntil(
        lambda: all(editor.font().pointSize() == size for editor in _editors(window))
    )


def _monospace_family() -> str:
    """A monospace family on this runner that Default Font does not resolve to."""
    default = QFontInfo(code_font()).family()
    for family in QFontDatabase.families():
        if (
            family != default
            and not family.startswith(".")
            and not QFontDatabase.isPrivateFamily(family)
            and QFontDatabase.isFixedPitch(family)
        ):
            return family
    pytest.skip("this runner lists no second monospace family")


def _proportional_family() -> str:
    for family in QFontDatabase.families():
        if (
            not family.startswith(".")
            and not QFontDatabase.isPrivateFamily(family)
            and not QFontDatabase.isFixedPitch(family)
        ):
            return family
    pytest.skip("this runner lists no proportional family")


def _pick(monkeypatch, font: QFont | None) -> list[tuple]:
    """Make the dialog return ``font``, or cancel when it is None."""
    calls: list[tuple] = []

    def stub(initial, parent=None, title="", options=None) -> tuple[bool, QFont]:
        calls.append((QFont(initial), parent, title, options))
        if font is None:
            return False, QFont(initial)
        return True, QFont(font)

    monkeypatch.setattr(QFontDialog, "getFont", staticmethod(stub))
    return calls


def _choose(qtbot, window, monkeypatch, family: str, size: int) -> list[tuple]:
    calls = _pick(monkeypatch, QFont(family, size))
    _item(window, "Choose Font…").trigger()
    _settled(qtbot, window, size)
    return calls


def _save(family: str | None, size: object | None) -> None:
    settings = problems_settings()
    if family is not None:
        settings.setValue(_FAMILY_KEY, family)
    if size is not None:
        settings.setValue(_SIZE_KEY, size)
    settings.sync()


def _step(qtbot, window, text: str, times: int) -> None:
    for _ in range(times):
        _item(window, text).trigger()
        qtbot.waitUntil(lambda: not application_editor_font().pending())


class _FontChanges(QObject):
    def __init__(self, widget) -> None:
        super().__init__(widget)
        self.count = 0
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.FontChange:
            self.count += 1
        return False


def _count_highlights(monkeypatch, editor: CodeEditor) -> list[int]:
    highlighter = editor._highlighter
    original = highlighter.highlightBlock
    seen: list[int] = []

    def counting(text: str) -> None:
        seen.append(highlighter.currentBlock().blockNumber())
        original(text)

    monkeypatch.setattr(highlighter, "highlightBlock", counting)
    return seen


def test_font_menu_lists_default_choose_and_the_size_steps(qtbot) -> None:
    window = _show(qtbot)
    assert _labels(window) == [
        "Default Font",
        "Choose Font…",
        "|",
        "Bigger",
        "Smaller",
        "Actual Size",
    ]
    assert _checked(window) == ["Default Font"]
    assert not _item(window, "Choose Font…").isCheckable()
    for editor in _editors(window):
        assert editor.font().family() == code_font().family()
        assert editor.font().pointSize() == DEFAULT_SIZE
    # Actual Size has nothing to reset at the default size.
    assert not _item(window, "Actual Size").isEnabled()
    assert _item(window, "Bigger").isEnabled()
    assert _item(window, "Smaller").isEnabled()


def test_font_items_keep_out_of_the_macos_app_menu(qtbot) -> None:
    window = _show(qtbot)
    for action in _font_menu(window).actions():
        if not action.isSeparator():
            assert action.menuRole() == QAction.MenuRole.NoRole, action.text()


def test_choose_font_shows_the_family_checked_above_choose(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    calls = _choose(qtbot, window, monkeypatch, family, 14)

    assert len(calls) == 1
    initial, parent, title, options = calls[0]
    assert parent is window
    assert title == "Choose Font"
    assert initial.pointSize() == DEFAULT_SIZE
    assert options & QFontDialog.FontDialogOption.MonospacedFonts
    # The macOS native panel ignores MonospacedFonts, so Qt's own dialog runs.
    assert options & QFontDialog.FontDialogOption.DontUseNativeDialog
    assert _labels(window)[:3] == ["Default Font", family, "Choose Font…"]
    assert _checked(window) == [family]
    assert _item(window, family).menuRole() == QAction.MenuRole.NoRole
    for editor in _editors(window):
        assert editor.font().family() == family
        assert editor.font().pointSize() == 14


def test_a_canceled_dialog_changes_nothing(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _pick(monkeypatch, None)
    _item(window, "Choose Font…").trigger()
    QApplication.processEvents()
    assert _labels(window)[:2] == ["Default Font", "Choose Font…"]
    assert _checked(window) == ["Default Font"]
    assert problems_settings().value(_FAMILY_KEY) is None


def test_a_proportional_pick_is_not_applied(qtbot, monkeypatch) -> None:
    family = _proportional_family()
    window = _show(qtbot)
    _pick(monkeypatch, QFont(family, 20))
    _item(window, "Choose Font…").trigger()
    QApplication.processEvents()
    assert _checked(window) == ["Default Font"]
    for editor in _editors(window):
        assert editor.font().family() == code_font().family()
        assert editor.font().pointSize() == DEFAULT_SIZE


def test_the_dialog_size_is_clamped(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    _pick(monkeypatch, QFont(family, 72))
    _item(window, "Choose Font…").trigger()
    _settled(qtbot, window, MAX_SIZE)
    assert _checked(window) == [family]


def test_default_font_after_a_pick_keeps_the_family_item(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    _choose(qtbot, window, monkeypatch, family, 15)
    _item(window, "Default Font").trigger()
    qtbot.waitUntil(lambda: _editors(window)[0].font().family() != family)

    assert _labels(window)[:3] == ["Default Font", family, "Choose Font…"]
    assert _checked(window) == ["Default Font"]
    for editor in _editors(window):
        assert editor.font().family() == code_font().family()
        assert editor.font().pointSize() == 15

    _item(window, family).trigger()
    qtbot.waitUntil(lambda: _editors(window)[0].font().family() == family)
    assert _checked(window) == [family]


def test_size_steps_one_point_and_grey_out_at_the_limits(qtbot) -> None:
    window = _show(qtbot)
    _step(qtbot, window, "Bigger", 1)
    _settled(qtbot, window, DEFAULT_SIZE + 1)
    assert _item(window, "Actual Size").isEnabled()

    _step(qtbot, window, "Smaller", DEFAULT_SIZE + 1 - MIN_SIZE)
    _settled(qtbot, window, MIN_SIZE)
    assert not _item(window, "Smaller").isEnabled()
    assert _item(window, "Bigger").isEnabled()
    application_editor_font().step(-1)
    assert application_editor_font().size() == MIN_SIZE

    _step(qtbot, window, "Bigger", MAX_SIZE - MIN_SIZE)
    _settled(qtbot, window, MAX_SIZE)
    assert not _item(window, "Bigger").isEnabled()
    assert _item(window, "Smaller").isEnabled()
    application_editor_font().step(1)
    assert application_editor_font().size() == MAX_SIZE


def test_actual_size_resets_only_the_size(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    _choose(qtbot, window, monkeypatch, family, 20)
    assert _item(window, "Actual Size").isEnabled()

    _item(window, "Actual Size").trigger()
    _settled(qtbot, window, DEFAULT_SIZE)

    assert _checked(window) == [family]
    assert _editors(window)[0].font().family() == family
    assert not _item(window, "Actual Size").isEnabled()
    assert int(problems_settings().value(_SIZE_KEY)) == DEFAULT_SIZE


def test_size_steps_reach_both_editors_and_the_gutter(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    for editor in (source, output):
        editor.setPlainText("x\n" * 2000)
    before = [editor.viewportMargins().left() for editor in (source, output)]

    _step(qtbot, window, "Bigger", 10)
    _settled(qtbot, window, DEFAULT_SIZE + 10)

    after = [editor.viewportMargins().left() for editor in (source, output)]
    assert after[0] > before[0]
    assert after[1] > before[1]


def test_the_find_fields_keep_their_own_font(qtbot) -> None:
    window = _show(qtbot)
    fields = [window._input_pane.find_bar.field, window._output_pane.find_bar.field]
    before = [QFont(field.font()) for field in fields]
    _step(qtbot, window, "Bigger", 3)
    _settled(qtbot, window, DEFAULT_SIZE + 3)
    assert [field.font() for field in fields] == before


def test_five_bigger_steps_relayout_once(qtbot) -> None:
    window = _show(qtbot)
    changes = [_FontChanges(editor) for editor in _editors(window)]
    for _ in range(5):
        _item(window, "Bigger").trigger()
    _settled(qtbot, window, DEFAULT_SIZE + 5)
    QApplication.processEvents()
    assert [change.count for change in changes] == [1, 1]
    assert int(problems_settings().value(_SIZE_KEY)) == DEFAULT_SIZE + 5


def test_a_font_change_does_not_recolor(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    source = _editors(window)[0]
    source.setPlainText(_JSON_LINES)
    qtbot.waitUntil(lambda: not source.coloring_pending())
    assert _first_block_is_colored(source)
    seen = _count_highlights(monkeypatch, source)

    _step(qtbot, window, "Bigger", 2)
    _settled(qtbot, window, DEFAULT_SIZE + 2)
    _choose(qtbot, window, monkeypatch, family, DEFAULT_SIZE + 2)
    source.viewport().repaint()
    QApplication.processEvents()
    assert seen == []


def test_size_shortcuts_still_work_from_the_editor(qtbot) -> None:
    window = _show(qtbot)
    source = _editors(window)[0]
    window.activateWindow()
    source.setFocus()
    qtbot.waitUntil(source.hasFocus, timeout=2000)

    bigger = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn)[0][0]
    qtbot.keyClick(source, bigger.key(), bigger.keyboardModifiers())
    _settled(qtbot, window, DEFAULT_SIZE + 1)
    smaller = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut)[0][0]
    qtbot.keyClick(source, smaller.key(), smaller.keyboardModifiers())
    qtbot.keyClick(source, smaller.key(), smaller.keyboardModifiers())
    _settled(qtbot, window, DEFAULT_SIZE - 1)
    qtbot.keyClick(source, Qt.Key.Key_0, CTRL)
    _settled(qtbot, window, DEFAULT_SIZE)
    assert source.toPlainText() == ""


def test_family_and_size_persist_across_a_restart(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    _choose(qtbot, window, monkeypatch, family, 16)
    _step(qtbot, window, "Bigger", 2)
    _settled(qtbot, window, 18)
    window.close()
    assert problems_settings().value(_FAMILY_KEY) == family
    assert int(problems_settings().value(_SIZE_KEY)) == 18

    restarted = create_main_window()
    qtbot.addWidget(restarted)
    # Read before the first show, so the window never paints the default.
    assert not restarted.isVisible()
    for editor in _editors(restarted):
        assert editor.font().family() == family
        assert editor.font().pointSize() == 18
    assert _labels(restarted)[:3] == ["Default Font", family, "Choose Font…"]
    assert _checked(restarted) == [family]


def test_default_font_persists_across_a_restart(qtbot, monkeypatch) -> None:
    family = _monospace_family()
    window = _show(qtbot)
    _choose(qtbot, window, monkeypatch, family, 13)
    _item(window, "Default Font").trigger()
    qtbot.waitUntil(lambda: _editors(window)[0].font().family() != family)
    window.close()

    restarted = _show(qtbot)
    assert _labels(restarted)[:2] == ["Default Font", "Choose Font…"]
    assert _checked(restarted) == ["Default Font"]
    assert _editors(restarted)[0].font().pointSize() == 13


@pytest.mark.parametrize("saved", ["missing", "proportional"])
def test_an_unusable_saved_family_falls_back_quietly(qtbot, saved: str) -> None:
    family = _MISSING_FAMILY if saved == "missing" else _proportional_family()
    _save(family, 17)
    window = _show(qtbot)

    assert _labels(window)[:2] == ["Default Font", "Choose Font…"]
    assert _checked(window) == ["Default Font"]
    for editor in _editors(window):
        assert editor.font().family() == code_font().family()
        assert editor.font().pointSize() == 17
    shown = [
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QDialog) and widget.isVisible()
    ]
    assert shown == []


@pytest.mark.parametrize(
    ("saved", "expected"),
    [(3, MIN_SIZE), (99, MAX_SIZE), ("large", DEFAULT_SIZE), (20, 20)],
)
def test_a_saved_size_is_clamped(qtbot, saved, expected: int) -> None:
    _save(None, saved)
    window = _show(qtbot)
    for editor in _editors(window):
        assert editor.font().pointSize() == expected


def _timed_change(qapp, window, change, done) -> None:
    change()
    while not done():
        qapp.processEvents()
    for editor in _editors(window):
        editor.viewport().repaint()


@pytest.mark.perf
def test_font_change_repaint_on_two_megabytes_is_timed(
    qtbot, qapp, monkeypatch
) -> None:
    """Time a size step and a family change to repaint on 2 MB of JSON.

    Provisional: the medians are printed with no budget. Infra sets the
    budget under #152. Coloring must not run again either way.
    """
    other = _monospace_family()
    window = _show(qtbot)
    window.resize(1280, 800)
    source = _editors(window)[0]
    source.setPlainText(_document("2 MB"))
    qtbot.waitUntil(lambda: not source.coloring_pending(), timeout=_COLORING_HANG_MS)
    assert _first_block_is_colored(source)
    assert source.blockCount() > 50_000
    fonts = application_editor_font()
    seen = _count_highlights(monkeypatch, source)

    sizes = iter(range(10_000))

    def size_step() -> None:
        target = DEFAULT_SIZE + (1 if next(sizes) % 2 == 0 else 0)
        _timed_change(
            qapp,
            window,
            lambda: fonts.set_size(target),
            lambda: source.font().pointSize() == target,
        )

    families = iter(range(10_000))

    def family_change() -> None:
        target = other if next(families) % 2 == 0 else ""
        _timed_change(
            qapp,
            window,
            lambda: fonts.set_family(target),
            lambda: (source.font().family() == other) == bool(target),
        )

    timings = {
        "size step": time_runs(size_step),
        "family change": time_runs(family_change),
    }
    lines = ["font-change timings on 2 MB JSON (provisional; no budget yet, #152)"]
    for label, timing in timings.items():
        samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
        lines.append(f"  {label}: median {timing.median * 1000:.1f} ms ({samples})")
    print("\n".join(lines), flush=True)
    assert seen == []
