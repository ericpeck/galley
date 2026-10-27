"""The HTML table row that #63 slice 5 adds, for slice 4's tests.

Slice 4 keeps HTML out of ``FORMATS``, so the pickers and Open don't offer
it. Tests that route HTML through ``format_document``, ``run_compare``, or
the worker add this row first, the way slice 5 will. Nothing outside
``tests`` imports this module.
"""

from multiprocessing.queues import Queue

import pytest

from galley.core import formats
from galley.core.formats import DocumentFormat
from galley.core.worker import run_worker


def _looks_like_html(line: str) -> bool:
    return line.casefold().startswith(("<!doctype html", "<html"))


HTML_ROW = DocumentFormat("html", "HTML", (".html", ".htm"), _looks_like_html, False)


def add_html_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add ``HTML_ROW`` to the table for one test."""
    monkeypatch.setattr(formats, "FORMATS", (*formats.FORMATS, HTML_ROW))


def run_worker_with_html_row(job_queue: Queue, event_queue: Queue) -> None:
    """``run_worker`` in a spawn child whose table has ``HTML_ROW``."""
    formats.FORMATS = (*formats.FORMATS, HTML_ROW)
    run_worker(job_queue, event_queue)
