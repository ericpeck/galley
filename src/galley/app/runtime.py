"""Process start configuration for the Galley entry point."""

import multiprocessing


def configure_runtime() -> None:
    """Let a frozen executable re-enter this process.

    Spawn children are created from ``multiprocessing.get_context("spawn")``.
    This does not set the process-wide start method. ``set_start_method``
    raises when one process calls it twice, including from tests.
    """
    multiprocessing.freeze_support()
