"""Cancel, rollback, and commit tests for the parse worker."""

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton

from galley.app.banner import FORMATTER_STOPPED
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import CANCELLED_MESSAGE, WRITING_MESSAGE
from galley.app.parse_session import REAP_DEADLINE_MS, ParseSession
from galley.core.format import format_json
from galley.core.worker import FAULT_AFTER_TEMP

_SPAWN_TIMEOUT_MS = 20000
_SLOW_CHUNK = 4096
_SLOW_PAUSE_S = 0.2


def test_cancel_mid_write_leaves_the_original_file(qtbot, tmp_path: Path) -> None:
    destination, original = _original_file(tmp_path)
    session = _started(qtbot)
    try:
        _submit_slow(session, destination)
        qtbot.waitUntil(_mid_write(session), timeout=_SPAWN_TIMEOUT_MS)
        pid = session.process.pid if session.process is not None else None
        session.cancel()
        assert session.reaped_exitcode is None
        qtbot.waitUntil(lambda: session.reaped_exitcode is not None, timeout=10000)
        assert destination.read_bytes() == original
        assert _temps(tmp_path) == []
        assert session.reaped_pid == pid
        _assert_reaped(pid)
        assert session.reaped_process is not None
        assert not session.reaped_process.is_alive()
    finally:
        session.shutdown()


