"""Parse and format documents in a child process.

``run_worker`` is a module-level function so a spawn child can import it by
name. This module does not import Qt. The child writes a temp file beside
the destination and never replaces that destination; the UI process commits.
A ``CompareJob`` loads two documents, diffs them, and builds the Compare
report. It has no output file, so it writes no temp. A ``FormatJob`` with
``mask`` set formats through ``mask_document``. If masking fails, the job
fails and sends no text, so unmasked output never reaches the parent while
masking is on. Any other failure of a masked job is rebuilt from its kind,
key name and place, so no value or quoted source line reaches the parent
either.
"""

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from multiprocessing.queues import Queue
from typing import BinaryIO, Literal

from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import MarkedYAMLError

from galley.core.compare_report import CompareReport, build_report
from galley.core.diff import (
    DiffResult,
    Doc,
    _json_spans,
    diff_with_pairs,
    load_document,
)
from galley.core.errors import InputError, JSONDuplicateKeyError, NotJSONValueError
from galley.core.folds import fold_ranges, line_count, pack_ranges
from galley.core.format import DEFAULT_INDENT, format_document, validate_document
from galley.core.formats import format_named
from galley.core.mask import (
    MASK,
    CopyAsError,
    CopyAsMode,
    CopyAsResult,
    MaskError,
    MaskRules,
    copy_as_text,
    looks_personal,
    mask_document,
)
from galley.core.problems import Problem, collect_problems

# The message a failed mask step sends. It is fixed, so it never quotes a
# value from the document. The banner words are chosen in the app.
MASK_FAILURE_MESSAGE = "Masking failed."

# What a masked job's failure may keep: the escape a ``bad_escape`` names,
# brackets, and the key a duplicate repeats unless it looks personal.
_ESCAPE_RE = re.compile(r"\\[uU][0-9A-Fa-f]{4,8}")
_BRACKETS = frozenset("{}[]")
_DUPLICATE_CODES = frozenset({"json_duplicate", "yaml_duplicate"})
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")

TEMP_PREFIX = ".galley-"
TEMP_SUFFIX = ".tmp"
FAULT_AFTER_TEMP = "after-temp"
_KNOWN_FAULTS = frozenset({None, FAULT_AFTER_TEMP})

# Placeholders until Isaac's benchmark (ADR 0001, UI spec §10). Not tuned.
# Chunk size and how often a write-progress event is sent can change separately.
WRITE_CHUNK_SIZE = 65_536
WRITE_PROGRESS_EVERY_CHUNKS = 1


@dataclass(frozen=True)
class FormatJob:
    """One document to format. The body is a copy; the worker does not share it.

    ``sort_keys`` orders the output's keys; the text in ``text`` is unchanged.
    ``mask`` is ``None`` when masking is off, and the job formats exactly as
    before. With rules set, matched values are replaced with ``"•••"``, and
    ``sort_keys`` orders the masked output the same way. ``minify`` writes
    JSON to JSON as compact JSON, with Format's checks; it takes no ``mask``.
    ``validate`` runs Format's checks on ``text`` in ``source`` and writes
    nothing: success sends empty text. ``target`` and ``sort_keys`` are
    ignored, and it takes no ``mask``. ``indent`` is what Format adds per
    level, masked or not; Minify and Validate ignore it.
    """

    job_id: int
    text: str
    source: str
    target: str
    destination: str | None = None
    temp_path: str | None = None
    chunk_size: int = WRITE_CHUNK_SIZE
    chunk_pause_s: float = 0.0
    fault: str | None = None
    sort_keys: bool = False
    mask: MaskRules | None = None
    minify: bool = False
    validate: bool = False
    indent: str = DEFAULT_INDENT


@dataclass(frozen=True)
class ProgressEvent:
    """Read phase has ``written == 0`` and ``total == 0``. Write carries counts."""

    job_id: int
    phase: str
    written: int
    total: int
    temp_path: str | None = None


@dataclass(frozen=True)
class SuccessEvent:
    """Formatting finished. ``temp_path`` is closed and not yet committed.

    ``folds`` is the text's fold ranges from :func:`pack_ranges`.
    ``masked_count`` is the number of values masked, or ``None`` when the
    job had no ``mask``, so "masking on, 0 values masked" stays distinct.
    """

    job_id: int
    text: str
    temp_path: str | None = None
    folds: bytes = b""
    masked_count: int | None = None


