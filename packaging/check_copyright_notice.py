"""Fail a v* tag while the About copyright line is still a placeholder.

About is release-gated. ``galley.app.about`` (``src/galley/app/about.py``)
may be absent; a tag must not ship until ``COPYRIGHT_NOTICE`` is the final
wording and no longer contains TBD.
"""

import importlib

MODULE = "galley.app.about"
ATTRIBUTE = "COPYRIGHT_NOTICE"
OK = "Copyright notice: ok"


def problem(notice: object) -> str | None:
    """Return why ``notice`` cannot ship, or None when it can."""
    label = f"{MODULE}.{ATTRIBUTE}"
    if not isinstance(notice, str) or notice.strip() == "":
        return f"{label} is empty."
    if "tbd" in notice.casefold():
        return f"{label} still contains TBD."
    return None


def main() -> int:
    """Read ``COPYRIGHT_NOTICE``. Return 0 only when it can ship."""
    label = f"{MODULE}.{ATTRIBUTE}"
    try:
        module = importlib.import_module(MODULE)
    except ImportError as exc:
        print(f"{label}: could not import {MODULE} ({exc}).")
        return 1
    if not hasattr(module, ATTRIBUTE):
        print(f"{label} is missing.")
        return 1
    failure = problem(getattr(module, ATTRIBUTE))
    if failure is not None:
        print(failure)
        return 1
    print(OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