def test_cancel_uses_fresh_queues_for_the_next_job(qtbot, tmp_path: Path) -> None:
    destination, _original = _original_file(tmp_path)
    session = _started(qtbot)
    results: list[str] = []
    session.succeeded.connect(results.append)
    try:
        old_jobs = session.job_queue
        old_events = session.event_queue
        assert old_jobs is not None
        assert old_events is not None
        _submit_slow(session, destination)
        qtbot.waitUntil(_mid_write(session), timeout=_SPAWN_TIMEOUT_MS)
        session.cancel()
        qtbot.waitUntil(
            lambda: session.job_queue is not None and session.job_queue is not old_jobs,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert session.event_queue is not old_events
        assert session.reaped_exitcode is not None
        session.submit('{"a": 1}', "json", "json")
        qtbot.waitUntil(lambda: bool(results), timeout=_SPAWN_TIMEOUT_MS)
        assert results == [format_json('{"a": 1}')]
        assert destination.read_bytes() == _original
        assert _temps(tmp_path) == []
    finally:
        session.shutdown()


def test_worker_failure_keeps_the_original_and_removes_the_temp(
    qtbot, tmp_path: Path
) -> None:
    destination, original = _original_file(tmp_path)
    session = _started(qtbot)
    failures: list[str] = []
    session.failed.connect(failures.append)
    try:
        pid = session.process.pid if session.process is not None else None
        session.submit(
            '{"a": 1}',
            "json",
            "json",
            destination=str(destination),
            fault=FAULT_AFTER_TEMP,
        )
        qtbot.waitUntil(lambda: bool(failures), timeout=_SPAWN_TIMEOUT_MS)
        assert failures == ["forced failure"]
        assert destination.read_bytes() == original
        assert session.last_temp_path is not None
        assert Path(session.last_temp_path).parent == destination.parent
        assert _temps(tmp_path) == []
        assert session.process is not None
        assert session.process.pid == pid
        assert session.process.exitcode is None
    finally:
        session.shutdown()


def test_input_error_is_shown_as_a_banner(qtbot) -> None:
    session = _started(qtbot)
    messages: list[str] = []
    session.failed.connect(messages.append)
    try:
        session.submit('{"a": "\\uD800"}', "json", "json")
        qtbot.waitUntil(lambda: bool(messages), timeout=_SPAWN_TIMEOUT_MS)
        assert messages == [
            "Line 1, column 8 has an invalid "
            "character escape (\\uD800) that can't be saved as UTF-8."
        ]
    finally:
        session.shutdown()


def test_success_replaces_the_destination(qtbot, tmp_path: Path) -> None:
    destination = tmp_path / "out.json"
    destination.write_bytes(b"OLD")
    text = '{"a": 1}'
    expected = format_json(text).encode()
    session = _started(qtbot)
    results: list[str] = []
    session.succeeded.connect(results.append)
    try:
        session.submit(text, "json", "json", destination=str(destination))
        qtbot.waitUntil(lambda: bool(results), timeout=_SPAWN_TIMEOUT_MS)
        assert results == [format_json(text)]
        assert destination.read_bytes() == expected
        assert _temps(tmp_path) == []
    finally:
        session.shutdown()


def test_cancel_updates_the_window_before_the_worker_is_killed(
    qtbot, tmp_path: Path
) -> None:
    destination, original = _original_file(tmp_path)
    window = _show(qtbot)
    window.start_parse_worker()
    session = window._session
    qtbot.waitUntil(_warm(session), timeout=_SPAWN_TIMEOUT_MS)
    previous = '{"keep": true}\n'
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(cancel, QPushButton)
    output.setPlainText(previous)
    window._output_before_format = previous
    window._formatting = True
    _submit_slow(session, destination)
    qtbot.waitUntil(
        lambda: _mid_write(session)() and bar.isVisible(),
        timeout=_SPAWN_TIMEOUT_MS,
    )
    assert status.text() == WRITING_MESSAGE
    process = session.process
    assert process is not None
    joined: list[str] = []
    killed: list[str] = []
    real_join = process.join
    real_kill = process.kill

    def _join(*args: object, **kwargs: object) -> None:
        joined.append("join")
        real_join(*args, **kwargs)

    def _kill() -> None:
        killed.append(status.text())
        killed.append(output.toPlainText())
        killed.append("hidden" if not bar.isVisible() else "visible")
        real_kill()

    process.join = _join  # type: ignore[method-assign]
    process.kill = _kill  # type: ignore[method-assign]
    cancel.click()
    assert joined == []
    assert killed == [CANCELLED_MESSAGE, previous, "hidden"]
    assert session.reaped_exitcode is None
    qtbot.waitUntil(lambda: session.reaped_exitcode is not None, timeout=10000)
    assert joined == []
    assert destination.read_bytes() == original
    assert _temps(tmp_path) == []
    _assert_reaped(session.reaped_pid)
    assert not bar.isVisible()
    assert status.text() == CANCELLED_MESSAGE
    assert output.toPlainText() == previous


def test_reap_poll_stays_fast_and_replaces_a_stuck_child(qtbot, tmp_path: Path) -> None:
    session = ParseSession()
    path = tmp_path / ".galley-slow.tmp"
    path.write_text("pending", encoding="utf-8")
    extra = tmp_path / ".galley-later.tmp"
    joins: list[str] = []
    failures: list[str] = []
    session.failed.connect(failures.append)

    class _SlowExit:
        pid = 424242
        exitcode: int | None = None

        def join(self, timeout: float | None = None) -> None:
            joins.append("join")
            time.sleep(0.3)

        def kill(self) -> None:
            return

    try:
        slow = _SlowExit()
        session._retired_process = slow  # type: ignore[assignment]
        session._temps_to_delete = [str(path)]
        session._reap_started_s = time.monotonic()
        for _ in range(5):
            assert _tick_ms(session) < 100
            assert session.process is None
            assert path.exists()
        assert joins == []
        assert failures == []
        session._reap_started_s = time.monotonic() - (REAP_DEADLINE_MS + 1000) / 1000
        assert _tick_ms(session) < 100
        assert session.process is not None
        assert session.process.pid != 424242
        replacement = session.process
        assert path.exists()
        assert str(path) in session._deferred_temps
        assert joins == []
        assert failures == [FORMATTER_STOPPED]
        slow.exitcode = -9
        session._poll()
        assert joins == []
        assert failures == [FORMATTER_STOPPED]
        assert not path.exists()
        assert session.process is replacement
        assert session.reaped_pid == 424242
        assert session.reaped_exitcode == -9
        extra.write_text("later", encoding="utf-8")
        session._deferred_temps.append(str(extra))
        session.shutdown()
        assert not path.exists()
        assert not extra.exists()
    finally:
        session.shutdown()


def test_stuck_killed_worker_shows_the_crash_banner_and_starts_a_worker(
    qtbot, tmp_path: Path
) -> None:
    """A killed worker whose exitcode stays unset must not block Format."""
    destination, original = _original_file(tmp_path)
    window = _show(qtbot)
    session = window._session
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    button = window.findChild(QPushButton, "formatButton")
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(button, QPushButton)
    assert isinstance(cancel, QPushButton)
    previous = "previous output\n"
    output.setPlainText(previous)
    editor.setPlainText('{"a": 1}')
    joins: list[str] = []
    ticks: list[float] = []

    class _NeverExits:
        pid = 515151

        @property
        def exitcode(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            joins.append("join")
            time.sleep(0.3)

        def kill(self) -> None:
            return

    stuck = _NeverExits()
    session._process = stuck  # type: ignore[assignment]
    session.submit(
        '{"keep": true}',
        "json",
        "json",
        destination=str(destination),
    )
    temp_path = session.last_temp_path
    assert temp_path is not None
    assert Path(temp_path).exists()
    window._formatting = True
    window._output_before_format = previous
    try:
        cancel.show()
        cancel.click()
        assert status.text() == CANCELLED_MESSAGE
        assert output.toPlainText() == previous
        assert destination.read_bytes() == original
        assert session.process is None
        assert str(temp_path) in session._temps_to_delete
        session._timer.stop()

        button.click()
        session._timer.stop()
        window._reveal_timer.stop()
        assert window._formatting
        assert status.text() != FORMATTER_STOPPED
        for _ in range(5):
            ticks.append(_tick_ms(session))
            assert session.process is None
            assert status.text() != FORMATTER_STOPPED
            assert Path(temp_path).exists()

        session._reap_started_s = time.monotonic() - (REAP_DEADLINE_MS + 1000) / 1000
        ticks.append(_tick_ms(session))
        for _ in range(3):
            ticks.append(_tick_ms(session))

        assert status.text() == FORMATTER_STOPPED
        lowered = status.text().casefold()
        assert "line" not in lowered
        assert "column" not in lowered
        assert output.toPlainText() == previous
        assert destination.read_bytes() == original
        assert Path(temp_path).exists()
        assert str(temp_path) in session._deferred_temps
        replacement = session.process
        assert replacement is not None
        assert replacement.pid not in (None, stuck.pid)
        assert replacement.exitcode is None
        assert stuck.exitcode is None
        assert joins == []
        assert ticks
        assert all(tick < 100 for tick in ticks)
        assert button.isEnabled()

        session._timer.timeout.disconnect(session._poll)

        def _timed_poll() -> None:
            ticks.append(_tick_ms(session))

        session._timer.timeout.connect(_timed_poll)
        editor.setPlainText('{"b": 2}')
        button.click()
        qtbot.waitUntil(
            lambda: output.toPlainText() == '{\n  "b": 2\n}\n',
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert session.process is replacement
        assert replacement.exitcode is None
        assert stuck.exitcode is None
        assert destination.read_bytes() == original
        assert Path(temp_path).exists()
        assert joins == []
        assert all(tick < 100 for tick in ticks)
        window.shutdown_parse_worker()
        assert not Path(temp_path).exists()
    finally:
        window.shutdown_parse_worker()


def test_format_after_a_crash_uses_a_fresh_worker(qtbot, tmp_path: Path) -> None:
    destination, original = _original_file(tmp_path)
    window = _show(qtbot)
    window.start_parse_worker()
    session = window._session
    qtbot.waitUntil(_warm(session), timeout=_SPAWN_TIMEOUT_MS)
    previous = "previous output\n"
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(button, QPushButton)
    output.setPlainText(previous)
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    window._formatting = True
    window._output_before_format = previous
    try:
        _submit_slow(session, destination)
        qtbot.waitUntil(_mid_write(session), timeout=_SPAWN_TIMEOUT_MS)
        process = session.process
        assert process is not None
        crashed_pid = process.pid
        assert crashed_pid is not None
        process.kill()
        qtbot.waitUntil(
            lambda: status.text() == FORMATTER_STOPPED,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert "line" not in status.text().casefold()
        assert "column" not in status.text().casefold()
        assert output.toPlainText() == previous
        assert destination.read_bytes() == original
        assert button.text() == idle_text

        def _replaced() -> bool:
            replacement = session.process
            return (
                replacement is not None
                and replacement.pid not in (None, crashed_pid)
                and replacement.exitcode is None
            )

        qtbot.waitUntil(_replaced, timeout=_SPAWN_TIMEOUT_MS)
        replacement = session.process
        assert replacement is not None
        fresh_pid = replacement.pid
        qtbot.waitUntil(lambda: button.isEnabled(), timeout=5000)
        editor.setPlainText('{"b": 2}')
        button.click()
        qtbot.waitUntil(
            lambda: output.toPlainText() == '{\n  "b": 2\n}\n',
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert session.process is not None
        assert session.process.pid == fresh_pid
        assert session.process.exitcode is None
        assert destination.read_bytes() == original
        assert button.text() == idle_text
        _assert_reaped(crashed_pid)
    finally:
        window.shutdown_parse_worker()


def _tick_ms(session: ParseSession) -> float:
    started = time.perf_counter()
    session._poll()
    return (time.perf_counter() - started) * 1000


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _started(qtbot) -> ParseSession:
    session = ParseSession()
    session.start()
    qtbot.waitUntil(_warm(session), timeout=_SPAWN_TIMEOUT_MS)
    return session


def _warm(session: ParseSession):
    def _ready() -> bool:
        process = session.process
        return (
            session.job_queue is not None
            and process is not None
            and process.pid is not None
        )

    return _ready


def _original_file(tmp_path: Path) -> tuple[Path, bytes]:
    destination = tmp_path / "out.json"
    original = b'{"keep": true}\n'
    destination.write_bytes(original)
    return destination, original


def _submit_slow(session: ParseSession, destination: Path) -> None:
    text = json.dumps({"blob": "x" * 80000})
    session.submit(
        text,
        "json",
        "json",
        destination=str(destination),
        chunk_size=_SLOW_CHUNK,
        chunk_pause_s=_SLOW_PAUSE_S,
    )


def _mid_write(session: ParseSession):
    def _partial() -> bool:
        return session.total > 0 and 0 < session.written < session.total

    return _partial


def _temps(directory: Path) -> list[Path]:
    return sorted(directory.glob(".galley-*"))


def _assert_reaped(pid: int | None) -> None:
    assert pid is not None
    if sys.platform == "win32":
        return
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return
    assert waited != pid, "worker is a zombie"
    raise AssertionError(f"worker pid {pid} is still running")