@dataclass(frozen=True)
class FailureEvent:
    """Formatting failed. ``temp_path`` is set when a temp file was created.

    ``error`` is set when the failure is an ``InputError``. Its ``kind``,
    ``lineno``, ``colno``, and ``details`` are the structured fields.
    ``not_json_value`` is True for a ``NotJSONValueError``. The banner
    sentence is built in the app, not here.
    """

    job_id: int
    message: str
    temp_path: str | None = None
    error: InputError | None = None
    problems: tuple[Problem, ...] = ()
    not_json_value: bool = False


@dataclass(frozen=True)
class CompareJob:
    """Two documents to compare. The bodies are copies; the worker does not share them.

    ``reformat`` formats each side in its own format first and compares the
    formatted text, in this one job, so one Cancel stops both and the spans
    match what each pane then shows. It never sorts keys: Compare ignores
    the Sort keys setting (#59).

    ``hold_s`` pauses after both sides load and before ``diff`` runs, so a
    test can cancel mid-compare. The app leaves it at 0.
    """

    job_id: int
    left_text: str
    left_format: str
    right_text: str
    right_format: str
    hold_s: float = 0.0
    reformat: bool = False


CompareSide = Literal["left", "right"]


@dataclass(frozen=True)
class CompareEvent:
    """Comparing finished.

    ``result`` is the engine's ``DiffResult`` and ``report`` its rows, marks
    and anchors. ``left_text`` and ``right_text`` are the formatted texts
    when the job had ``reformat``, and ``None`` otherwise.
    """

    job_id: int
    result: DiffResult
    report: CompareReport
    left_text: str | None = None
    right_text: str | None = None


@dataclass(frozen=True)
class CompareFailureEvent:
    """Comparing failed.

    ``side`` names the document that did not load. It is ``None`` when both
    loaded and ``diff`` itself failed. ``error`` and ``problems`` are filled
    the way :class:`FailureEvent` fills them, with lines in that side's
    document. The words are chosen in the app.
    """

    job_id: int
    side: CompareSide | None
    message: str
    error: InputError | None = None
    problems: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class CopyAsJob:
    """One Copy As text to build. The body is a copy; the worker does not share it.

    The app sends one for a large Output. Like a compare, it has no output
    file and writes no temp.

    ``fmt`` is the Output format, for reading and writing. ``hold_s``
    pauses before the copy is built, so a test can cancel mid-copy. The app
    leaves it at 0.
    """

    job_id: int
    text: str
    fmt: str
    mode: CopyAsMode
    rules: MaskRules | None = None
    hold_s: float = 0.0


@dataclass(frozen=True)
class CopyAsEvent:
    """Copy As finished. ``result`` holds the text for the clipboard."""

    job_id: int
    result: CopyAsResult


@dataclass(frozen=True)
class CopyAsFailureEvent:
    """Copy As built no text. ``mode`` and ``invalid`` are from :class:`CopyAsError`.

    It carries no error text: a parse error can quote the Output, which may
    hold the values Masked and Random fake are meant to hide.
    """

    job_id: int
    mode: CopyAsMode
    invalid: bool


def run_worker(job_queue: Queue, event_queue: Queue) -> None:
    """Format jobs until the queue yields ``None``.

    The spawn parent imports this function by module path. A ``None`` job is
    a clean shutdown. Cancel kills the process instead of using this sentinel.
    """
    while True:
        job = job_queue.get()
        if job is None:
            return
        if isinstance(job, CompareJob):
            _execute_compare(job, event_queue)
            continue
        if isinstance(job, CopyAsJob):
            event_queue.put(run_copy_as(job))
            continue
        if not isinstance(job, FormatJob):
            continue
        _execute(job, event_queue)


def allocate_temp(destination: str) -> str:
    """Create an empty temp file in ``destination``'s directory and close it.

    The parent keeps this path so Cancel can delete it without reading a
    queue. The worker opens it only to write formatted bytes.
    """
    directory = os.path.dirname(os.path.abspath(destination))
    descriptor, name = tempfile.mkstemp(
        prefix=TEMP_PREFIX,
        suffix=TEMP_SUFFIX,
        dir=directory,
    )
    os.close(descriptor)
    return name


def unlink_temp(path: str | None) -> None:
    """Delete a Galley temp file. Other paths are left alone."""
    if path is None or not _is_temp_name(path):
        return
    try:
        os.unlink(path)
    except OSError:
        return


