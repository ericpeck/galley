"""Cancel, rollback, and commit tests for the parse worker."""

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton

from galley.app import parse_session as parse_session_module
from galley.app.banner import FORMATTER_STOPPED
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import CANCELLED_MESSAGE, WRITING_MESSAGE
from galley.app.parse_session import REAP_DEADLINE_MS, ParseSession, _Retired
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
    shown: list[tuple[object, ...]] = []
    session.problems_found.connect(shown.append)
    try:
        session.submit('{"a": "\\uD800"}', "json", "json")
        qtbot.waitUntil(lambda: bool(shown), timeout=_SPAWN_TIMEOUT_MS)
        assert len(shown) == 1
        row = shown[0][0]
        assert row.lineno == 1
        assert row.message == (
            "Line 1, column 8 has an invalid "
            "character escape (\\uD800) that can't be saved as UTF-8."
        )
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
        session._retired.append(
            _Retired(
                process=slow,  # type: ignore[arg-type]
                resource=None,
                queues=[],
                started_s=time.monotonic(),
                temps=[str(path)],
            )
        )
        for _ in range(5):
            assert _tick_ms(session) < 100
            assert session.process is None
            assert path.exists()
        assert joins == []
        assert failures == []
        _expire_reap(session)
        assert _tick_ms(session) < 100
        assert session.process is not None
        assert session.process.pid != 424242
        replacement = session.process
        assert path.exists()
        assert str(path) in session._retired[0].temps
        assert joins == []
        assert failures == []
        slow.exitcode = -9
        session._poll()
        assert joins == []
        assert failures == []
        assert not path.exists()
        assert session.process is replacement
        assert session.reaped_pid == 424242
        assert session.reaped_exitcode == -9
        extra.write_text("later", encoding="utf-8")
        still = _SlowExit()
        still.pid = 434343
        session._retired.append(
            _Retired(
                process=still,  # type: ignore[arg-type]
                resource=None,
                queues=[],
                started_s=time.monotonic(),
                temps=[str(extra)],
            )
        )
        session.shutdown()
        assert not path.exists()
        assert not extra.exists()
    finally:
        session.shutdown()


def test_reap_deadline_keeps_cancelled_when_format_was_not_pressed(
    qtbot, tmp_path: Path
) -> None:
    rig = _stuck_cancel(qtbot, tmp_path)
    session = rig.window._session
    try:
        for _ in range(5):
            rig.ticks.append(_tick_ms(session))
            assert session.process is None
            assert rig.status.text() == CANCELLED_MESSAGE
        _expire_reap(session)
        rig.ticks.append(_tick_ms(session))
        for _ in range(3):
            rig.ticks.append(_tick_ms(session))
        assert rig.status.text() == CANCELLED_MESSAGE
        assert FORMATTER_STOPPED not in rig.status.text()
        assert rig.output.toPlainText() == "previous output\n"
        assert rig.destination.read_bytes() == rig.original
        assert Path(rig.temp_path).exists()
        assert str(rig.temp_path) in session._retired[0].temps
        replacement = session.process
        assert replacement is not None
        assert replacement.pid not in (None, rig.stuck.pid)
        assert replacement.exitcode is None
        assert rig.stuck.exitcode is None
        assert not session.has_pending_job
        assert rig.joins == []
        assert rig.ticks
        assert all(tick < 100 for tick in rig.ticks)
    finally:
        rig.window.shutdown_parse_worker()


def test_format_during_reap_shows_the_crash_banner_and_runs(
    qtbot, tmp_path: Path
) -> None:
    rig = _stuck_cancel(qtbot, tmp_path)
    session = rig.window._session
    try:
        rig.button.click()
        session._timer.stop()
        rig.window._reveal_timer.stop()
        assert rig.window._formatting
        assert session.has_pending_job
        for _ in range(5):
            rig.ticks.append(_tick_ms(session))
            assert session.process is None
            assert rig.status.text() == CANCELLED_MESSAGE
            assert Path(rig.temp_path).exists()
        _expire_reap(session)
        rig.ticks.append(_tick_ms(session))
        assert rig.status.text() == FORMATTER_STOPPED
        lowered = rig.status.text().casefold()
        assert "line" not in lowered
        assert "column" not in lowered
        replacement = session.process
        assert replacement is not None
        assert replacement.pid not in (None, rig.stuck.pid)
        assert replacement.exitcode is None
        assert not session.has_pending_job

        def _formatted() -> bool:
            rig.ticks.append(_tick_ms(session))
            return rig.output.toPlainText() == '{\n  "a": 1\n}\n'

        qtbot.waitUntil(_formatted, timeout=_SPAWN_TIMEOUT_MS)
        assert rig.destination.read_bytes() == rig.original
        assert Path(rig.temp_path).exists()
        assert str(rig.temp_path) in session._retired[0].temps
        assert session.process is replacement
        assert rig.stuck.exitcode is None
        assert rig.joins == []
        assert rig.ticks
        assert all(tick < 100 for tick in rig.ticks)
    finally:
        rig.window.shutdown_parse_worker()


