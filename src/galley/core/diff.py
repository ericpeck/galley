"""Structural comparison of two JSON or YAML documents.

This module does not import Qt. ``load_document`` parses text and records
where each value sits. ``diff`` compares two loaded documents and returns
``Change`` records in left-document order. Both are plain functions over
picklable values, so a spawn worker can call them by import path.

Value rules:

- Objects match by key, and key order is ignored. Keys compare as text, the
  way JSON sees them, so YAML ``1:`` and ``"1":`` are the same key.
- Arrays match by index. Extra items at the end are Added or Removed.
- Scalars compare by type and value. ``1`` equals ``1.0``. ``"1"`` and ``1``
  are Changed. ``true`` and ``1`` are Changed: a boolean is not a number.
- ``null`` is a value, so a key holding ``null`` is not a missing key.
- NaN equals NaN. A document that contains ``.nan`` (YAML) or ``NaN`` (JSON)
  compares equal to itself.
- YAML anchors, aliases, merge keys, and tags compare by their resolved
  values. ``!!set`` compares as a mapping whose values are ``null``, and
  ``!!omap`` and ``!!pairs`` compare as the list of one-key mappings they are
  written as.

Moved: after the walk, one Removed and one Added subtree become one Moved
change when the two values are equal, no other Removed or Added value is
equal to them, and their parents differ.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.constructor import ConstructorError, DuplicateKeyError
from ruamel.yaml.error import StreamMark
from ruamel.yaml.events import (
    AliasEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from ruamel.yaml.nodes import ScalarNode

from galley.core.errors import MAX_DEPTH, InputError, nesting_error, utf8_input_error
from galley.core.format import (
    _CHARACTER_WALK,
    _clib_available,
    _decode_json,
    _json_fast_value,
    _json_limits,
    _needs_pure_reparse,
    _normalize_format,
    _safe_yaml,
    _scalar_value,
    _scan_yaml,
)
from galley.core.keypath import render_path

ChangeKind = Literal["added", "removed", "changed", "moved"]
DocumentFormat = Literal["json", "yaml"]
Path = tuple[str | int, ...]

# Longest summary text, in characters, including the closing "…".
SUMMARY_LIMIT = 80

_MERGE_TAG = "tag:yaml.org,2002:merge"
_STR_TAG = "tag:yaml.org,2002:str"
_COLLECTION_TAGS = frozenset(
    {
        None,
        "!",
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:set",
        "tag:yaml.org,2002:omap",
        "tag:yaml.org,2002:pairs",
    }
)

# One JSON token: a whole string, one structural character, or a bare
# number or literal. The unrolled string body stays linear.
_JSON_TOKEN = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[][{},:]|[^\s\][{},:"]+')

_MISSING = object()
_EXPECT_KEY = object()
_FIRST = object()
_MERGE = object()
_END = object()


@dataclass(frozen=True)
class Span:
    """Where a value sits in its source text.

    Lines and columns are 1-based and count characters, like ``InputError``.
    The end column is exclusive. An object member starts at its key, so a
    jump lands on the key line. Other values start at their first character.
    """

    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class Summary:
    """A short view of one value.

    ``type`` is ``object``, ``array``, ``string``, ``number``, ``boolean``,
    ``null``, ``date``, ``timestamp``, or ``binary``. ``text`` is compact
    JSON-style text, at most ``SUMMARY_LIMIT`` characters, ending in "…"
    when it was cut.
    """

    type: str
    text: str


@dataclass(frozen=True)
class Change:
    """One difference between the left and right documents.

    ``path`` is the left path, or the right path for Added. A Moved change
    also sets ``moved_to``, the right path. ``path_text`` is ``path`` in
    ``render_path`` form; the root is ``""``. ``old`` is the left value and
    ``new`` the right value; each is ``None`` on the side that has no value.
    The spans follow the same rule.
    """

    kind: ChangeKind
    path: Path
    path_text: str
    old: Summary | None
    new: Summary | None
    left_span: Span | None
    right_span: Span | None
    moved_to: Path | None = None
    moved_to_text: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """A parsed document plus the span of every value that has one.

    ``value`` uses dict, list, str, int, float, bool, and None, plus date,
    datetime, and bytes from YAML. Object keys are str. ``spans`` maps a
    path to its span. A path inside an alias or a merged mapping has no
    entry; ``diff`` then uses the nearest enclosing span.
    """

    value: object
    format: DocumentFormat
    spans: dict[Path, Span] = field(default_factory=dict)


def load_document(text: str | bytes, source: str) -> ParsedDocument:
    """Parse ``text`` as ``source`` (``json`` or ``yaml``) and locate its values.

    Errors match ``format_document`` for the same text: ``InputError`` for
    invalid UTF-8, nesting past ``MAX_DEPTH``, and JSON lone surrogates;
    ``json.JSONDecodeError`` or ruamel's ``YAMLError`` for other syntax.
    """
    if _normalize_format(source) == "json":
        return _load_json(text)
    return _load_yaml(text)


def diff(left: ParsedDocument, right: ParsedDocument) -> list[Change]:
    """Return the differences from ``left`` to ``right``.

    Changes come in left-document order. An Added key sits after its
    nearest left neighbour: the closest key before it in the right object
    that the left object also has, after any changes inside that key. With
    no such key it comes first in its object. Extra array items sit at the
    end of their array. A Moved change sits where its left value was.

    Raises ``InputError`` (``too_deep``) for a value nested past
    ``MAX_DEPTH``, which ``load_document`` already rejects.
    """
    found = _walk(left, right)
    found = _pair_moves(found, left, right)
    return [_change(entry, left.spans, right.spans) for entry in found]


# --- JSON ---------------------------------------------------------------


def _load_json(text: str | bytes) -> ParsedDocument:
    source = _decode_json(text)
    value = _json_fast_value(source)
    if value is _CHARACTER_WALK:
        opened = _json_limits(source)
        try:
            value = json.loads(source)
        except RecursionError:
            raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    return ParsedDocument(value, "json", _json_spans(source))


def _json_spans(text: str) -> dict[Path, Span]:
    """Map each path in valid JSON ``text`` to its span, in one token pass.

    Each frame is ``[path, is_object, key_or_index, line, column, key_line,
    key_column]``. Valid JSON strings hold no raw newline, so a token ends
    on the line it starts.
    """
    spans: dict[Path, Span] = {}
    frames: list[list[object]] = []
    line = 1
    line_start = 0
    cursor = 0
    for match in _JSON_TOKEN.finditer(text):
        position = match.start()
        breaks = text.count("\n", cursor, position)
        if breaks:
            line += breaks
            line_start = text.rfind("\n", cursor, position) + 1
        cursor = position
        column = position - line_start + 1
        token = match.group()
        first = token[0]
        if first == ":":
            continue
        if first == ",":
            frame = frames[-1]
            if frame[1]:
                frame[2] = _EXPECT_KEY
            else:
                frame[2] += 1
            continue
        if first in "}]":
            frame = frames.pop()
            spans[frame[0]] = Span(frame[3], frame[4], line, column + 1)
            continue
        if frames:
            frame = frames[-1]
            if frame[1]:
                if frame[2] is _EXPECT_KEY:
                    frame[2] = _json_key(token)
                    frame[5] = line
                    frame[6] = column
                    continue
                start_line, start_column = frame[5], frame[6]
            else:
                start_line, start_column = line, column
            path = (*frame[0], frame[2])
        else:
            path = ()
            start_line, start_column = line, column
        if first == "{":
            frames.append([path, True, _EXPECT_KEY, start_line, start_column, 0, 0])
        elif first == "[":
            frames.append([path, False, 0, start_line, start_column, 0, 0])
        else:
            spans[path] = Span(start_line, start_column, line, column + len(token))
    return spans


def _json_key(token: str) -> str:
    if "\\" not in token:
        return token[1:-1]
    return json.loads(token)


# --- YAML ---------------------------------------------------------------


def _load_yaml(text: str | bytes) -> ParsedDocument:
    """Load YAML with the same C-or-pure choice ``format_yaml`` makes."""
    source = _decode_yaml(text)
    if _clib_available():
        try:
            _opened, tokens, failed = _scan_yaml(source, pure=False, keep_tokens=True)
            if not failed and not _needs_pure_reparse(source, tokens):
                return _yaml_document(source, pure=False)
        except Exception:
            pass
        return _yaml_document(source, pure=True)
    return _yaml_document(source, pure=False)


def _decode_yaml(text: str | bytes) -> str:
    """Return YAML text with a leading BOM removed, so marks match the text."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise utf8_input_error(text, exc.start) from None
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _yaml_document(text: str, *, pure: bool) -> ParsedDocument:
    yaml = _safe_yaml(pure=pure)
    builder = _YamlBuilder(yaml, text)
    for event in yaml.parse(text):
        builder.feed(event)
    return ParsedDocument(builder.root, "yaml", builder.spans)