def _execute(job: FormatJob, event_queue: Queue) -> None:
    if job.fault not in _KNOWN_FAULTS:
        event_queue.put(FailureEvent(job.job_id, "Unknown fault.", None))
        return
    event_queue.put(ProgressEvent(job.job_id, "read", 0, 0, None))
    early = collect_problems(job.text, job.source)
    if early:
        if job.mask is not None:
            early = _masked_problems(early)
        event_queue.put(FailureEvent(job.job_id, "", None, None, early))
        return
    try:
        text, masked_count = _format_text(job)
    except MaskError:
        event_queue.put(FailureEvent(job.job_id, MASK_FAILURE_MESSAGE, None))
        return
    except Exception as exc:
        event_queue.put(_job_failure(job, exc, None))
        return
    # Minified JSON is one line, and Validate writes nothing to fold.
    folds = b"" if job.minify or job.validate else _scan_folds(text, job.target)
    payload = text.encode("utf-8")
    if job.destination is None and job.temp_path is None:
        event_queue.put(SuccessEvent(job.job_id, text, None, folds, masked_count))
        return
    temp_path: str | None = None
    try:
        temp_path = _write_output(job, payload, event_queue)
    except Exception as exc:
        event_queue.put(_job_failure(job, exc, temp_path))
        return
    if job.fault == FAULT_AFTER_TEMP:
        event_queue.put(FailureEvent(job.job_id, "forced failure", temp_path))
        return
    event_queue.put(SuccessEvent(job.job_id, text, temp_path, folds, masked_count))


def _format_text(job: FormatJob) -> tuple[str, int | None]:
    """The job's output text, and its masked count when ``mask`` is set."""
    if job.validate:
        validate_document(job.text, job.source)
        return "", None
    if job.mask is None:
        text = format_document(
            job.text,
            job.source,
            job.target,
            sort_keys=job.sort_keys,
            minify=job.minify,
            indent=job.indent,
        )
        return text, None
    masked = mask_document(
        job.text,
        job.source,
        job.target,
        job.mask,
        sort_keys=job.sort_keys,
        indent=job.indent,
    )
    return masked.text, masked.masked_count


def _scan_folds(text: str, target: str) -> bytes:
    """Fold ranges for the Output, found here so the UI thread does not scan.

    Only JSON and YAML Output folds. XML and HTML have no fold ranges yet.
    """
    kind = format_named(target).name
    if kind == "yaml":
        return pack_ranges(fold_ranges(text, "YAML"), line_count(text))
    if kind == "json":
        return pack_ranges(fold_ranges(text, "JSON"), line_count(text))
    return b""


def _write_output(job: FormatJob, payload: bytes, event_queue: Queue) -> str:
    path = job.temp_path
    if path is None:
        if job.destination is None:
            raise ValueError("A destination is required to write output.")
        path = allocate_temp(job.destination)
    try:
        # The handle is closed before this returns so Windows can replace it.
        with open(path, "wb") as handle:
            _write_chunks(job, payload, path, handle, event_queue)
    except Exception:
        unlink_temp(path)
        raise
    return path


def _write_chunks(
    job: FormatJob,
    payload: bytes,
    path: str,
    handle: BinaryIO,
    event_queue: Queue,
) -> None:
    total = len(payload)
    _report_write(event_queue, job.job_id, path, 0, total)
    written = 0
    step = job.chunk_size if job.chunk_size > 0 else 1
    pause_s = job.chunk_pause_s if job.chunk_pause_s > 0 else 0.0
    every = WRITE_PROGRESS_EVERY_CHUNKS if WRITE_PROGRESS_EVERY_CHUNKS > 0 else 1
    chunks = 0
    while written < total:
        end = min(written + step, total)
        handle.write(payload[written:end])
        written = end
        handle.flush()
        chunks += 1
        if chunks % every == 0 or written == total:
            _report_write(event_queue, job.job_id, path, written, total)
        if pause_s > 0 and written < total:
            time.sleep(pause_s)
    os.fsync(handle.fileno())


def _report_write(
    event_queue: Queue,
    job_id: int,
    temp_path: str,
    written: int,
    total: int,
) -> None:
    event_queue.put(ProgressEvent(job_id, "write", written, total, temp_path))


