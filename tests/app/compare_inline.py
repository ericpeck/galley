"""An in-process compare behind ``ParseSession``'s compare signals.

``inline_compare`` replaces ``ParseSession.submit_compare`` so a window test
runs the compare without the spawn worker. Results arrive on the next
event-loop turn through the session's own handlers, Cancel is the session's
own, and ``stop`` reports a worker that exited mid-compare.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from PySide6.QtCore import QTimer

from galley.app.parse_session import ParseSession, _CompareActive
from galley.core.worker import CompareEvent, CompareJob, run_compare


@dataclass
class _Inline:
    """Compare jobs the window sent, delivered on the next event-loop turn.

    ``hold`` keeps jobs running until :meth:`release` or a Cancel.
    """

    hold: bool = False
    sent: list[CompareJob] = field(default_factory=list)
    session: ParseSession | None = None

    def deliver(self, session: ParseSession, job: CompareJob) -> None:
        event = run_compare(job)
        if isinstance(event, CompareEvent):
            session._handle_compare(event)
        else:
            session._handle_compare_failure(event)

    def release(self) -> None:
        assert self.session is not None
        self.deliver(self.session, self.sent[-1])

    def stop(self) -> None:
        """The worker exits mid-compare, as ``compare_stopped`` reports it."""
        session = self.session
        assert session is not None
        job_id = session.active_job_id
        assert job_id is not None
        session._active = None
        session.compare_stopped.emit(job_id)


@pytest.fixture
def inline_compare(monkeypatch) -> Iterator[_Inline]:
    inline = _Inline()

    def _submit(
        self: ParseSession,
        left_text: str,
        left_format: str,
        right_text: str,
        right_format: str,
        *,
        reformat: bool = False,
        hold_s: float = 0.0,
    ) -> int | None:
        del hold_s
        if self._shut_down or self._active is not None:
            return None
        self._job_ids += 1
        job = CompareJob(
            self._job_ids,
            left_text,
            left_format,
            right_text,
            right_format,
            reformat=reformat,
        )
        self._active = _CompareActive(job.job_id)
        inline.session = self
        inline.sent.append(job)
        if not inline.hold:
            QTimer.singleShot(0, lambda: inline.deliver(self, job))
        return job.job_id

    monkeypatch.setattr(ParseSession, "submit_compare", _submit)
    yield inline