def test_second_cancel_inside_reap_deadline_queues_once(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Cancel, format, and cancel again while the first worker is still alive.

    Both cancels stay inside the reap deadline. A second job must not start
    another replacement, queue a stuck worker twice, or replace the
    Cancelled status with the crash banner.
    """
    rig = _stuck_cancel(qtbot, tmp_path)
    session = rig.window._session
    cancel = rig.window.findChild(QPushButton, "cancelButton")
    assert isinstance(cancel, QPushButton)
    unlinked: list[str | None] = []
    spawned: list[int] = []
    original_unlink = parse_session_module.unlink_temp
    real_spawn = session._spawn

    def _track_unlink(path: str | None) -> None:
        unlinked.append(path)
        original_unlink(path)

    def _spawn_and_count() -> None:
        real_spawn()
        process = session.process
        assert process is not None
        spawned.append(id(process))

    monkeypatch.setattr(parse_session_module, "unlink_temp", _track_unlink)
    session._spawn = _spawn_and_count  # type: ignore[method-assign]
    try:
        assert _within_reap_deadline(session)
        assert session.process is None
        assert session._retired[0].process is rig.stuck
        _poll_within_deadline(rig, session, 3)

        rig.button.click()
        session._timer.stop()
        rig.window._reveal_timer.stop()
        assert rig.window._formatting
        assert session.has_pending_job
        assert _within_reap_deadline(session)
        _poll_within_deadline(rig, session, 3)
        assert session.process is None
        assert rig.status.text() == CANCELLED_MESSAGE

        cancel.show()
        cancel.click()
        session._timer.stop()
        assert not rig.window._formatting
        assert not session.has_pending_job
        assert _within_reap_deadline(session)
        _poll_within_deadline(rig, session, 3)

        assert len(spawned) <= 1
        assert session.process is None
        assert len(session._retired) == 1
        assert session._retired[0].process is rig.stuck
        _assert_stuck_worker_queued_once(session, rig.stuck, rig.temp_path)
        assert rig.temp_path not in unlinked
        assert rig.status.text() == CANCELLED_MESSAGE
        assert FORMATTER_STOPPED not in rig.status.text()
        assert rig.output.toPlainText() == "previous output\n"
        assert rig.destination.read_bytes() == rig.original
        assert rig.joins == []
        assert rig.ticks
        assert all(tick < 100 for tick in rig.ticks)

        rig.window.shutdown_parse_worker()
        assert unlinked.count(rig.temp_path) == 1
        assert not Path(rig.temp_path).exists()
        assert _queued_temps(session) == []
    finally:
        rig.window.shutdown_parse_worker()


def test_second_cancel_after_deadline_keeps_each_child_temp(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Cancel the replacement after the first child's reap deadline.

    Both children stay stuck. Each temp stays queued once, and neither
    file is deleted until its own child exits or the window shuts down.
    The poll and shutdown do not join. After the second Cancel the window
    shows the previous result, with Format enabled and no crash banner.
    """
    rig = _stuck_cancel(qtbot, tmp_path)
    session = rig.window._session
    cancel = rig.window.findChild(QPushButton, "cancelButton")
    assert isinstance(cancel, QPushButton)
    unlinked: list[str | None] = []
    original_unlink = parse_session_module.unlink_temp

    def _track_unlink(path: str | None) -> None:
        unlinked.append(path)
        original_unlink(path)

    class _Stuck:
        def __init__(self, joins: list[str]) -> None:
            self.pid = 626262
            self.exitcode: int | None = None
            self._joins = joins

        def join(self, timeout: float | None = None) -> None:
            self._joins.append("join")
            time.sleep(0.3)

        def kill(self) -> None:
            return

    def _spawn_stuck() -> None:
        job_queue: parse_session_module.Queue = session._ctx.Queue()
        event_queue: parse_session_module.Queue = session._ctx.Queue()
        process = _Stuck(rig.joins)
        queues = [job_queue, event_queue]
        resource = parse_session_module._RunningWorker(process, queues)  # type: ignore[arg-type]
        parse_session_module._LIVE_WORKERS.append(resource)
        parse_session_module._arm_exit_cleanup()
        session._job_queue = job_queue
        session._event_queue = event_queue
        session._process = process  # type: ignore[assignment]
        session._resource = resource

    monkeypatch.setattr(parse_session_module, "unlink_temp", _track_unlink)
    session._spawn = _spawn_stuck  # type: ignore[method-assign]
    second = tmp_path / "next.json"
    second.write_bytes(b'{"old": true}\n')
    try:
        rig.window._formatting = True
        rig.window._sync_format_button()
        assert not rig.button.isEnabled()
        rig.output.setPlainText("partial result\n")
        session.submit(
            '{"b": 2}',
            "json",
            "json",
            destination=str(second),
        )
        temp2 = session.last_temp_path
        assert temp2 is not None
        assert temp2 != rig.temp_path
        assert session.has_pending_job
        assert session._retired[0].process is rig.stuck
        _expire_reap(session)
        rig.ticks.append(_tick_ms(session))
        child2 = session.process
        assert child2 is not None
        assert child2 is not rig.stuck
        assert rig.status.text() == FORMATTER_STOPPED
        assert not session.has_pending_job

        cancel.show()
        cancel.click()
        session._timer.stop()
        assert rig.status.text() == CANCELLED_MESSAGE
        assert FORMATTER_STOPPED not in rig.status.text()
        assert rig.button.isEnabled()
        assert rig.output.toPlainText() == "previous output\n"
        assert not rig.window._formatting
        queued = _queued_temps(session)
        assert queued.count(rig.temp_path) == 1
        assert queued.count(temp2) == 1
        assert len(queued) == len(set(queued))
        assert session._retired[0].process is rig.stuck
        assert session._retired[0].temps == [rig.temp_path]
        assert session._retired[1].process is child2
        assert session._retired[1].temps == [temp2]
        assert Path(rig.temp_path).exists()
        assert Path(temp2).exists()
        assert rig.temp_path not in unlinked
        assert temp2 not in unlinked
        assert rig.destination.read_bytes() == rig.original
        assert second.read_bytes() == b'{"old": true}\n'
        assert rig.joins == []

        child2.exitcode = -9  # type: ignore[attr-defined]
        rig.ticks.append(_tick_ms(session))
        assert not Path(temp2).exists()
        assert Path(rig.temp_path).exists()
        assert unlinked.count(temp2) == 1
        assert rig.temp_path not in unlinked
        remaining = _queued_temps(session)
        assert remaining.count(rig.temp_path) == 1
        assert temp2 not in remaining
        assert any(entry.process is rig.stuck for entry in session._retired)
        assert rig.status.text() == CANCELLED_MESSAGE
        assert rig.joins == []
        assert rig.ticks
        assert all(tick < 100 for tick in rig.ticks)

        rig.window.shutdown_parse_worker()
        assert unlinked.count(rig.temp_path) == 1
        assert unlinked.count(temp2) == 1
        assert not Path(rig.temp_path).exists()
        assert not Path(temp2).exists()
        assert _queued_temps(session) == []
        assert rig.joins == []
    finally:
        rig.window.shutdown_parse_worker()


def test_format_after_second_cancel_past_deadline_stays_quiet(
    qtbot, tmp_path: Path
) -> None:
    """Format right after a second cancel does not announce an earlier reap.

    The first worker is already past ``REAP_DEADLINE_MS``. The second has
    just been cancelled. Format then runs on the replacement. The user did
    not wait on a worker that died, so the crash banner stays hidden.
    """
    rig = _stuck_cancel(qtbot, tmp_path)
    session = rig.window._session
    cancel = rig.window.findChild(QPushButton, "cancelButton")
    editor = rig.window.findChild(CodeEditor, "inputEditor")
    assert isinstance(cancel, QPushButton)
    assert isinstance(editor, CodeEditor)
    real_spawn = session._spawn

    class _Stuck:
        def __init__(self) -> None:
            self.pid = 626262
            self.exitcode: int | None = None

        def join(self, timeout: float | None = None) -> None:
            rig.joins.append("join")
            time.sleep(0.3)

        def kill(self) -> None:
            return

    def _spawn_stuck() -> None:
        job_queue: parse_session_module.Queue = session._ctx.Queue()
        event_queue: parse_session_module.Queue = session._ctx.Queue()
        process = _Stuck()
        queues = [job_queue, event_queue]
        resource = parse_session_module._RunningWorker(process, queues)  # type: ignore[arg-type]
        parse_session_module._LIVE_WORKERS.append(resource)
        parse_session_module._arm_exit_cleanup()
        session._job_queue = job_queue
        session._event_queue = event_queue
        session._process = process  # type: ignore[assignment]
        session._resource = resource

    session._spawn = _spawn_stuck  # type: ignore[method-assign]
    try:
        _expire_reap(session)
        rig.ticks.append(_tick_ms(session))
        child2 = session.process
        assert child2 is not None
        assert child2 is not rig.stuck
        assert rig.status.text() == CANCELLED_MESSAGE
        assert FORMATTER_STOPPED not in rig.status.text()
        assert _elapsed_ms(session._retired[0].started_s) >= REAP_DEADLINE_MS

        editor.setPlainText('{"b": 2}')
        rig.button.click()
        session._timer.stop()
        rig.window._reveal_timer.stop()
        assert session.process is child2
        assert not session.has_pending_job
        cancel.show()
        cancel.click()
        session._timer.stop()
        assert rig.status.text() == CANCELLED_MESSAGE
        assert FORMATTER_STOPPED not in rig.status.text()
        assert rig.button.isEnabled()
        assert session.process is None
        assert len(session._retired) == 2
        assert session._retired[0].process is rig.stuck
        assert session._retired[1].process is child2
        assert _elapsed_ms(session._retired[0].started_s) >= REAP_DEADLINE_MS
        assert _elapsed_ms(session._retired[1].started_s) < REAP_DEADLINE_MS

        session._spawn = real_spawn  # type: ignore[method-assign]
        failures: list[str] = []
        session.failed.connect(failures.append)
        editor.setPlainText('{"c": 3}')
        rig.button.click()
        session._timer.stop()
        rig.window._reveal_timer.stop()
        assert rig.window._formatting
        assert session.has_pending_job
        rig.ticks.append(_tick_ms(session))

        assert FORMATTER_STOPPED not in failures
        assert FORMATTER_STOPPED not in rig.status.text()
        replacement = session.process
        assert replacement is not None
        assert replacement is not rig.stuck
        assert replacement is not child2
        assert replacement.pid not in (None, rig.stuck.pid, child2.pid)
        assert replacement.exitcode is None
        assert not session.has_pending_job
        expected = format_json('{"c": 3}')

        def _formatted() -> bool:
            rig.ticks.append(_tick_ms(session))
            return rig.output.toPlainText() == expected

        qtbot.waitUntil(_formatted, timeout=_SPAWN_TIMEOUT_MS)
        assert session.process is replacement
        assert session.process.exitcode is None
        assert FORMATTER_STOPPED not in failures
        assert FORMATTER_STOPPED not in rig.status.text()
        assert rig.output.toPlainText() == expected
        assert rig.joins == []
        assert rig.ticks
        assert all(tick < 100 for tick in rig.ticks)
    finally:
        rig.window.shutdown_parse_worker()


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


class _StuckCancel:
    def __init__(
        self,
        destination: Path,
        original: bytes,
        window,
        status: QLabel,
        output: CodeEditor,
        button: QPushButton,
        joins: list[str],
        ticks: list[float],
        stuck: object,
        temp_path: str,
    ) -> None:
        self.destination = destination
        self.original = original
        self.window = window
        self.status = status
        self.output = output
        self.button = button
        self.joins = joins
        self.ticks = ticks
        self.stuck = stuck
        self.temp_path = temp_path


def _stuck_cancel(qtbot, tmp_path: Path) -> _StuckCancel:
    """Cancel a job whose worker never sets ``exitcode``."""
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
    assert isinstance(status, QLabel)
    assert isinstance(button, QPushButton)
    assert isinstance(cancel, QPushButton)
    previous = "previous output\n"
    output.setPlainText(previous)
    editor.setPlainText('{"a": 1}')
    joins: list[str] = []

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
    cancel.show()
    cancel.click()
    session._timer.stop()
    assert status.text() == CANCELLED_MESSAGE
    assert session.process is None
    assert str(temp_path) in _queued_temps(session)
    return _StuckCancel(
        destination,
        original,
        window,
        status,
        output,
        button,
        joins,
        [],
        stuck,
        temp_path,
    )


def _queued_temps(session: ParseSession) -> list[str]:
    queued: list[str] = []
    for entry in session._retired:
        queued.extend(entry.temps)
    return queued


def _expire_reap(session: ParseSession) -> None:
    overdue_s = (REAP_DEADLINE_MS + 1000) / 1000
    session._retired[0].started_s = time.monotonic() - overdue_s


def _elapsed_ms(started_s: float) -> float:
    return (time.monotonic() - started_s) * 1000


def _within_reap_deadline(session: ParseSession) -> bool:
    assert session._retired
    started = session._retired[0].started_s
    elapsed = (time.monotonic() - started) * 1000
    return elapsed < REAP_DEADLINE_MS


def _poll_within_deadline(rig: _StuckCancel, session: ParseSession, times: int) -> None:
    """Poll inside the deadline. A replacement must not be running yet."""
    for _ in range(times):
        assert _within_reap_deadline(session)
        rig.ticks.append(_tick_ms(session))
        assert session.process is None


def _assert_stuck_worker_queued_once(
    session: ParseSession, worker: object, path: str
) -> None:
    """A worker that never exits is queued once and is not also reaped."""
    exitcode = getattr(worker, "exitcode", None)
    queued = _queued_temps(session)
    assert exitcode is None
    assert queued.count(path) == 1
    assert len(queued) == len(set(queued))
    assert Path(path).exists()


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