def _failure_event(
    job: FormatJob, exc: Exception, temp_path: str | None
) -> FailureEvent:
    error = exc if isinstance(exc, InputError) else None
    problems: tuple[Problem, ...] = ()
    if error is None:
        problems = collect_problems(job.text, job.source, exc)
    return FailureEvent(
        job.job_id,
        _failure_message(exc),
        temp_path,
        error,
        problems,
        not_json_value=isinstance(exc, NotJSONValueError),
    )


def _failure_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _job_failure(job: FormatJob, exc: Exception, temp_path: str | None) -> FailureEvent:
    if job.mask is None:
        return _failure_event(job, exc, temp_path)
    return _masked_failure(job, exc, temp_path)


def _masked_failure(
    job: FormatJob, exc: Exception, temp_path: str | None
) -> FailureEvent:
    """The failure a masked job sends: no value and no quoted source line.

    Parser messages quote the document, such as ruamel's ``original value:
    "…"`` and the line under it, so the message is rebuilt from the error's
    kind, the key a duplicate repeats, and the line and column, as in
    ``duplicate_key "password" at line 2, column 1``. The kind is a name,
    like ``InputError.kind``; the app chooses the words. Problems and
    ``InputError`` details keep only what can't hold a value.
    """
    plain = _failure_event(job, exc, temp_path)
    problems = _masked_problems(plain.problems)
    error = None if plain.error is None else _masked_error(plain.error)
    return FailureEvent(
        job.job_id,
        _masked_message(exc, problems),
        temp_path,
        error,
        problems,
        not_json_value=plain.not_json_value,
    )


def _masked_problems(problems: tuple[Problem, ...]) -> tuple[Problem, ...]:
    return tuple(
        Problem(
            problem.code,
            problem.lineno,
            _masked_token(problem),
            problem.colno,
            problem.uncertain,
        )
        for problem in problems
    )


def _masked_token(problem: Problem) -> str:
    token = problem.token
    if not token or token in _BRACKETS:
        return token
    if problem.code in _DUPLICATE_CODES and not looks_personal(token):
        return token
    return MASK


def _masked_error(error: InputError) -> InputError:
    details = {
        name: value
        for name, value in error.details.items()
        if isinstance(value, (int, float))
        or (isinstance(value, str) and _ESCAPE_RE.fullmatch(value))
    }
    return InputError(
        error.kind, lineno=error.lineno, colno=error.colno, details=details
    )


def _masked_message(exc: Exception, problems: tuple[Problem, ...]) -> str:
    if isinstance(exc, OSError):
        # A file the job couldn't write: its path, not document text.
        return _failure_message(exc)
    key = next(
        (p.token for p in problems if p.code in _DUPLICATE_CODES and p.token), ""
    )
    named = f' "{key}"' if key else ""
    # Some json messages end in "at", such as "Unterminated string starting
    # at", and the place adds its own.
    kind = _failure_kind(exc).removesuffix(" at")
    return kind + named + _failure_place(exc, problems)


def _failure_kind(exc: Exception) -> str:
    """The kind of failure, from fixed text only."""
    if isinstance(exc, (JSONDuplicateKeyError, DuplicateKeyError)):
        return "duplicate_key"
    if isinstance(exc, json.JSONDecodeError):
        # The json module's own fixed wording, such as "Expecting value".
        return exc.msg
    if isinstance(exc, InputError):
        return exc.kind
    if isinstance(exc, NotJSONValueError):
        # Names only a type: "JSON can't hold a date value".
        return _failure_message(exc)
    if isinstance(exc, MarkedYAMLError):
        # ScannerError is yaml_scanner_error.
        return "yaml_" + _CAMEL_RE.sub("_", type(exc).__name__).lower()
    return type(exc).__name__


def _failure_place(exc: Exception, problems: tuple[Problem, ...]) -> str:
    """`` at line L, column C`` for a masked failure, or ``""`` with no line.

    A YAML error takes its first problem, the row the app shows for it,
    because that row can sit on an earlier line than the parser's mark. A
    problem with no column (``colno`` 0, as when it moved to an earlier
    line) gives the line alone. Without a problem, YAML takes its 0-based
    ``problem_mark``. ``context_mark`` is never used: it marks where the
    enclosing construct began, not the error, and the problems don't use it
    either. JSON decode, repeated-key and input errors take their own
    ``lineno`` and ``colno``. Anything else, such as a ``ReaderError`` (a
    ``YAMLError`` with no mark), takes its first problem.
    """
    line: int | None = None
    column: int | None = None
    mark = getattr(exc, "problem_mark", None)
    if isinstance(exc, MarkedYAMLError) and problems:
        line, column = problems[0].lineno, problems[0].colno
    elif isinstance(exc, MarkedYAMLError) and mark is not None:
        line, column = mark.line + 1, mark.column + 1
    elif isinstance(getattr(exc, "lineno", None), int):
        line, column = exc.lineno, getattr(exc, "colno", None)
    elif problems:
        line, column = problems[0].lineno, problems[0].colno
    if line is None:
        return ""
    if column:
        return f" at line {line}, column {column}"
    return f" at line {line}"


