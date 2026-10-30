"""Pytest defaults for Galley. Tests always run without a display."""

import functools
import gc
import os
import sys
import weakref
from collections.abc import Generator, Iterable, Iterator
from pathlib import Path

import pytest
from tests.perf import Budget, budget_for, current_mode

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# tests/test_leak_guard.py runs the leak guard in pytester's inner sessions.
pytest_plugins = ["pytester"]

# DejaVu Sans Mono 2.37, regular, from the official release (#145). Its
# license is LICENSE-DejaVu.txt in the same folder.
CODE_TEST_FONT = (
    Path(__file__).resolve().parent / "fixtures" / "fonts" / "DejaVuSansMono.ttf"
)
CODE_TEST_FAMILY = "DejaVu Sans Mono"


@pytest.fixture(scope="session")
def code_test_font(qapp) -> str:
    """Load DejaVu Sans Mono and make it the editors' code font (#145).

    Qt's offscreen platform on the Windows runners has no fonts and draws
    every glyph as the same box, so pixel tests broke there only. Loading a
    real font gives every platform the same glyphs. ``code_font`` asks for
    the generic ``monospace`` family, which Qt maps to this font here. App
    code is unchanged.
    """
    from PySide6.QtGui import QFont, QFontDatabase

    del qapp
    font_id = QFontDatabase.addApplicationFont(str(CODE_TEST_FONT))
    if font_id == -1:
        pytest.fail(f"Qt could not load the test font {CODE_TEST_FONT}", pytrace=False)
    families = QFontDatabase.applicationFontFamilies(font_id)
    if CODE_TEST_FAMILY not in families:
        pytest.fail(
            f"the test font reports {families}, not {CODE_TEST_FAMILY!r}",
            pytrace=False,
        )
    QFont.insertSubstitution("monospace", CODE_TEST_FAMILY)
    return CODE_TEST_FAMILY


@pytest.fixture(autouse=True)
def _qt_tests_use_the_code_test_font(request: pytest.FixtureRequest) -> None:
    """Load the test font before a Qt test makes its first widget.

    Only tests that use ``qapp`` or ``qtbot`` start Qt, so core tests stay
    without a QApplication.
    """
    if {"qapp", "qtbot"} & set(request.fixturenames):
        request.getfixturevalue("code_test_font")


_MAIN_WINDOW_MODULE = "galley.app.main_window"
_SETUP_OR_CALL_FAILED = pytest.StashKey[bool]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Note a failed setup or call, so the leak guard doesn't add a second
    failure. A failed test's traceback still holds its window.
    """
    del call
    report = yield
    if report.when in ("setup", "call") and not report.passed:
        item.stash[_SETUP_OR_CALL_FAILED] = True
    return report


@pytest.fixture(autouse=True)
def _no_main_window_outlives_its_test(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Fail a test that leaves a MainWindow alive after it ends.

    A lambda connected to a long-lived signal kept every closed window
    alive in #172, and the app tests ran 3x slower before anyone noticed.

    The guard is off until ``galley.app.main_window`` is imported, so core
    tests never load Qt for it. Otherwise it counts the windows this test
    makes by wrapping ``MainWindow.__init__``, and does nothing more when
    the test made none. Tests that run pytester are skipped: their inner
    sessions run their own guard.
    """
    module = sys.modules.get(_MAIN_WINDOW_MODULE)
    if module is None or "pytester" in request.fixturenames:
        yield
        return
    window_class = module.MainWindow
    original_init = vars(window_class)["__init__"]
    made = weakref.WeakSet()
    made_any = False

    @functools.wraps(original_init)
    def _counted_init(self, *args, **kwargs) -> None:
        nonlocal made_any
        original_init(self, *args, **kwargs)
        made.add(self)
        made_any = True

    window_class.__init__ = _counted_init
    try:
        yield
    finally:
        window_class.__init__ = original_init
    if made_any:
        _fail_on_leftover_windows(request, made)


def _fail_on_leftover_windows(
    request: pytest.FixtureRequest, made: weakref.WeakSet
) -> None:
    """Close and delete this test's windows, then fail if any is still alive.

    pytest-qt closes and deletes the widgets registered with
    ``qtbot.addWidget`` in its teardown hook, before any fixture teardown,
    so this runs after it. A closed window is hidden, and only visible
    windows are closed here, so none is closed twice.
    """
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    _close_and_delete(window for window in list(made) if isValid(window))
    _run_deferred_deletes()
    # pytest keeps fixture values in funcargs until after every teardown,
    # and a fixture that returns a window would keep it alive here. The
    # fixtures that made them are already torn down.
    funcargs = getattr(request.node, "funcargs", None)
    if funcargs:
        funcargs.clear()
    check = not request.node.stash.get(_SETUP_OR_CALL_FAILED, False) and (
        request.node.get_closest_marker("allow_window_leak") is None
    )
    if check and made:
        gc.collect()
    leftover = len(made) if check else 0
    # Every other top-level window goes after the count, so a window held
    # by a parentless widget's connection still counts. Compare's Right
    # editor was one in #172.
    _close_and_delete(
        widget
        for widget in QApplication.topLevelWidgets()
        if widget.parentWidget() is None
    )
    _run_deferred_deletes()
    if leftover:
        noun, it = ("MainWindow", "it") if leftover == 1 else ("MainWindows", "them")
        pytest.fail(
            f"{request.node.nodeid} left {leftover} {noun} alive after it "
            f"ended. Something still refers to {it}, such as a module-level "
            "name or a lambda connected to a signal that outlives the window.",
            pytrace=False,
        )


def _close_and_delete(windows: Iterable) -> None:
    for window in windows:
        if window.isVisible():
            window.close()
        window.deleteLater()


def _run_deferred_deletes() -> None:
    """Run pending deleteLater() calls now. Outside a running event loop,
    processEvents() alone leaves them queued.
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()


@pytest.fixture
def perf_budget(request: pytest.FixtureRequest) -> Budget:
    """Time and check a wall-clock budget. Only for tests marked ``perf``."""
    return budget_for(request.node)


def pytest_configure(config: pytest.Config) -> None:
    """Stop at once on a GALLEY_PERF_MODE that is neither gate nor strict."""
    del config
    current_mode()
