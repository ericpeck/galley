"""Process start configuration for the Galley entry point."""

import multiprocessing


def configure_runtime() -> None:
    """Prepare a frozen spawn child for a future parser subprocess.

    ``freeze_support`` lets a PyInstaller build re-enter this process, and
    ``spawn`` is the start method on every platform Galley will ship to.
    """
    multiprocessing.freeze_support()
    multiprocessing.set_start_method("spawn")