class _YamlFrame:
    """One open YAML collection. ``path`` is None where spans are not kept."""

    __slots__ = (
        "anchor",
        "awaiting_key",
        "container",
        "flow",
        "key",
        "key_start",
        "last_end",
        "merges",
        "path",
        "start",
        "start_mark",
    )

    def __init__(
        self,
        container: dict[object, object] | list[object],
        path: Path | None,
        start: tuple[int, int],
        start_mark: StreamMark,
        *,
        flow: bool,
        anchor: str | None,
    ) -> None:
        self.container = container
        self.path = path
        self.start = start
        self.start_mark = start_mark
        self.flow = flow
        self.anchor = anchor
        self.awaiting_key = True
        self.key: object = None
        self.key_start = start
        self.last_end: tuple[int, int] | None = None
        self.merges: list[tuple[list[dict[object, object]], Span]] = []


class _YamlBuilder:
    """Build one document from parser events with an explicit stack.

    This follows ``format._yaml_from_events`` and also records spans,
    applies merge keys, and rejects duplicate keys the way the safe
    constructor does.
    """

    def __init__(self, yaml: YAML, text: str) -> None:
        self.yaml = yaml
        self.text = text
        self.root: object = None
        self.spans: dict[Path, Span] = {}
        self.frames: list[_YamlFrame] = []
        self.anchors: dict[str, object] = {}
        self.open_anchors: set[str] = set()
        self.first_document: StreamMark | None = None
        self.line_starts: list[int] | None = None

    def feed(self, event: object) -> None:
        if isinstance(event, ScalarEvent):
            self.scalar(event)
        elif isinstance(event, MappingStartEvent):
            self.open(event, {})
        elif isinstance(event, SequenceStartEvent):
            self.open(event, [])
        elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
            self.close(event)
        elif isinstance(event, AliasEvent):
            self.alias(event)
        elif isinstance(event, DocumentStartEvent):
            if self.first_document is not None:
                raise ComposerError(
                    "expected a single document in the stream",
                    self.first_document,
                    "but found another document",
                    event.start_mark,
                )
            self.first_document = event.start_mark

    def scalar(self, event: ScalarEvent) -> None:
        tag = event.tag
        if tag is None:
            tag = self.yaml.resolver.resolve(ScalarNode, event.value, event.implicit)
        start = _mark_start(event.start_mark)
        end = self.mark_end(event.start_mark, event.end_mark)
        frame = self.frames[-1] if self.frames else None
        if (
            frame is not None
            and isinstance(frame.container, dict)
            and frame.awaiting_key
            and tag == _MERGE_TAG
        ):
            self.set_key(frame, _MERGE, start)
            return
        if tag == _STR_TAG:
            value: object = event.value
        else:
            value = _scalar_value(self.yaml, event)
        if event.anchor:
            self.anchors[event.anchor] = value
        self.attach(value, start, end, event.start_mark)

    def alias(self, event: AliasEvent) -> None:
        name = event.anchor
        if name in self.open_anchors:
            raise ComposerError(
                None, None, f"found recursive alias {name!r}", event.start_mark
            )
        if name not in self.anchors:
            raise ComposerError(
                None, None, f"found undefined alias {name!r}", event.start_mark
            )
        start = _mark_start(event.start_mark)
        end = self.mark_end(event.start_mark, event.end_mark)
        self.attach(self.anchors[name], start, end, event.start_mark)

    def open(
        self,
        event: MappingStartEvent | SequenceStartEvent,
        container: dict[object, object] | list[object],
    ) -> None:
        if event.tag not in _COLLECTION_TAGS:
            raise ConstructorError(
                None,
                None,
                f"could not determine a constructor for the tag {event.tag!r}",
                event.start_mark,
            )
        start = _mark_start(event.start_mark)
        depth = len(self.frames) + 1
        if depth > MAX_DEPTH:
            raise nesting_error(start[0], start[1], depth=depth)
        path: Path | None = ()
        if self.frames:
            parent = self.frames[-1]
            if parent.path is None:
                path = None
            elif isinstance(parent.container, list):
                path = (*parent.path, len(parent.container))
            elif parent.awaiting_key or parent.key is _MERGE:
                path = None
            else:
                path = (*parent.path, parent.key)
        frame = _YamlFrame(
            container,
            path,
            start,
            event.start_mark,
            flow=bool(event.flow_style),
            anchor=event.anchor,
        )
        if event.anchor:
            self.anchors[event.anchor] = container
            self.open_anchors.add(event.anchor)
        self.frames.append(frame)

    def close(self, event: MappingEndEvent | SequenceEndEvent) -> None:
        frame = self.frames.pop()
        if frame.anchor:
            self.open_anchors.discard(frame.anchor)
        if frame.flow:
            end = self.mark_end(frame.start_mark, event.end_mark)
        else:
            end = frame.last_end or frame.start
        if frame.merges:
            self.apply_merges(frame)
        self.attach(frame.container, frame.start, end, frame.start_mark)

    def attach(
        self,
        value: object,
        start: tuple[int, int],
        end: tuple[int, int],
        mark: StreamMark,
    ) -> None:
        if not self.frames:
            self.root = value
            self.spans[()] = Span(*start, *end)
            return
        frame = self.frames[-1]
        container = frame.container
        if isinstance(container, list):
            if frame.path is not None:
                self.spans[(*frame.path, len(container))] = Span(*start, *end)
            container.append(value)
            frame.last_end = end
            return
        if frame.awaiting_key:
            self.set_key(frame, value, start)
            return
        span = Span(*frame.key_start, *end)
        frame.last_end = end
        frame.awaiting_key = True
        key = frame.key
        if key is _MERGE:
            frame.merges.append((_merge_sources(value, frame, mark), span))
            return
        if key in container:
            raise DuplicateKeyError(
                "while constructing a mapping",
                frame.start_mark,
                f'found duplicate key "{key}" with value '
                f'"{_summary(value).text}" '
                f'(original value: "{_summary(container[key]).text}")',
                mark,
            )
        container[key] = value
        if frame.path is not None:
            self.spans[(*frame.path, key)] = span

    def set_key(self, frame: _YamlFrame, key: object, start: tuple[int, int]) -> None:
        frame.key = key if key is _MERGE else _key_text(key)
        frame.key_start = start
        frame.awaiting_key = False

    def apply_merges(self, frame: _YamlFrame) -> None:
        """Apply ``<<`` keys: explicit keys win, then earlier merge sources."""
        merged: dict[object, object] = {}
        located: dict[object, Span] = {}
        for sources, span in reversed(frame.merges):
            for source in reversed(sources):
                for key, value in source.items():
                    merged[key] = value
                    located[key] = span
        explicit = frame.container
        if not isinstance(explicit, dict):
            return
        if frame.path is not None:
            for key, span in located.items():
                if key not in explicit:
                    self.spans[(*frame.path, key)] = span
        merged.update(explicit)
        explicit.clear()
        explicit.update(merged)

    def mark_end(self, start: StreamMark, end: StreamMark) -> tuple[int, int]:
        """Return a 1-based exclusive end, kept on the value's last line.

        A block scalar's mark ends at column 0 of the next line. That end
        moves back to the end of the previous line.
        """
        if end.column == 0 and end.line > start.line:
            return end.line, self.line_length(end.line - 1) + 1
        return end.line + 1, end.column + 1

    def line_length(self, index: int) -> int:
        """Return the length of 0-based line ``index``, without its line break."""
        starts = self.line_starts
        if starts is None:
            starts = [0]
            starts.extend(match.end() for match in re.finditer("\n", self.text))
            self.line_starts = starts
        if index >= len(starts):
            return 0
        begin = starts[index]
        stop = starts[index + 1] - 1 if index + 1 < len(starts) else len(self.text)
        if stop > begin and self.text[stop - 1] == "\r":
            stop -= 1
        return stop - begin


