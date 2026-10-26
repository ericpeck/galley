"""Pause Python's cyclic garbage collector around allocation-heavy work.

Loading, diffing, and reporting a large document pair allocate hundreds of
thousands of tuples, lists, and ``Span`` objects that hold no reference
cycles. Each allocation burst triggers a collection, and the older
generations walk every live object of both documents again. On the 1.5 MB
``reformat=True`` compare job that was 54 ms of the 178 ms this pause and
the rest of #129 saved. Reference counting still frees everything while the
collector is paused; only cycle detection waits.

Pauses nest and overlap across threads. A module lock guards a count of
active pauses: the first to start turns the collector off, if it was on,
and the last to end turns it back on, only if it was on before the first
started. This module does not import Qt.
"""

import functools
import gc
import threading
from collections.abc import Callable

_lock = threading.Lock()
_active = 0
_was_enabled = False


def pause_gc[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Run ``function`` with the cyclic collector paused, then restore it.

    The collector comes back in a ``finally``, so an exception restores it
    too. A collector that was already off stays off.
    """

    @functools.wraps(function)
    def paused(*args: P.args, **kwargs: P.kwargs) -> R:
        _start()
        try:
            return function(*args, **kwargs)
        finally:
            _end()

    return paused


def _start() -> None:
    global _active, _was_enabled
    with _lock:
        if _active == 0:
            _was_enabled = gc.isenabled()
            if _was_enabled:
                gc.disable()
        _active += 1


def _end() -> None:
    global _active
    with _lock:
        _active -= 1
        if _active == 0 and _was_enabled:
            gc.enable()
