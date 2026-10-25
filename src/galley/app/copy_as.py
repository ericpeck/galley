"""Copy As (#66): labels, status text, and where each copy is built.

Copy As works on Output: the selection, or all of Output when nothing is
selected. The text itself comes from :func:`galley.core.mask.copy_as_text`.
Only a tiny Output is copied on the UI thread. Anything larger goes to the
parse worker, so the window keeps responding, and Cancel or Esc stops it.
"""

from galley.app.limits import JSON_COPY_AS_INLINE_BYTES, YAML_COPY_AS_INLINE_BYTES
from galley.core.mask import CopyAsMode

COPY_AS_TITLE = "Copy As"
COPY_AS_BUSY_NAME = "Copying"
COPY_AS_BUSY_MESSAGE = "Copying…"
COPY_AS_PROGRESS_NAME = "Copying progress"
# Cancel or Esc during a Copy As on the worker (Eric, via Chief, on #99).
COPY_CANCELED_MESSAGE = "Copy canceled. Nothing copied."
# Every failure, whatever the cause. Error text can quote the Output, so
# none of it reaches the status bar.
COPY_AS_FAILED_MESSAGE = "Nothing copied. Galley couldn't start the copy. Try again."

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
