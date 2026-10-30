"""The leak guard in tests/conftest.py fails a test that keeps a MainWindow.

The guard runs in a fixture teardown. pytest-qt closes the widgets
registered with ``qtbot.addWidget`` in its ``pytest_runtest_teardown``
hook, before any fixture teardown runs, so the guard always comes after
it. The inner session below checks that qtbot's close was the only one.
"""

from pathlib import Path

from PySide6.QtCore import QSettings

from galley.app.main import create_main_window
from galley.app.main_window import MainWindow

# Read at import, before any test wraps it.
_ORIGINAL_INIT = vars(MainWindow)["__init__"]

_INNER_TESTS = '''
import sys
import weakref

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

from galley.app.main import create_main_window
from galley.app.main_window import MainWindow

_KEPT = []
_CLOSED_BY = []


class _Recorded(MainWindow):
    def closeEvent(self, event):
        _CLOSED_BY.append(sys._getframe(1).f_code.co_name)
        super().closeEvent(event)


@pytest.fixture(scope="module")
def app_slots():
    """Disconnect at the end of the module, after the guard has counted.

    Only weak references are kept here, so the connection is the one thing
    holding each slot.
    """
    slots = []
    yield slots
    for ref in slots:
        slot = ref()
        if slot is not None:
            QApplication.instance().focusChanged.disconnect(slot)


def test_kept_by_a_module_name():
    _KEPT.extend([create_main_window(), create_main_window()])


def test_clean_after_a_leak():
    window = create_main_window()
    window.show()
    window.close()


def test_kept_by_a_lambda_on_an_app_signal(app_slots):
    window = create_main_window()
    slot = lambda *_: window
    QApplication.instance().focusChanged.connect(slot)
    app_slots.append(weakref.ref(slot))


@pytest.fixture(scope="module")
def strays():
    """Delete at the end of the module, after the guard has counted. The
    guard deletes only MainWindows, so nothing else deletes them.
    """
    widgets = []
    yield widgets
    for widget in widgets:
        widget.deleteLater()
        QApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete.value)


def test_kept_by_a_lambda_on_a_parentless_widget(strays):
    window = create_main_window()
    window.stray = QWidget()
    window.stray.objectNameChanged.connect(lambda *_: window)
    strays.append(window.stray)


@pytest.mark.allow_window_leak
def test_allowed_to_leak():
    _KEPT.append(create_main_window())


def test_fails_while_holding_a_window():
    window = create_main_window()
    assert window is None


def test_closed_by_qtbot(qtbot):
    window = _Recorded()
    qtbot.addWidget(window)
    window.show()


def test_qtbot_closed_it_once_before_the_guard():
    assert _CLOSED_BY == ["_close_widgets"]
'''


def _isolate_settings(path: Path) -> None:
    """Point QSettings at a throwaway directory, as the app tests do."""
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(path),
    )


def test_the_guard_fails_only_the_tests_that_leak(pytester, qapp) -> None:
    del qapp
    _isolate_settings(pytester.path / "settings")
    pytester.makeini(
        "[pytest]\nmarkers =\n    allow_window_leak: a known MainWindow leak\n"
    )
    pytester.makepyfile(test_inner=_INNER_TESTS)

    # The inner session loads the real conftest, so it runs the real guard.
    reprec = pytester.inline_run("-p", "tests.conftest")

    reports = reprec.getreports("pytest_runtest_logreport")
    failed = {
        (report.nodeid.rpartition("::")[2], report.when)
        for report in reports
        if report.failed
    }
    assert failed == {
        ("test_kept_by_a_module_name", "teardown"),
        ("test_kept_by_a_lambda_on_an_app_signal", "teardown"),
        ("test_kept_by_a_lambda_on_a_parentless_widget", "teardown"),
        # The traceback holds the window, so the guard adds nothing.
        ("test_fails_while_holding_a_window", "call"),
    }
    passed = {
        report.nodeid.rpartition("::")[2]
        for report in reports
        if report.when == "call" and report.passed
    }
    assert {
        "test_clean_after_a_leak",
        "test_allowed_to_leak",
        "test_closed_by_qtbot",
        "test_qtbot_closed_it_once_before_the_guard",
    } <= passed
    messages = {
        report.nodeid: report.longreprtext
        for report in reports
        if report.failed and report.when == "teardown"
    }
    for name, count in [
        ("test_kept_by_a_module_name", "2 MainWindows"),
        ("test_kept_by_a_lambda_on_an_app_signal", "1 MainWindow"),
        ("test_kept_by_a_lambda_on_a_parentless_widget", "1 MainWindow"),
    ]:
        nodeid = f"test_inner.py::{name}"
        assert f"{nodeid} left {count} alive after it ended" in messages[nodeid]


def test_the_guard_counts_the_windows_a_plain_test_makes(qapp, tmp_path) -> None:
    """This window goes when the test ends, so the guard passes it."""
    del qapp
    _isolate_settings(tmp_path)
    assert vars(MainWindow)["__init__"].__wrapped__ is _ORIGINAL_INIT
    window = create_main_window()
    window.show()
    window.close()


def test_the_guard_is_off_in_a_test_that_runs_pytester(pytester) -> None:
    del pytester
    assert vars(MainWindow)["__init__"] is _ORIGINAL_INIT
