"""Turn a ``DiffResult`` into what the Compare view shows.

This module does not import Qt. ``build_report`` runs in the worker right
after ``diff``, so the UI thread receives rows that are ready to show:

- one ``ChangeRow`` per ``Change``, in engine order, with its kind label,
  glyph, key path and spoken name;
- for each array compared by position (``DiffResult.unaligned``), one
  ``InfoRow`` above that array's changes, and one ``GroupRow`` per run of
  consecutive differing indexes in it (Bill's B4 and PR 3 addendum on #65);
- the lines each pane marks in the gutter and tints;
- line anchors for Link Scrolling, packed as ints and capped at
  ``ANCHOR_LIMIT`` pairs.

Every ``Change`` appears exactly once, as a top-level row or a group child.
``CompareReport.count`` counts every change, grouped ones included. Info
rows are not changes. Lines are 1-based, like ``Span``.
"""

from array import array
from bisect import bisect_left
from dataclasses import dataclass, field

from galley.core.diff import Change, ChangeKind, DiffResult, Doc, Path, Span, Summary
from galley.core.keypath import render_path

# Bill's labels and glyphs (#65 v1 spec): "−" is U+2212 and "⇄" is U+21C4.
KIND_LABELS: dict[ChangeKind, str] = {
    "added": "Added",
    "removed": "Removed",
    "changed": "Changed",
    "moved": "Moved",
}
KIND_GLYPHS: dict[ChangeKind, str] = {
    "added": "+",
    "removed": "−",
    "changed": "~",
    "moved": "⇄",
}
# The root path's label (B3).
ROOT_LABEL = "Whole document"
UNALIGNED_TEXT = "{path} items: compared by position (too complex to align)."
GROUP_TEXT = "{path}: {count} items differ"

# Most (left line, right line) pairs one report carries. Each pair packs to
# two unsigned ints, so the payload stays under ``ANCHOR_LIMIT * 8`` bytes
# however large the documents are. One anchor per few lines of a 1.5 MB
# document is still finer than a scroll step.
ANCHOR_LIMIT = 10_000


@dataclass(frozen=True)
class ChangeRow:
    """One change, as the Changes pane shows it.

    ``path_text`` is the change's key path, or ``ROOT_LABEL`` for the root.
    ``old`` and ``new`` are the engine's summaries; the side without a value
    is ``None``. ``moved_to_text`` is the right path of a Moved change. The
    spans are the change's own, for jumping and selecting.
    """

    kind: ChangeKind
    glyph: str
    label: str
    path_text: str
    old: Summary | None
    new: Summary | None
    moved_to_text: str | None
    left_span: Span | None
    right_span: Span | None
    accessible_name: str

    @property
    def left_line(self) -> int | None:
        return None if self.left_span is None else self.left_span.line

    @property
    def right_line(self) -> int | None:
        return None if self.right_span is None else self.right_span.line


@dataclass(frozen=True)
class InfoRow:
    """A muted note above the changes of an array compared by position.

    It is not a change: it is not counted and navigation skips it.
    """

    path_text: str
    text: str

    @property
    def accessible_name(self) -> str:
        return self.text


@dataclass(frozen=True)
class GroupRow:
    """A run of consecutive differing indexes in an array compared by position.

    ``path_text`` reads like ``users[40–212]`` and ``text`` like
    ``users[40–212]: 173 items differ``. ``children`` holds the run's change
    rows in engine order, plus the info row of any array inside the run that
    was also compared by position.
    """

    path_text: str
    first: int
    last: int
    text: str
    children: tuple["ChangeRow | InfoRow", ...]

    @property
    def item_count(self) -> int:
        return self.last - self.first + 1

    @property
    def count(self) -> int:
        """Changes in this group, for the difference count."""
        return sum(1 for child in self.children if isinstance(child, ChangeRow))

    @property
    def accessible_name(self) -> str:
        return self.text

    @property
    def left_line(self) -> int | None:
        """First left line among the children, where a collapsed group jumps."""
        return next(
            (
                child.left_line
                for child in self.children
                if isinstance(child, ChangeRow) and child.left_line is not None
            ),
            None,
        )

    @property
    def right_line(self) -> int | None:
        return next(
            (
                child.right_line
                for child in self.children
                if isinstance(child, ChangeRow) and child.right_line is not None
            ),
            None,
        )


ReportRow = ChangeRow | GroupRow | InfoRow


@dataclass(frozen=True)
class SideMarks:
    """What one pane marks.

    ``markers`` maps the first line of each change's span on this side to
    its kind; when changes share a line, the first in engine order wins.
    ``tints`` lists ``(first line, last line, kind)`` for each span, in
    engine order. Removed has no right span and Added no left span, so a
    Removed change marks Left only and an Added change Right only.
    """

    markers: dict[int, ChangeKind] = field(default_factory=dict)
    tints: tuple[tuple[int, int, ChangeKind], ...] = ()


