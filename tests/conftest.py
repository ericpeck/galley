"""Pytest defaults for Galley. Tests always run without a display."""

import os
from pathlib import Path

import pytest
from tests.perf import Budget, budget_for, current_mode

os.environ["QT_QPA_PLATFORM"] = "offscreen"

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
# How many tests the log lists under the worst regex worker times.
_WORST_SHOWN = 10
# How much of a test id the log shows.
_TEST_ID_SHOWN = 200


class _RunNotes:
    """What this run logs, reset when a session starts."""

    def __init__(self, log: str | None = None, root: str = ".") -> None:
        self.log = log
        self.root = root
        self.big_compares = 0
        # The worst elapsed_ms the regex worker reported in each test.
        self.worst_elapsed: dict[str, float] = {}


_NOTES = _RunNotes()


def pytest_assertrepr_compare(op: str, left: object, right: object) -> list[str] | None:
    """Log each ``==`` on a str or bytes over 64 KB, and explain a failed one.

    The log finds the asserts to move to ``assert_same_text``. pytest's own
    difflib diff of two multi-MB texts can run for minutes, so a failed one
    gets a one-line report instead. Smaller values keep pytest's diff.
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


def pytest_assertion_pass() -> None:
    """Make pytest call ``pytest_assertrepr_compare`` for passing asserts.

    ``enable_assertion_pass_hook`` does that only while some plugin has this
    hook. It does nothing itself.
    """


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
    texts and, with the pass hook on, for every passing compare. It looks up
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
    _NOTES = _RunNotes(log, str(root))
    if log is not None:
        with open(log, "w", encoding="utf-8") as file:
            file.write("## Compares of texts over 64 KB\n\n")

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
    reporter.write_line(f"Compares of texts over 64 KB: {_NOTES.big_compares}{where}")
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
