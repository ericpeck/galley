"""Copy As text: Original, Masked and Random fake. This module does not import Qt.

``copy_as_text`` takes the Output text (or a selection of it) and its
format. Original is that text as it is. Masked and Random fake load it with
``format.load_document``, rewrite it with ``mask.copy_as_result`` and write
it back with ``format.dump_value``, so the copy keeps the structure and the
format of the Output. The Output itself is never changed.
"""

import random
from dataclasses import dataclass

from galley.core.format import dump_value, load_document
from galley.core.mask import CopyAsMode, MaskRules, copy_as_result


@dataclass(frozen=True)
class CopyAsResult:
    """The text to put on the clipboard.

    ``count`` is the number of values hidden (Masked) or replaced (Random
    fake). It is 0 for Original.
    """

    mode: CopyAsMode
    text: str
    count: int


class CopyAsError(Exception):
    """Copy As built no text, so nothing may be copied.

    ``invalid`` is True when the text did not load as its format, and False
    when it loaded but masking or writing it failed.
    """

    def __init__(self, message: str, *, invalid: bool) -> None:
        super().__init__(message)
        self.invalid = invalid


def copy_as_text(
    text: str,
    fmt: str,
    mode: CopyAsMode,
    rules: MaskRules | None = None,
    rng: random.Random | None = None,
) -> CopyAsResult:
    """Return the Copy As text for ``mode``. Every failure is a CopyAsError.

    ``fmt`` is ``json`` or ``yaml`` (case-insensitive), for both reading and
    writing. ``rng`` is for tests. Random fake uses ``random.SystemRandom``
    without one, so every copy gets new values.
    """
    if mode == "original":
        return CopyAsResult(mode, text, 0)
    try:
        value = load_document(text, fmt)
    except Exception as exc:
        raise CopyAsError(_reason(exc), invalid=True) from exc
    try:
        result = copy_as_result(value, mode, rules, rng)
        copied = dump_value(result.value, fmt)
    except Exception as exc:
        raise CopyAsError(_reason(exc), invalid=False) from exc
    return CopyAsResult(mode, copied, result.masked_count)


def _reason(exc: Exception) -> str:
    message = str(exc)
    return message or type(exc).__name__