@dataclass(frozen=True)
class CompareReport:
    """Everything the Compare view needs from one comparison.

    ``rows`` is empty when the documents match. ``count`` is the number of
    changes, grouped ones included. ``anchors`` is ``pack_anchors`` output:
    (left line, right line) pairs, strictly increasing on both sides.
    """

    rows: tuple[ReportRow, ...]
    count: int
    left: SideMarks
    right: SideMarks
    anchors: bytes = b""

    def anchor_pairs(self) -> list[tuple[int, int]]:
        return unpack_anchors(self.anchors)


def build_report(
    result: DiffResult, left: Doc, right: Doc, pairs: dict[Path, Path]
) -> CompareReport:
    """Build the report for ``result``, the diff of ``left`` against ``right``.

    ``pairs`` is the container map from ``diff_with_pairs``. It gives the
    right path of each array compared by position, so Added items join their
    array's runs, and it pairs lines for the anchors.
    """
    changes = result.changes
    return CompareReport(
        rows=_rows(changes, result.unaligned, pairs),
        count=len(changes),
        left=_marks(changes, left_side=True),
        right=_marks(changes, left_side=False),
        anchors=pack_anchors(_anchors(left, right, pairs)),
    )


def pack_anchors(anchors: list[tuple[int, int]]) -> bytes:
    """``anchors`` as compact bytes for the worker's queue."""
    packed = array("I")
    for left_line, right_line in anchors:
        packed.append(left_line)
        packed.append(right_line)
    return packed.tobytes()


def unpack_anchors(data: bytes) -> list[tuple[int, int]]:
    """The pairs ``pack_anchors`` wrote. Empty data is none."""
    if not data:
        return []
    packed = array("I")
    packed.frombytes(data)
    return list(zip(packed[0::2], packed[1::2], strict=True))


# --- Rows ---------------------------------------------------------------


def change_row(change: Change) -> ChangeRow:
    """The row for one change, with Bill's label, glyph and spoken name."""
    kind = change.kind
    label = KIND_LABELS[kind]
    spoken = _spoken_path(change.path)
    if kind == "changed":
        detail = f"{_spoken_value(change.old)} to {_spoken_value(change.new)}"
    elif kind == "added":
        detail = _spoken_value(change.new)
    elif kind == "removed":
        detail = _spoken_value(change.old)
    else:
        detail = f"to {_spoken_path(change.moved_to or ())}"
    return ChangeRow(
        kind=kind,
        glyph=KIND_GLYPHS[kind],
        label=label,
        path_text=change.path_text or ROOT_LABEL,
        old=change.old,
        new=change.new,
        moved_to_text=change.moved_to_text,
        left_span=change.left_span,
        right_span=change.right_span,
        accessible_name=f"{label}, {spoken}, {detail}",
    )


def _spoken_path(path: Path) -> str:
    """``orders[2].total`` read aloud: ``orders 2 total``."""
    if not path:
        return ROOT_LABEL
    return " ".join(str(segment) for segment in path)


def _spoken_value(summary: Summary | None) -> str:
    """A scalar as written; a non-empty object or array as ``{…}`` or ``[…]``."""
    if summary is None:
        return ""
    if summary.type == "object" and summary.text != "{}":
        return "{…}"
    if summary.type == "array" and summary.text != "[]":
        return "[…]"
    return summary.text


class _Run:
    """Consecutive differing indexes of one array compared by position."""

    __slots__ = ("first", "items", "last", "path")

    def __init__(self, path: Path, index: int) -> None:
        self.path = path
        self.first = index
        self.last = index
        self.items: list[ChangeRow | InfoRow] = []


def _rows(
    changes: list[Change], unaligned: tuple[Path, ...], pairs: dict[Path, Path]
) -> tuple[ReportRow, ...]:
    """Rows in engine order, with runs in unaligned arrays folded into groups.

    The engine visits each array's items in order, so an array's changes
    are contiguous and their indexes never go back. A run that covers one
    index stays as plain rows. An unaligned array whose changes all moved
    elsewhere has nothing to explain, so it gets no info row.
    """
    if not unaligned:
        return tuple(change_row(change) for change in changes)
    left_arrays = frozenset(unaligned)
    right_arrays = {pairs[path]: path for path in unaligned if path in pairs}
    rows: list[ReportRow] = []
    announced: set[Path] = set()
    run: _Run | None = None
    for change in changes:
        owners = _owners(change, left_arrays, right_arrays)
        if not owners:
            _close(run, rows)
            run = None
            rows.append(change_row(change))
            continue
        outer, index = owners[0]
        if run is None or run.path != outer or not run.last <= index <= run.last + 1:
            _close(run, rows)
            if outer not in announced:
                announced.add(outer)
                rows.append(_info_row(outer))
            run = _Run(outer, index)
        run.last = index
        for inner, _index in owners[1:]:
            if inner not in announced:
                announced.add(inner)
                run.items.append(_info_row(inner))
        run.items.append(change_row(change))
    _close(run, rows)
    return tuple(rows)


