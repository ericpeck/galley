"""Copy As (#66): labels, status text, and where each copy is built.

Copy As works on Output: the selection, or all of Output when nothing is
selected. The text itself comes from :func:`galley.core.mask.copy_as_text`.
A small Output is copied on the UI thread. A larger one goes to the parse
worker, so the window keeps responding, and Cancel or Esc stops it.
"""

from galley.app.limits import _SI_KB, _SI_MB
from galley.core.mask import CopyAsMode

COPY_AS_TITLE = "Copy As"
COPY_AS_BUSY_NAME = "Copying"
COPY_AS_BUSY_MESSAGE = "Copying…"
COPY_AS_PROGRESS_NAME = "Copying progress"
# Cancel or Esc during a Copy As on the worker (Eric, via Chief, on #99).
COPY_CANCELED_MESSAGE = "Copy canceled. Nothing copied."
COPY_AS_NOT_STARTED_MESSAGE = (
    "Nothing copied. The formatter is shut down or already running."
)

# Menu order is Bill's brief, without "Placeholders" (dropped by Eric).
COPY_AS_LABELS: dict[CopyAsMode, str] = {
    "original": "Original",
    "masked": "Masked",
    "fake": "Random fake",
}

# Verbatim from the #66 masking-tightening PR (#88), for the Masked and
# Random fake items.
MASK_HELP = (
    "Masking hides values under keys that look personal or secret, like "
    "email, phone, name, or password, and any value that's just an email or "
    "phone number. It can miss some. Check before you share."
)

COPIED_ORIGINAL = "Copied original (unmasked)"

# Copy As walks the whole tree in Python, so it costs more per byte than
# Format, and YAML loads with the pure-Python parser. Format's busy sizes
# would hold the UI thread for over a second. These sizes keep an inline
# copy near 250 ms on an Apple M-series Mac (1.6 MB JSON took 390 ms for
# Random fake; 1 MB YAML took 6.7 s). Anything at or above them runs on
# the worker.
JSON_COPY_AS_INLINE_BYTES = 1 * _SI_MB
YAML_COPY_AS_INLINE_BYTES = 32 * _SI_KB


def copy_as_inline_limit(fmt: str) -> int:
    """Bytes below which Copy As runs on the UI thread for ``fmt``."""
    if fmt.casefold() == "yaml":
        return YAML_COPY_AS_INLINE_BYTES
    return JSON_COPY_AS_INLINE_BYTES


def copied_text(mode: CopyAsMode, count: int) -> str:
    """Status text after a copy, naming what was copied."""
    if mode == "original":
        return COPIED_ORIGINAL
    values = "1 value" if count == 1 else f"{count:,} values"
    if mode == "masked":
        return f"Copied masked ({values} hidden)"
    return f"Copied random fake ({values} replaced)"


def not_copied_text(
    mode: CopyAsMode, fmt: str, *, invalid: bool, selection: bool
) -> str:
    """Status text when Copy As built nothing. The clipboard is unchanged."""
    if invalid and selection:
        return f"Nothing copied. Select a whole {fmt} value, or clear the selection."
    if invalid:
        return f"Nothing copied. Output isn't valid {fmt}."
    return f"Nothing copied. Couldn't build the {COPY_AS_LABELS[mode].lower()} copy."
