"""Pytest defaults for Galley. Tests always run without a display."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
