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


# Big texts, regex worker times and leftover child processes ---------------------
# Self-contained: the imports are local, so this block merges apart from the
# rest of the file.

# How long the end of the run waits, in all, for children still exiting.
_CHILD_EXIT_S = 10.0
_CHILDREN_BEFORE = pytest.StashKey[frozenset[int]]()
# Where the run lists its big-text compares and regex worker times, unless
# GALLEY_BIGCOMPARE_LOG names a file outside the repository: a file in
# pytest's base temp directory.
_LOG_NAME = "bigcompare.log"
_LOG_ENV = "GALLEY_BIGCOMPARE_LOG"
# Set to 1 for a call-site audit: passing compares are logged too. It slows
# the run, so it is off by default, CI included.
_AUDIT_ENV = "GALLEY_BIGCOMPARE_AUDIT"
_AUDIT_PLUGIN = "galley-bigcompare-audit"
# How many tests the log lists under the worst regex worker times.
_WORST_SHOWN = 10
# How much of a test id the log shows.
_TEST_ID_SHOWN = 200


class _RunNotes:
    """What this run logs, reset when a session starts."""

    def __init__(
        self, log: str | None = None, root: str = ".", *, audit: bool = False
    ) -> None:
        self.log = log
        self.root = root
        self.audit = audit
        self.big_compares = 0
        # The worst elapsed_ms the regex worker reported in each test.
        self.worst_elapsed: dict[str, float] = {}


_NOTES = _RunNotes()


def pytest_assertrepr_compare(op: str, left: object, right: object) -> list[str] | None:
    """Explain a failed ``==`` on a str or bytes over 64 KB, and log it.

    pytest's own difflib diff of two multi-MB texts can run for minutes, so
    a failed one gets a one-line report instead. Smaller values keep
    pytest's diff. In an audit run pytest calls this for passing asserts
    too, so the log lists every such compare to move to ``assert_same_text``.
    """
    from tests.text_compare import describe_difference

    if not _big_text_pair(op, left, right):
        return None
    assert isinstance(left, str | bytes) and isinstance(right, str | bytes)
    same = left == right
    _log_big_compare(left, right, same=same)
    if same:
        return None
    return [describe_difference(left, right)]


class _PassingCompares:
    """Registered only for an audit run, to explain passing asserts too.

    With ``enable_assertion_pass_hook`` on, rewritten asserts build an
    explanation for a passing assert, which calls the hook above, only
    while some plugin has ``pytest_assertion_pass``. Without one, a passing
    assert costs one extra check. The ini stays on, because pytest caches
    rewritten asserts without regard to it.
    """

    def pytest_assertion_pass(self) -> None:
        """Does nothing. Being registered is what counts."""


def _log_big_compare(left: str | bytes, right: str | bytes, *, same: bool) -> None:
    _NOTES.big_compares += 1
    if _NOTES.log is None:
        return
    result = "passed" if same else "failed"
    sizes = (
        f"{type(left).__name__} of length {len(left):,} vs "
        f"{type(right).__name__} of length {len(right):,}"
    )
    with open(_NOTES.log, "a", encoding="utf-8") as log:
        log.write(f"- `{_assert_site()}` in `{_current_test()}`: {result}, {sizes}\n")


def _current_test() -> str:
    import os

    current = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]
    if len(current) > _TEST_ID_SHOWN:
        # A generated parameter can make the id kilobytes long.
        current = current[:_TEST_ID_SHOWN] + "..."
    return current or "no test"


def _assert_site() -> str:
    """The file and line of the assert pytest is explaining."""
    import os
    import sys

    frame = sys._getframe()
    while frame is not None and frame.f_code.co_name != "_call_reprcompare":
        frame = frame.f_back
    caller = frame.f_back if frame is not None else None
    if caller is None:
        return "unknown"
    filename = caller.f_code.co_filename
    try:
        filename = os.path.relpath(filename, _NOTES.root)
    except ValueError:
        # Another drive on Windows.
        filename = os.path.basename(filename)
    return f"{filename.replace(os.sep, '/')}:{caller.f_lineno}"


def _holds(op: str, left: object, right: object) -> bool:
    """Whether a compare pytest is explaining passed. An error counts as no."""
    import operator

    compares = {
        "==": operator.eq,
        "!=": operator.ne,
        "<": operator.lt,
        "<=": operator.le,
        ">": operator.gt,
        ">=": operator.ge,
        "is": operator.is_,
        "is not": operator.is_not,
        "in": lambda item, container: item in container,
        "not in": lambda item, container: item not in container,
    }
    compare = compares.get(op)
    if compare is None:
        return False
    try:
        return bool(compare(left, right))
    except Exception:
        return False


