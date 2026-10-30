"""Control what the find session sees of time, so timeout tests don't depend
on how fast the runner is.

The cutoff uses the ``elapsed_ms`` the worker reports with each answer, and
the watchdog uses the session's own clock. These helpers fake one or both.
"""

import dataclasses

import pytest

import galley.app.regex_session as regex_session_module
from galley.app.regex_session import RegexSession


class FakeClock:
    """Stands in for ``time`` in regex_session. ``monotonic()`` reads ``now``.

    It starts at a whole number of seconds, so whole milliseconds after it
    compare exactly.
    """

    _START = 100.0

    def __init__(self) -> None:
        self.now = self._START

    def monotonic(self) -> float:
        return self.now

    def set_ms_after_start(self, milliseconds: float) -> None:
        self.now = self._START + milliseconds / 1000


def freeze_watchdog(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Stop the session's clock. The watchdog fires only when a test moves it."""
    clock = FakeClock()
    monkeypatch.setattr(regex_session_module, "time", clock)
    return clock


def report_elapsed(
    monkeypatch: pytest.MonkeyPatch, session: RegexSession, elapsed_ms: float
) -> list[object]:
    """Give each answer this ``elapsed_ms``, whatever the worker measured.

    Returns the answers the session reads, in order.
    """
    finish = session._finish
    read: list[object] = []

    def finish_with(event: object) -> None:
        event = dataclasses.replace(event, elapsed_ms=elapsed_ms)
        read.append(event)
        finish(event)

    monkeypatch.setattr(session, "_finish", finish_with)
    return read


def hold_answers(monkeypatch: pytest.MonkeyPatch, session: RegexSession) -> None:
    """The session never reads the worker's events, as if it never answered."""
    monkeypatch.setattr(session, "_drain", lambda: None)