def _mark_start(mark: StreamMark) -> tuple[int, int]:
    return mark.line + 1, mark.column + 1


def _merge_sources(
    value: object, frame: _YamlFrame, mark: StreamMark
) -> list[dict[object, object]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return list(value)
    raise ConstructorError(
        "while constructing a mapping",
        frame.start_mark,
        "expected a mapping or list of mappings for merging",
        mark,
    )


def _key_text(key: object) -> str:
    """Return a mapping key as text, the way ``json.dumps`` writes keys."""
    if isinstance(key, str):
        return key
    if key is None or isinstance(key, (bool, int, float)):
        return json.dumps(key)
    if isinstance(key, (date, datetime)):
        return key.isoformat()
    return _preview(key, None)


# --- Walk ---------------------------------------------------------------


def _walk(left: ParsedDocument, right: ParsedDocument) -> list[list[object]]:
    """Compare two trees on an explicit stack, in left-document order.

    Each entry is ``[kind, path, old, new, moved_to]``. A job's path length
    is the number of containers above it, so a container at
    ``len(path) == MAX_DEPTH`` is one level too deep.
    """
    found: list[list[object]] = []
    stack: list[tuple[object, object, Path]] = [(left.value, right.value, ())]
    while stack:
        old, new, path = stack.pop()
        if old is _MISSING:
            found.append(["added", path, None, new, None])
            continue
        if new is _MISSING:
            found.append(["removed", path, old, None, None])
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            _check_depth(path, left)
            stack.extend(reversed(_object_jobs(old, new, path)))
        elif isinstance(old, list) and isinstance(new, list):
            _check_depth(path, left)
            stack.extend(reversed(_array_jobs(old, new, path)))
        elif not _same_scalar(old, new):
            found.append(["changed", path, old, new, None])
    return found


def _check_depth(path: Path, document: ParsedDocument) -> None:
    if len(path) >= MAX_DEPTH:
        raise _too_deep(len(path) + 1, path, document.spans)


def _object_jobs(
    old: dict[object, object], new: dict[object, object], path: Path
) -> list[tuple[object, object, Path]]:
    """Order members: left keys in left order, right-only keys after the
    left key that precedes them on the right."""
    added_after: dict[object, list[object]] = {}
    anchor: object = _FIRST
    for key in new:
        if key in old:
            anchor = key
        else:
            added_after.setdefault(anchor, []).append(key)
    jobs: list[tuple[object, object, Path]] = []
    for key in added_after.get(_FIRST, ()):
        jobs.append((_MISSING, new[key], (*path, _segment(key))))
    for key, value in old.items():
        jobs.append((value, new.get(key, _MISSING), (*path, _segment(key))))
        if added_after:
            for extra in added_after.get(key, ()):
                jobs.append((_MISSING, new[extra], (*path, _segment(extra))))
    return jobs


def _array_jobs(
    old: list[object], new: list[object], path: Path
) -> list[tuple[object, object, Path]]:
    shared = min(len(old), len(new))
    jobs = [(old[index], new[index], (*path, index)) for index in range(shared)]
    jobs.extend(
        (old[index], _MISSING, (*path, index)) for index in range(shared, len(old))
    )
    jobs.extend(
        (_MISSING, new[index], (*path, index)) for index in range(shared, len(new))
    )
    return jobs


def _segment(key: object) -> str:
    return key if isinstance(key, str) else _key_text(key)


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "binary"
    return type(value).__name__


def _same_scalar(old: object, new: object) -> bool:
    """Compare by type and value. A container never equals a scalar here."""
    kind = type(old)
    if kind is type(new):
        if kind is float:
            return old == new or (old != old and new != new)
        if kind is dict or kind is list:
            return False
        return old == new
    old_kind = _kind(old)
    if old_kind != _kind(new) or old_kind in ("object", "array"):
        return False
    if old_kind == "number" and (old != old or new != new):
        return old != old and new != new
    return old == new


def _equal(old: object, new: object) -> bool:
    """Deep equality under the value rules, on an explicit stack."""
    stack: list[tuple[object, object]] = [(old, new)]
    while stack:
        left, right = stack.pop()
        if isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                return False
            stack.extend((left[key], right[key]) for key in left)
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                return False
            stack.extend(zip(left, right, strict=True))
        elif not _same_scalar(left, right):
            return False
    return True


# --- Moved --------------------------------------------------------------


def _pair_moves(
    found: list[list[object]], left: ParsedDocument, right: ParsedDocument
) -> list[list[object]]:
    """Turn a unique Removed and Added pair with different parents into Moved."""
    removed = [index for index, entry in enumerate(found) if entry[0] == "removed"]
    added = [index for index, entry in enumerate(found) if entry[0] == "added"]
    if not removed or not added:
        return found
    removed_groups = _value_groups(found, removed, 2, left.spans)
    added_groups = _value_groups(found, added, 3, right.spans)
    dropped: set[int] = set()
    for digest, removed_classes in removed_groups.items():
        added_classes = added_groups.get(digest)
        if added_classes is None:
            continue
        for members in removed_classes:
            if len(members) != 1:
                continue
            source = found[members[0]]
            match = next(
                (
                    other
                    for other in added_classes
                    if _equal(source[2], found[other[0]][3])
                ),
                None,
            )
            if match is None or len(match) != 1:
                continue
            target = found[match[0]]
            source_path = source[1]
            target_path = target[1]
            if not isinstance(source_path, tuple) or not isinstance(target_path, tuple):
                continue
            if source_path[:-1] == target_path[:-1]:
                continue
            found[members[0]] = [
                "moved",
                source_path,
                source[2],
                target[3],
                target_path,
            ]
            dropped.add(match[0])
    if not dropped:
        return found
    return [entry for index, entry in enumerate(found) if index not in dropped]


def _value_groups(
    found: list[list[object]], indexes: list[int], slot: int, spans: dict[Path, Span]
) -> dict[int, list[list[int]]]:
    """Group entries by hash, then into classes of equal values."""
    groups: dict[int, list[list[int]]] = {}
    for index in indexes:
        entry = found[index]
        value = entry[slot]
        path = entry[1]
        digest = _value_hash(value, path if isinstance(path, tuple) else (), spans)
        classes = groups.setdefault(digest, [])
        for members in classes:
            if _equal(found[members[0]][slot], value):
                members.append(index)
                break
        else:
            classes.append([index])
    return groups


def _value_hash(value: object, path: Path, spans: dict[Path, Span]) -> int:
    """Hash ``value`` so equal values hash alike, whatever their key order.

    Post-order on an explicit stack. ``1`` and ``1.0`` hash alike, and
    ``True`` does not hash like ``1``.
    """
    if not isinstance(value, (dict, list)):
        return hash(_scalar_token(value))
    base = len(path)
    hashes: list[int] = []
    stack: list[tuple[object, bool, int]] = [(value, False, base + 1)]
    while stack:
        node, done, depth = stack.pop()
        if isinstance(node, dict):
            if done:
                count = len(node)
                children = hashes[len(hashes) - count :]
                del hashes[len(hashes) - count :]
                hashes.append(hash(("object", frozenset(zip(node, children)))))
                continue
            if depth > MAX_DEPTH:
                raise _too_deep(depth, path, spans)
            stack.append((node, True, depth))
            stack.extend((child, False, depth + 1) for child in reversed(node.values()))
        elif isinstance(node, list):
            if done:
                count = len(node)
                children = hashes[len(hashes) - count :]
                del hashes[len(hashes) - count :]
                hashes.append(hash(("array", tuple(children))))
                continue
            if depth > MAX_DEPTH:
                raise _too_deep(depth, path, spans)
            stack.append((node, True, depth))
            stack.extend((child, False, depth + 1) for child in reversed(node))
        else:
            hashes.append(hash(_scalar_token(node)))
    return hashes[0]


def _scalar_token(value: object) -> tuple[str, object]:
    kind = _kind(value)
    if kind == "number" and value != value:
        return kind, "nan"
    try:
        hash(value)
    except TypeError:
        return kind, repr(value)
    return kind, value


# --- Output -------------------------------------------------------------


def _change(
    entry: list[object], left_spans: dict[Path, Span], right_spans: dict[Path, Span]
) -> Change:
    kind, path, old, new, moved_to = entry
    if not isinstance(path, tuple):
        raise TypeError(path)
    target = moved_to if isinstance(moved_to, tuple) else None
    return Change(
        kind=kind,
        path=path,
        path_text=render_path(list(path)),
        old=None if kind == "added" else _summary(old),
        new=None if kind == "removed" else _summary(new),
        left_span=None if kind == "added" else _locate(left_spans, path),
        right_span=None
        if kind == "removed"
        else _locate(right_spans, path if target is None else target),
        moved_to=target,
        moved_to_text=None if target is None else render_path(list(target)),
    )


def _locate(spans: dict[Path, Span], path: Path) -> Span | None:
    """Return the span at ``path``, or at its nearest ancestor that has one."""
    while True:
        span = spans.get(path)
        if span is not None or not path:
            return span
        path = path[:-1]


def _too_deep(depth: int, path: Path, spans: dict[Path, Span]) -> InputError:
    span = _locate(spans, path)
    if span is None:
        return InputError("too_deep", lineno=None, colno=None, details={"depth": depth})
    return nesting_error(span.line, span.column, depth=depth)


def _summary(value: object) -> Summary:
    return Summary(_kind(value), _preview(value, SUMMARY_LIMIT))


def _preview(value: object, limit: int | None) -> str:
    """Compact JSON-style text for ``value``, cut to ``limit`` characters.

    Containers are read through iterators on an explicit stack, and the
    walk stops once the text is past the limit, so a large value costs
    only what is shown.
    """
    pieces: list[str] = []
    size = 0
    frames: list[list[object]] = []
    pending: object = value
    have_value = True
    while limit is None or size <= limit:
        if have_value:
            have_value = False
            if isinstance(pending, dict):
                if pending:
                    frames.append([iter(pending.items()), "}", True, True])
                    piece = "{"
                else:
                    piece = "{}"
            elif isinstance(pending, list):
                if pending:
                    frames.append([iter(pending), "]", False, True])
                    piece = "["
                else:
                    piece = "[]"
            else:
                piece = _scalar_text(pending, limit)
        else:
            if not frames:
                break
            frame = frames[-1]
            item = next(frame[0], _END)
            if item is _END:
                frames.pop()
                piece = str(frame[1])
            else:
                separator = "" if frame[3] else ", "
                frame[3] = False
                if frame[2]:
                    key, pending = item
                    piece = separator + json.dumps(_segment(key), ensure_ascii=False)
                    piece += ": "
                else:
                    pending = item
                    piece = separator
                have_value = True
        pieces.append(piece)
        size += len(piece)
    text = "".join(pieces)
    if limit is not None and size > limit:
        return text[: limit - 1] + "…"
    return text


def _scalar_text(value: object, limit: int | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        try:
            return str(value)
        except ValueError:
            return "a very large number"
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value in (float("inf"), float("-inf")):
            return "Infinity" if value > 0 else "-Infinity"
        return repr(value)
    if isinstance(value, str):
        shown = value if limit is None else value[: limit + 1]
        return json.dumps(shown, ensure_ascii=False)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return f"{len(value)} bytes"
    return repr(value)
