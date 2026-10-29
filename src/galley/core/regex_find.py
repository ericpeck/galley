"""Regex and Whole word for the find bars, run in a child process.

``run_find_worker`` is a module-level function so a spawn child can import
it by name. User patterns are compiled only there: the UI process sends the
query and reads back spans. The engine is the standard library's ``re``.

A pattern runs with MULTILINE on and DOTALL off, and IGNORECASE when Match
case is off. Whole word wraps the pattern as ``(?<!\\w)(?:pattern)(?!\\w)``
instead of using ``\\b``, after ``re.escape`` when Regex is off. ``\\w``
does not match combining marks, so a decomposed "é" ends a word at its mark.

The replacement is not ``re``'s template: ``\\n`` and ``\\1`` stay as
typed. ``$0`` to ``$9`` put in a group, ``$$`` puts in one ``$``, and any
other ``$`` is itself. ``$10`` is group 1, then ``0``. A group that did not
take part in the match puts in nothing.

Offsets here count code points. :func:`galley.core.find.utf16_offsets`
turns them into the UTF-16 positions a Qt document uses.

This module does not import Qt.
"""

import re
from array import array
from dataclasses import dataclass
from multiprocessing.queues import Queue

_WORD_START = r"(?<!\w)(?:"
_WORD_END = r")(?!\w)"
_DIGITS = "0123456789"

# A replacement: literal text, or the number of a group to put in.
ReplacementPart = str | int


class PatternError(Exception):
    """A pattern ``re`` can't compile.

    ``column`` is 1-based, in code points of the query as typed, or
    ``None`` when the engine gave no place.
    """

    def __init__(self, message: str, column: int | None) -> None:
        super().__init__(message)
        self.message = message
        self.column = column


def pattern_source(query: str, *, regex: bool, whole_word: bool) -> str:
    """What ``re`` compiles for ``query``."""
    source = query if regex else re.escape(query)
    if whole_word:
        return f"{_WORD_START}{source}{_WORD_END}"
    return source


def pattern_flags(*, match_case: bool) -> int:
    flags = re.MULTILINE
    if not match_case:
        flags |= re.IGNORECASE
    return flags


def compile_pattern(
    query: str, *, regex: bool, whole_word: bool, match_case: bool
) -> re.Pattern[str]:
    """Compile ``query``. Only the worker calls this on user input.

    A regex is compiled as typed first, so an error's column is a place in
    the query and never in the Whole word wrapper.
    """
    flags = pattern_flags(match_case=match_case)
    if regex:
        _compile(query, flags, shift=0, length=len(query))
    shift = len(_WORD_START) if whole_word else 0
    source = pattern_source(query, regex=regex, whole_word=whole_word)
    return _compile(source, flags, shift=shift, length=len(query))


def find_spans(text: str, pattern: re.Pattern[str]) -> tuple[array, array]:
    """``(starts, ends)`` of every match, left to right, in code points.

    An empty match is a span with ``start == end``. ``re`` moves on one
    code point past it.
    """
    starts = array("q")
    ends = array("q")
    for match in pattern.finditer(text):
        start, end = match.span()
        starts.append(start)
        ends.append(end)
    return starts, ends


def parse_replacement(template: str) -> tuple[ReplacementPart, ...]:
    """Split ``template`` into literal text and group numbers.

    This reads ``$`` only and never compiles anything, so the UI process
    may call it to check the groups a replacement names.
    """
    parts: list[ReplacementPart] = []
    literal: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        following = template[index + 1 : index + 2]
        if char == "$" and following == "$":
            literal.append("$")
            index += 2
            continue
        if char == "$" and following and following in _DIGITS:
            if literal:
                parts.append("".join(literal))
                literal = []
            parts.append(int(following))
            index += 2
            continue
        literal.append(char)
        index += 1
    if literal:
        parts.append("".join(literal))
    return tuple(parts)


def missing_group(parts: tuple[ReplacementPart, ...], groups: int) -> int | None:
    """The first group ``parts`` names that a pattern with ``groups`` lacks."""
    for part in parts:
        if isinstance(part, int) and part > groups:
            return part
    return None


def expand(parts: tuple[ReplacementPart, ...], match: re.Match[str]) -> str:
    """The replacement for ``match``. A group that did not match is empty."""
    pieces: list[str] = []
    for part in parts:
        if isinstance(part, str):
            pieces.append(part)
        else:
            pieces.append(match.group(part) or "")
    return "".join(pieces)


def replace_spans(
    text: str, pattern: re.Pattern[str], parts: tuple[ReplacementPart, ...]
) -> tuple[str, array, array]:
    """Replace every match. Returns the new text and each replacement's span.

    The spans are code points in the new text. The text is built in one
    join.
    """
    pieces: list[str] = []
    starts = array("q")
    ends = array("q")
    last = 0
    size = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        before = text[last:start]
        replacement = expand(parts, match)
        pieces.append(before)
        pieces.append(replacement)
        size += len(before)
        starts.append(size)
        size += len(replacement)
        ends.append(size)
        last = end
    pieces.append(text[last:])
    return "".join(pieces), starts, ends


