"""Pause Python's cyclic garbage collector around allocation-heavy work.

Loading, diffing, and reporting a large document pair allocate hundreds of
thousands of tuples, lists, and ``Span`` objects that hold no reference
cycles. Each allocation burst triggers a collection, and the older
generations walk every live object of both documents again: about a fifth
of a 1.5 MB compare job (#129). Reference counting still frees everything
as usual while the collector is paused; only cycle detection waits, and it
runs again as soon as the outermost paused call returns.

This module does not import Qt.
"""

import functools
import gc
from collections.abc import Callable


def pause_gc[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Run ``function`` with the cyclic collector paused, then restore it.

    Nested calls are safe: only the call that paused the collector turns it
    back on, and an exception still turns it back on.
    """

    @functools.wraps(function)
    def paused(*args: P.args, **kwargs: P.kwargs) -> R:
        if not gc.isenabled():
            return function(*args, **kwargs)
        gc.disable()
        try:
            return function(*args, **kwargs)
        finally:
            gc.enable()

    return paused