def _big_text_pair(op: str, left: object, right: object) -> bool:
    from tests.text_compare import is_big_text

    return (
        op == "=="
        and isinstance(left, str | bytes)
        and isinstance(right, str | bytes)
        and (is_big_text(left) or is_big_text(right))
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Skip pytest's diff where unneeded, start the log, note the children.

    pytest calls every ``pytest_assertrepr_compare`` and uses the first
    answer, so its own diff would still run after the hook above, for big
    texts and, in an audit run, for every passing compare. It looks up
    ``util.assertrepr_compare`` at call time, so wrapping that skips it.
    Children already running belong to an outer session, such as one that
    runs pytester, and are not counted at the end.
    """
    import multiprocessing
    import os
    from pathlib import Path

    from _pytest.assertion import util

    global _NOTES

    original = util.assertrepr_compare
    if not getattr(original, "skips_big_texts", False):

        def assertrepr_compare(op, left, right, *args, **kwargs):
            if _big_text_pair(op, left, right) or _holds(op, left, right):
                return []
            return original(op, left, right, *args, **kwargs)

        assertrepr_compare.skips_big_texts = True  # type: ignore[attr-defined]
        util.assertrepr_compare = assertrepr_compare

    root = session.config.rootpath
    log = os.environ.get(_LOG_ENV)
    if log:
        log = str(Path(log).resolve())
        if Path(log).is_relative_to(root.resolve()):
            raise pytest.UsageError(f"{_LOG_ENV} must name a file outside {root}")
    else:
        factory = getattr(session.config, "_tmp_path_factory", None)
        log = str(factory.getbasetemp() / _LOG_NAME) if factory else None
    audit = os.environ.get(_AUDIT_ENV) == "1"
    _NOTES = _RunNotes(log, str(root), audit=audit)
    plugins = session.config.pluginmanager
    if audit and not plugins.has_plugin(_AUDIT_PLUGIN):
        plugins.register(_PassingCompares(), _AUDIT_PLUGIN)
    if log is not None:
        with open(log, "w", encoding="utf-8") as file:
            file.write("## Compares of texts over 64 KB\n\n")
            if not audit:
                file.write(
                    "No big-compare audit in this run: only failed compares are "
                    f"listed. Set {_AUDIT_ENV}=1 to list passing ones too.\n\n"
                )

    session.stash[_CHILDREN_BEFORE] = frozenset(
        child.pid for child in multiprocessing.active_children() if child.pid
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Note the worst ``elapsed_ms`` the regex worker reports in each test.

    Only when a collected test imported the session, so a core-only run
    does not load Qt. A session whose ``_finish`` a test replaced reports
    a made-up time, so it is left out.
    """
    import sys

    del session
    module = sys.modules.get("galley.app.regex_session")
    if module is None:
        return
    original = module.RegexSession._finish
    if getattr(original, "notes_elapsed", False):
        return

    def _finish(self, event):
        elapsed = getattr(event, "elapsed_ms", None)
        if elapsed is not None and "_finish" not in vars(self):
            test = _current_test()
            worst = _NOTES.worst_elapsed.get(test, 0.0)
            _NOTES.worst_elapsed[test] = max(worst, elapsed)
        original(self, event)

    _finish.notes_elapsed = True  # type: ignore[attr-defined]
    module.RegexSession._finish = _finish


def _finish_log(session: pytest.Session) -> None:
    """Close the log, and name the counts and the worst time in the terminal."""
    worst = sorted(_NOTES.worst_elapsed.items(), key=lambda item: -item[1])
    if _NOTES.log is not None:
        with open(_NOTES.log, "a", encoding="utf-8") as log:
            if not _NOTES.big_compares:
                log.write("None.\n")
            log.write("\n## Worst regex worker elapsed_ms, by test\n\n")
            for test, elapsed in worst[:_WORST_SHOWN]:
                log.write(f"- {elapsed:,.2f} ms in `{test}`\n")
            if not worst:
                log.write("None.\n")
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    where = f", listed in {_NOTES.log}" if _NOTES.log is not None else ""
    if _NOTES.audit:
        count = f"Compares of texts over 64 KB: {_NOTES.big_compares}"
        reporter.write_line(count + where)
    elif _NOTES.big_compares:
        count = f"Failed compares of texts over 64 KB: {_NOTES.big_compares}"
        reporter.write_line(count + where)
    if worst:
        test, elapsed = worst[0]
        reporter.write_line(f"Worst regex worker elapsed_ms: {elapsed:,.2f} in {test}")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """Finish the log, then fail if a child process the run started is alive.

    ``multiprocessing.active_children()`` lists only the children this
    process started with multiprocessing, and reaps any that have exited.
    The resource tracker is not one of them. A child still exiting, such as
    a worker killed in the last test, gets up to ``_CHILD_EXIT_S`` in all.
    """
    import multiprocessing
    import time

    _finish_log(session)
    before = session.stash.get(_CHILDREN_BEFORE, frozenset())
    children = [
        child for child in multiprocessing.active_children() if child.pid not in before
    ]
    deadline = time.monotonic() + _CHILD_EXIT_S
    for child in children:
        child.join(timeout=max(deadline - time.monotonic(), 0))
    alive = [child for child in children if child.is_alive()]
    if not alive:
        return
    names = ", ".join(f"{child.name} (pid {child.pid})" for child in alive)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"Child processes still alive at the end of the run: {names}",
            red=True,
            bold=True,
        )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