def replacement_at(
    text: str,
    pattern: re.Pattern[str],
    parts: tuple[ReplacementPart, ...],
    start: int,
    end: int,
) -> str | None:
    """The replacement for the match spanning ``start`` to ``end``.

    ``None`` when no match has that span any more.
    """
    match = pattern.search(text, start)
    if match is not None and match.span() == (start, end):
        return expand(parts, match)
    for match in pattern.finditer(text):
        if match.start() > start:
            return None
        if match.span() == (start, end):
            return expand(parts, match)
    return None


# Jobs -------------------------------------------------------------------


@dataclass(frozen=True)
class StoreText:
    """The document to search, kept by the worker until the next one."""

    version: int
    text: str


@dataclass(frozen=True)
class FindJob:
    """Find every match of ``query`` in the stored text."""

    job_id: int
    version: int
    query: str
    regex: bool
    whole_word: bool
    match_case: bool


@dataclass(frozen=True)
class ReplaceAllJob(FindJob):
    """Replace every match with ``replacement``.

    ``expand_groups`` reads ``$`` in the replacement. Off, it goes in as
    typed.
    """

    replacement: str
    expand_groups: bool


@dataclass(frozen=True)
class ReplaceOneJob(ReplaceAllJob):
    """The replacement for one match, at code points ``start`` to ``end``."""

    start: int
    end: int


# Events -----------------------------------------------------------------


@dataclass(frozen=True)
class FindReady:
    """The worker is up and reading jobs."""


@dataclass(frozen=True)
class TextStored:
    version: int


@dataclass(frozen=True)
class Found:
    """Every match, in code points. ``groups`` is the pattern's group count."""

    job_id: int
    starts: array
    ends: array
    groups: int


@dataclass(frozen=True)
class ReplacedAll:
    """The new text, and each replacement's code point span in it."""

    job_id: int
    text: str
    starts: array
    ends: array


@dataclass(frozen=True)
class ReplacedOne:
    """The replacement for one match, or ``None`` when it is gone."""

    job_id: int
    replacement: str | None


@dataclass(frozen=True)
class PatternFailed:
    job_id: int
    message: str
    column: int | None


@dataclass(frozen=True)
class ReplacementFailed:
    """The replacement names group ``group``, which the pattern lacks."""

    job_id: int
    group: int


FindEvent = Found | ReplacedAll | ReplacedOne | PatternFailed | ReplacementFailed


def run_job(job: FindJob, text: str) -> FindEvent:
    """Run one job on ``text``. The worker's whole body, for tests too."""
    try:
        pattern = compile_pattern(
            job.query,
            regex=job.regex,
            whole_word=job.whole_word,
            match_case=job.match_case,
        )
    except PatternError as exc:
        return PatternFailed(job.job_id, exc.message, exc.column)
    if not isinstance(job, ReplaceAllJob):
        starts, ends = find_spans(text, pattern)
        return Found(job.job_id, starts, ends, pattern.groups)
    if job.expand_groups:
        parts = parse_replacement(job.replacement)
        group = missing_group(parts, pattern.groups)
        if group is not None:
            return ReplacementFailed(job.job_id, group)
    else:
        parts = (job.replacement,) if job.replacement else ()
    if isinstance(job, ReplaceOneJob):
        return ReplacedOne(
            job.job_id, replacement_at(text, pattern, parts, job.start, job.end)
        )
    new_text, starts, ends = replace_spans(text, pattern, parts)
    return ReplacedAll(job.job_id, new_text, starts, ends)


def run_find_worker(job_queue: Queue, event_queue: Queue) -> None:
    """Run find jobs until the queue yields ``None``.

    The UI process kills this process when a job runs past its limit.
    """
    event_queue.put(FindReady())
    text = ""
    while True:
        job = job_queue.get()
        if job is None:
            return
        if isinstance(job, StoreText):
            text = job.text
            event_queue.put(TextStored(job.version))
            continue
        if isinstance(job, FindJob):
            event_queue.put(run_job(job, text))


def _compile(source: str, flags: int, *, shift: int, length: int) -> re.Pattern[str]:
    try:
        return re.compile(source, flags)
    except re.error as exc:
        column = None
        if exc.pos is not None:
            column = min(max(exc.pos - shift, 0), length) + 1
        raise PatternError(_sentence(exc.msg), column) from None
    except (OverflowError, RecursionError) as exc:
        raise PatternError(_sentence(str(exc)), None) from None


def _sentence(message: str) -> str:
    return message[:1].upper() + message[1:]