def run_compare(job: CompareJob) -> CompareEvent | CompareFailureEvent:
    """Load both sides, then diff them and build the report.

    Left is checked before right. This is the whole compare job without the
    queue, so it also runs in-process. A side that fails stops the job
    before ``diff`` runs; its problems are located in the text as sent. A
    side whose format does not load to a value, such as XML, fails before
    it is read, whatever the app offered.
    """
    sides: tuple[tuple[CompareSide, str, str], ...] = (
        ("left", job.left_text, job.left_format),
        ("right", job.right_text, job.right_format),
    )
    for side, _text, source in sides:
        refused = not_comparable(source)
        if refused is not None:
            return CompareFailureEvent(job.job_id, side, refused)
    docs = []
    texts: list[str] = []
    for side, text, source in sides:
        early = collect_problems(text, source)
        if early:
            return CompareFailureEvent(job.job_id, side, "", None, early)
        try:
            if job.reformat:
                shown, doc = _reformatted(text, source)
            else:
                shown, doc = text, load_document(text, source)
        except Exception as exc:
            return _compare_failure(job.job_id, side, text, source, exc)
        docs.append(doc)
        texts.append(shown)
    if job.hold_s > 0:
        time.sleep(job.hold_s)
    try:
        result, pairs = diff_with_pairs(docs[0], docs[1])
        report = build_report(result, docs[0], docs[1], pairs)
    except Exception as exc:
        error = exc if isinstance(exc, InputError) else None
        return CompareFailureEvent(job.job_id, None, _failure_message(exc), error)
    if not job.reformat:
        return CompareEvent(job.job_id, result, report)
    return CompareEvent(job.job_id, result, report, texts[0], texts[1])


def _reformatted(text: str, source: str) -> tuple[str, Doc]:
    """Format ``text`` in its own format, then locate the formatted values.

    JSON keeps the value the formatter loaded and only scans the formatted
    text for spans, so it is not parsed a second time; the result equals
    ``load_document`` of the formatted text. YAML spans come from the full
    parse, so YAML loads the formatted text. Keys are never sorted.
    """
    if format_named(source).name != "json":
        shown = format_document(text, source, source)
        return shown, load_document(shown, source)
    loaded: list[object] = []

    def _keep(value: object) -> object:
        loaded.append(value)
        return value

    shown = format_document(text, source, source, prepare=_keep)
    return shown, Doc(loaded[0], "json", _json_spans(shown))


def not_comparable(source: str) -> str | None:
    """Why a ``source`` document can't be compared, or None when it can.

    An unknown name is None here. Loading that side reports it. Compare
    also refuses to open such a file with these words.
    """
    try:
        fmt = format_named(source)
    except ValueError:
        return None
    if fmt.loads_to_value:
        return None
    return f"{fmt.label} can't be compared."


def _execute_compare(job: CompareJob, event_queue: Queue) -> None:
    event_queue.put(ProgressEvent(job.job_id, "read", 0, 0, None))
    event_queue.put(run_compare(job))


def _compare_failure(
    job_id: int, side: CompareSide, text: str, source: str, exc: Exception
) -> CompareFailureEvent:
    error = exc if isinstance(exc, InputError) else None
    problems: tuple[Problem, ...] = ()
    if error is None:
        problems = collect_problems(text, source, exc)
    return CompareFailureEvent(job_id, side, _failure_message(exc), error, problems)


def run_copy_as(job: CopyAsJob) -> CopyAsEvent | CopyAsFailureEvent:
    """Build the Copy As text without the queue, so it also runs in-process."""
    if job.hold_s > 0:
        time.sleep(job.hold_s)
    try:
        result = copy_as_text(job.text, job.fmt, job.mode, job.rules)
    except CopyAsError as exc:
        return CopyAsFailureEvent(job.job_id, exc.mode, exc.invalid)
    return CopyAsEvent(job.job_id, result)


def _is_temp_name(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)