def _owners(
    change: Change, left_arrays: frozenset[Path], right_arrays: dict[Path, Path]
) -> list[tuple[Path, int]]:
    """The unaligned arrays that hold ``change``, outermost first, with the
    item index in each. An Added change is found by its right path."""
    path = change.path
    owners: list[tuple[Path, int]] = []
    for depth in range(len(path)):
        prefix = path[:depth]
        if change.kind == "added":
            array_path = right_arrays.get(prefix)
        else:
            array_path = prefix if prefix in left_arrays else None
        index = path[depth]
        if array_path is not None and isinstance(index, int):
            owners.append((array_path, index))
    return owners


def _close(run: _Run | None, rows: list[ReportRow]) -> None:
    if run is None:
        return
    if run.first == run.last:
        rows.extend(run.items)
        return
    path_text = f"{render_path(list(run.path))}[{run.first}–{run.last}]"
    count = run.last - run.first + 1
    rows.append(
        GroupRow(
            path_text=path_text,
            first=run.first,
            last=run.last,
            text=GROUP_TEXT.format(path=path_text, count=count),
            children=tuple(run.items),
        )
    )


def _info_row(path: Path) -> InfoRow:
    path_text = render_path(list(path)) or ROOT_LABEL
    return InfoRow(path_text, UNALIGNED_TEXT.format(path=path_text))


# --- Marks --------------------------------------------------------------


def _marks(changes: list[Change], *, left_side: bool) -> SideMarks:
    markers: dict[int, ChangeKind] = {}
    tints: list[tuple[int, int, ChangeKind]] = []
    for change in changes:
        span = change.left_span if left_side else change.right_span
        if span is None:
            continue
        markers.setdefault(span.line, change.kind)
        tints.append((span.line, span.end_line, change.kind))
    return SideMarks(markers, tuple(tints))


# --- Anchors ------------------------------------------------------------


def _anchors(left: Doc, right: Doc, pairs: dict[Path, Path]) -> list[tuple[int, int]]:
    """Line pairs for Link Scrolling, strictly increasing on both sides.

    A pair joins the same value on each side: every compared container
    (its first and last lines), and every key the two sides of a compared
    object share. Array items that are not containers are left out, since
    their index can differ between the sides. Each left line keeps its
    lowest right line; the longest run that increases on both sides is
    kept, then thinned evenly to ``ANCHOR_LIMIT``.
    """
    right_spans = right.spans
    lowest: dict[int, int] = {}
    for path, span in left.spans.items():
        target = pairs.get(path)
        if target is not None:
            other = right_spans.get(target)
            if other is not None:
                _keep_lowest(lowest, span.end_line, other.end_line)
        elif path and isinstance(path[-1], str):
            parent = pairs.get(path[:-1])
            if parent is None:
                continue
            other = right_spans.get((*parent, path[-1]))
        else:
            continue
        if other is not None:
            _keep_lowest(lowest, span.line, other.line)
    ordered = sorted(lowest.items())
    return _thin(_increasing(ordered), ANCHOR_LIMIT)


def _keep_lowest(lowest: dict[int, int], left_line: int, right_line: int) -> None:
    current = lowest.get(left_line)
    if current is None or right_line < current:
        lowest[left_line] = right_line


def _increasing(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest subsequence whose right lines strictly increase.

    ``pairs`` is sorted by left line with one pair per left line, so the
    result increases on both sides. Patience sorting, O(n log n).
    """
    tails: list[int] = []
    tail_index: list[int] = []
    previous = [-1] * len(pairs)
    for index, (_left_line, right_line) in enumerate(pairs):
        slot = bisect_left(tails, right_line)
        if slot == len(tails):
            tails.append(right_line)
            tail_index.append(index)
        else:
            tails[slot] = right_line
            tail_index[slot] = index
        previous[index] = tail_index[slot - 1] if slot else -1
    kept: list[tuple[int, int]] = []
    index = tail_index[-1] if tail_index else -1
    while index >= 0:
        kept.append(pairs[index])
        index = previous[index]
    kept.reverse()
    return kept


def _thin(pairs: list[tuple[int, int]], limit: int) -> list[tuple[int, int]]:
    """Keep ``limit`` evenly spaced pairs, the first and last included."""
    if len(pairs) <= limit:
        return pairs
    if limit < 2:
        return pairs[:limit]
    step = (len(pairs) - 1) / (limit - 1)
    return [pairs[round(index * step)] for index in range(limit)]
