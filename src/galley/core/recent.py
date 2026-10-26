"""The File › Open Recent list: order, de-duplication, the cap, and labels.

Entries are path strings, newest first. Nothing here touches the disk or
settings; :mod:`galley.app.recent_files` stores the list and builds the menu.
"""

import os
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import PurePath

RECENT_LIMIT = 10


def add_recent(
    entries: Sequence[str], path: str, limit: int = RECENT_LIMIT
) -> list[str]:
    """Return ``entries`` with ``path`` first, once, capped at ``limit``."""
    key = _key(path)
    rest = [entry for entry in entries if _key(entry) != key]
    return [path, *rest][:limit]


def clean_recent(values: Iterable[object], limit: int = RECENT_LIMIT) -> list[str]:
    """Keep the non-empty path strings from stored ``values``, once each."""
    entries: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        key = _key(value)
        if key in seen:
            continue
        seen.add(key)
        entries.append(value)
    return entries[:limit]


def recent_labels(entries: Sequence[str]) -> list[str]:
    """Return the visible text for each entry: the file name.

    When two entries share a name, both also show their parent folder, as
    ``data.json — configs``. The full path never appears in a label.
    """
    paths = [PurePath(entry) for entry in entries]
    counts = Counter(path.name.casefold() for path in paths)
    labels: list[str] = []
    for path in paths:
        folder = path.parent.name
        if counts[path.name.casefold()] > 1 and folder:
            labels.append(f"{path.name} — {folder}")
        else:
            labels.append(path.name)
    return labels


def _key(path: str) -> str:
    # Windows paths match whatever their case or separators.
    return os.path.normcase(os.path.normpath(path))
