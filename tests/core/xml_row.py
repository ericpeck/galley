"""The XML table row that #63 slice 3 adds, for slice 2's tests.

Slice 2 keeps XML out of ``FORMATS``, so the pickers and Open don't offer
it. Tests that route XML through ``format_document``, ``run_compare``, or
the worker add this row first, the way slice 3 will. Nothing outside
``tests`` imports this module.
"""

from multiprocessing.queues import Queue

import pytest

from galley.core import formats
from galley.core.formats import DocumentFormat
from galley.core.worker import run_worker


def _looks_like_xml(line: str) -> bool:
    return line.startswith("<")


XML_ROW = DocumentFormat("xml", "XML", (".xml",), _looks_like_xml, False)


def add_xml_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add ``XML_ROW`` to the table for one test."""
    monkeypatch.setattr(formats, "FORMATS", (*formats.FORMATS, XML_ROW))


def run_worker_with_xml_row(job_queue: Queue, event_queue: Queue) -> None:
    """``run_worker`` in a spawn child whose table has ``XML_ROW``."""
    formats.FORMATS = (*formats.FORMATS, XML_ROW)
    run_worker(job_queue, event_queue)
