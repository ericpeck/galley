"""Benchmark galley.core against synthetic JSON, YAML, XML, and HTML files.

Generates nested documents in a temporary directory, times the format,
validate, and convert callables that exist on this checkout, and writes a
markdown table plus JSON. Generated inputs are deleted with the temp directory.
Both result files are rewritten after every completed size, including the
cutoff summary computed from the rows so far.

``format_yaml`` is timed as shipped: ruamel ``typ="safe"``. When
``ruamel.yaml.clib`` imports, the C loader parses and the pure-Python emitter
writes the result, except documents whose tokens need a full pure-Python
reparse. Without the C library the pure-Python parser does both. YAML UI
cutoffs use that row. Round-trip and safe+C are labeled comparisons. Each
call reports load and dump wall time. Nothing here changes ``galley.core``.

``--corpus plain`` (the default) is the historical synthetic catalog and the
historical rows, so those results stay comparable with earlier runs.
``--corpus realistic`` times the realistic-corpus rows and the plain shipped
C-path row. ``--corpus all`` runs both.

The realistic corpus is seeded (``REALISTIC_SEED``) so a size is the same
UTF-8 bytes on every run. Each file is one ``kind: List`` document of nested
maps and lists shaped like Kubernetes manifests, application config, and CI
jobs. A trailing ``padding`` value is ASCII ``x`` and only exists to make the
file an exact size. Indentation is spaces. Trigger features are chosen here.
They are not something the benchmark discovered by measuring.

Natural mix, on by item index:

- nested maps and lists, and an ASCII comment, on every item
- a block scalar on most items (``index % 5 != 4``, about 4/5). Even indexes
  use a ``|`` literal script. Odd indexes use a ``>`` folded summary. The
  indent is the normal four-space body. This generator does not emit the
  rule-1 block-scalar case (a whitespace-only first content line with a
  later line indented further). That shape is rejected by the pure parser,
  so there is no single-trigger row for it
- accented Latin in a title when ``index % 4 != 3`` (about 3/4), and an
  accented comment when ``index % 6 == 5``
- a small CJK value when ``index % 20 == 17``
- an emoji when ``index % 12 == 7``
- ``!!binary`` (a core tag) when ``index % 13 == 5``
- a ``%YAML 1.2`` document embedded in a ``|`` block when ``index % 17 == 4``
  (about 1/17). That text is scalar content, not a ``%YAML`` directive
- a bare tag ``channel: ! stable`` when ``index % 15 == 11`` (1/15).
  That is a bare ``!`` and the non-empty plain scalar ``stable`` on the
  same line
- a double-quoted ``\\`` path when ``index % 5 == 0``
- a tab inside a single-line double-quoted string, ``column: "name\\tregion"``,
  when ``index % 16 == 13`` (1/16). The tab is never in the indent

A bare ``!`` keeps the C value only when the same line continues with a
non-empty scalar or a flow collection. An empty ``!`` node, ``! ""``, and
a block collection on the next line still re-parse. A tab anywhere in
the text forces a pure re-parse, including a tab inside a comment or a
single-line quoted scalar. A ``%YAML`` or ``%TAG`` directive always
re-parses. The generator is left unchanged so the mixed corpus keeps
this mix.
``format_yaml_realistic_clib`` includes a quoted tab once the file reaches
that item, and every default ladder size does, so that row must be a pure
re-parse. The bare-tag row keeps the C value. The clean profile turns the
bare tag, the quoted tab, and the embedded ``%YAML`` text off (no bare or
non-core tag, no ``%YAML``/``%TAG`` directive, no tab, no odd block-scalar
indent) and keeps the block scalars,
accented text, emoji, CJK, ``!!binary``, and escapes.
``format_yaml_realistic_clean_pure`` and ``format_yaml_realistic_clean_clib``
time that profile. Clean clib must be C parse plus pure writer at every size.

Each single-trigger row is the clean profile plus one injection in the
document header, once:

- ``format_yaml_realistic_only_bare_tag_clib`` adds ``channel: ! stable``
  and must be C parse plus pure writer at every size
- ``format_yaml_realistic_only_tab_in_quotes_clib`` adds
  ``column: "name\\tregion"`` and must be a pure re-parse at every size
- ``format_yaml_realistic_only_directive_clib`` prefixes ``%YAML 1.2`` and
  ``---`` and must be a pure re-parse at every size

Those rows stop at 1 MB. A size on a different path fails the bench.

``format_yaml_realistic_clib_reparse`` keeps the natural item sequence and
adds a leading ``%YAML 1.2`` directive and ``---``. Parsed item values stay
the same. That row keeps the 0.05–5 MB ladder. The UI warning size comes
from it.

Shipped C-path rows classify that path by wrapping ``YAML.scan``,
``YAML.load``, and ``YAML.dump`` and reading whether the parser and emitter
were the C classes or the pure-Python classes. Pure re-parse is a C scan or
C load followed by a pure-Python load, with no C error. C parse plus pure
writer is a C load and a pure dump with no second load. C rejected and fell
back is a C error and then pure Python. C only is a C load and a C dump.
Detection does not look at confirm-mark characters. ``yaml_safe_c_realistic``
is benchmark-local C parse plus C writer on the realistic corpus, not
``format_yaml``.

Default ladders keep a three-run macos-14 ``--corpus plain`` job near 20
minutes. ``--corpus all`` adds the realistic rows and is sized to stay under
about 45 minutes on macos-14. JSON is 1, 5, 10, 50, and 100 MB. Shipped
YAML, round-trip, and safe+C are 0.05, 0.1, 0.25, 0.5, 1, 5, and 10 MB.
The natural realistic rows, the clean rows, the forced reparse row,
realistic safe+C, and ``format_yaml_plain_clib`` are 0.05, 0.1, 0.25, 0.5,
1, and 5 MB. Each single-trigger row is 0.05, 0.1, 0.25, 0.5, and 1 MB.
``--sizes`` replaces those ladders for every selected row, including sizes
above the caps.

``format_xml`` (#63) times XML Format on the plain catalog written as XML:
one ``<item>`` element per record, with attributes, nested elements, and
text-only leaves. Its 0.5 s and 2 s points are where the XML busy and
progress bands in ``galley.app.limits`` come from. It uses the JSON ladder
plus 2 MB, the Format limit XML shares with HTML.

``format_html`` (#63) times HTML Format on the plain catalog written as an
HTML page: one ``<article>`` per record, with a heading, an inline run, a
list whose end tags are left out, and a comment. ``html.parser`` is pure
Python, so it has its own ladder, which reaches past the 2 s point.

``copy_as_masked_json``, ``copy_as_fake_json``, ``copy_as_masked_yaml`` and
``copy_as_fake_yaml`` time Copy As › Masked and › Random fake (#66) as
shipped, ``mask.copy_as_text``, on the plain corpus with the JSON and YAML
ladders above. Each call is timed whole, like the convert rows: the load,
the rewrite and the write all run inside ``format_document``. These rows are
in their own ``copy_as`` group, so the Format UI cutoffs do not use them.

    uv run python benchmarks/size_bench.py
    uv run python benchmarks/size_bench.py --corpus realistic
    uv run python benchmarks/size_bench.py --corpus all
    uv run python benchmarks/size_bench.py --sizes 1 5 10 50 100 200
    uv run --with ruamel.yaml.clib==0.2.15 python benchmarks/size_bench.py
"""

import argparse
import ast
import atexit
import ctypes
import gc
import importlib.metadata
import importlib.util
import inspect
import json
import math
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.main import CEmitter, CParser

import galley.core
from galley.core import format as core_format
from galley.core.convert_python import ConvertError, convert_python
from galley.core.html_format import format_html
from galley.core.mask import CopyAsMode, copy_as_text
from galley.core.xml_format import format_xml, validate_xml

BYTES_PER_MB = 1024 * 1024
# Default ladders. `--sizes` replaces them for every operation.
# JSON stays through 100 MB: on macos-14 that was about 32 s per run, while
# 200 MB was about 64 s and is not needed for the 0.5 s / 2 s bands.
DEFAULT_JSON_SIZES_MB = (1, 5, 10, 50, 100)
# Capped at 10 MB. The 50/100/200 MB pure-Python points dominated the macOS job.
DEFAULT_YAML_SIZES_MB = (0.05, 0.1, 0.25, 0.5, 1, 5, 10)
# Same grid as pure-Python YAML so the 0.5 s and 2 s bands can be interpolated.
DEFAULT_SAFE_C_SIZES_MB = DEFAULT_YAML_SIZES_MB
# Natural realistic rows, clean rows, and the forced reparse row.
# Capped at 5 MB. The forced reparse ladder stays through 5 MB because the
# UI warning size is read from it and can land above 1 MB.
DEFAULT_REALISTIC_SIZES_MB = (0.05, 0.1, 0.25, 0.5, 1, 5)
# One rule-1 trigger, injected once. Same curve as the forced reparse row
# through 1 MB, so the 5 MB point is not repeated for each trigger.
DEFAULT_TRIGGER_SIZES_MB = (0.05, 0.1, 0.25, 0.5, 1)
# Convert from Python rows. The UI limits in ``galley.app.limits`` are about
# 1 MB to JSON and 192 KB to YAML, so the ladder brackets both.
DEFAULT_PYTHON_SIZES_MB = (0.1, 0.25, 0.5, 1, 2, 5)
# XML Format is expat in C plus slicing, close to JSON, so it follows that
# ladder. 1 MB is Isaac's 0.5 s budget point, and 2 MB is the Format limit
# XML shares with HTML (#63 slice 5).
DEFAULT_XML_SIZES_MB = (1, 2, 5, 10, 50, 100)
# HTML Format tokenizes in pure Python, about 0.25 s per MB on macOS arm64,
# so 1 MB is the 0.7 s macOS budget point (1.0 s on Windows) and 20 MB is
# past the 2 s band.
DEFAULT_HTML_SIZES_MB = (0.25, 0.5, 1, 2, 5, 10, 20)
# Pinned so later safe+C runs stay comparable. Same version as the project.
RUAMEL_CLIB_SPEC = "ruamel.yaml.clib==0.2.15"
# Realistic documents replay this seed. Item ``i`` uses ``REALISTIC_SEED + i``.
REALISTIC_SEED = 1729
_TRIGGER_OPERATION_IDS = (
    "format_yaml_realistic_only_bare_tag_clib",
    "format_yaml_realistic_only_tab_in_quotes_clib",
    "format_yaml_realistic_only_directive_clib",
)
_NEW_OPERATION_IDS = (
    "format_yaml_realistic_pure",
    "format_yaml_realistic_clib",
    "format_yaml_realistic_clib_reparse",
    "format_yaml_plain_clib",
    "yaml_safe_c_realistic",
    "format_yaml_realistic_clean_pure",
    "format_yaml_realistic_clean_clib",
    *_TRIGGER_OPERATION_IDS,
)
_PURE_OPERATION_IDS = frozenset(
    {
        "format_yaml_realistic_pure",
        "format_yaml_realistic_clean_pure",
    }
)
_SHIPPED_CLIB_IDS = (
    "format_yaml_realistic_clib",
    "format_yaml_realistic_clib_reparse",
    "format_yaml_plain_clib",
    "format_yaml_realistic_clean_clib",
    *_TRIGGER_OPERATION_IDS,
)
_CLIB_WORKER_IDS = frozenset(
    {
        "yaml_safe_c",
        "yaml_safe_c_realistic",
        *_SHIPPED_CLIB_IDS,
    }
)
# Observed engines for one shipped format_yaml call. The worker classifier
# writes the path id; the timer reads it.
PATH_PURE_REPARSE = "pure-reparse"
PATH_C_PARSE_PURE_WRITER = "c-parse-pure-writer"
PATH_C_REJECTED = "c-rejected"
PATH_C_ONLY = "c-only"
PATH_C_PARSE_C_WRITER = "c-parse-c-writer"
_PATH_LABELS = {
    PATH_PURE_REPARSE: "pure re-parse",
    PATH_C_PARSE_PURE_WRITER: "C parse plus pure writer",
    PATH_C_REJECTED: "C rejected and fell back",
    PATH_C_ONLY: "C only",
    PATH_C_PARSE_C_WRITER: "C parse plus C writer",
}
_observed_path: list[str | None] = [None]
_PURE_PYTHON: str | None = None
_SHIPPED_PYTHON: str | None = None
DEFAULT_RUNS = 3
CUTOFF_SECONDS = (0.5, 2.0)
SAMPLE_INTERVAL_S = 0.01

JSON_HEADER = '{\n  "catalog": "galley-size-bench",\n  "version": 1,\n  "items": [\n'
JSON_SEPARATOR = ",\n"
JSON_FOOTER_PREFIX = '\n  ],\n  "padding": "'
JSON_FOOTER_SUFFIX = '"\n}\n'

YAML_HEADER = 'catalog: "galley-size-bench"\nversion: 1\nitems:\n'
YAML_SEPARATOR = ""
YAML_FOOTER_PREFIX = 'padding: "'
YAML_FOOTER_SUFFIX = '"\n'

XML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<catalog name="galley-size-bench" version="1">\n'
)
XML_SEPARATOR = ""
XML_FOOTER_PREFIX = "  <padding>"
XML_FOOTER_SUFFIX = "</padding>\n</catalog>\n"

HTML_HEADER = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    "<title>galley-size-bench</title>\n</head>\n<body>\n<main>\n"
)
HTML_SEPARATOR = ""
HTML_FOOTER_PREFIX = '<p class="padding">'
HTML_FOOTER_SUFFIX = "</p>\n</main>\n</body>\n</html>\n"

PYTHON_HEADER = "{'catalog': 'galley-size-bench', 'version': 1, 'items': [\n"
PYTHON_SEPARATOR = ",\n"
PYTHON_FOOTER_PREFIX = "\n], 'padding': '"
PYTHON_FOOTER_SUFFIX = "'}\n"

OPERATION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "operation_id": "format_json",
        "group": "format",
        "source_format": "json",
        "names": ("format_json",),
    },
    {
        "operation_id": "format_yaml",
        "group": "format",
        "source_format": "yaml",
        "names": ("format_yaml",),
    },
    {
        "operation_id": "validate_json",
        "group": "validate",
        "source_format": "json",
        "names": ("validate_json",),
    },
    {
        "operation_id": "validate_yaml",
        "group": "validate",
        "source_format": "yaml",
        "names": ("validate_yaml",),
    },
    {
        "operation_id": "json_to_yaml",
        "group": "convert",
        "source_format": "json",
        "names": ("json_to_yaml", "convert_json_to_yaml"),
    },
    {
        "operation_id": "yaml_to_json",
        "group": "convert",
        "source_format": "yaml",
        "names": ("yaml_to_json", "convert_yaml_to_json"),
    },
)


# result, total seconds, load seconds, dump seconds
PhaseResult = tuple[object, float, float, float]
PhaseFn = Callable[[str], PhaseResult]


@dataclass(frozen=True)
class Operation:
    operation_id: str
    group: str
    source_format: str
    fn: Callable[..., object]
    phases: PhaseFn
    yaml_typ: str | None = None
    # "ui" sizes feed the spinner / progress-bar bands. "comparison" is reported
    # beside them and is not the product cutoff.
    cutoff_role: str = "ui"
    # "plain" is the historical synthetic catalog. "realistic" is the seeded
    # config-shaped corpus. JSON rows stay on "plain".
    corpus: str = "plain"
    # Shipped C-path rows classify the engines YAML.load / YAML.dump used.
    track_path: bool = False
    # When set, every measured run must take this path or the bench fails.
    require_path: str | None = None
    # When set, a different path is recorded and called out. The bench continues.
    expect_path: str | None = None
    # Benchmark-local rows that are one path by construction, not detected.
    reported_path: str | None = None


def _accepts_text(candidate: object) -> bool:
    if not callable(candidate):
        return False
    try:
        signature = inspect.signature(candidate)
    except (TypeError, ValueError):
        return False
    required = [
        param
        for param in signature.parameters.values()
        if param.kind
        not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
        and param.default is inspect.Parameter.empty
    ]
    return len(required) == 1


def _find_callable(names: tuple[str, ...]) -> Callable[..., object] | None:
    for name in names:
        for module in (galley.core, core_format):
            candidate = getattr(module, name, None)
            if _accepts_text(candidate):
                return candidate
    return None


def discover_operations() -> tuple[list[Operation], list[dict[str, str]]]:
    found: list[Operation] = []
    skipped: list[dict[str, str]] = []
    for spec in OPERATION_SPECS:
        fn = _find_callable(spec["names"])
        if fn is None:
            skipped.append(
                {
                    "operation_id": spec["operation_id"],
                    "group": spec["group"],
                    "source_format": spec["source_format"],
                    "reason": "not defined on galley.core",
                }
            )
            continue
        yaml_typ = "safe" if spec["operation_id"] == "format_yaml" else None
        found.append(
            Operation(
                operation_id=spec["operation_id"],
                group=spec["group"],
                source_format=spec["source_format"],
                fn=fn,
                phases=_phases_for(spec["operation_id"], fn),
                yaml_typ=yaml_typ,
                cutoff_role="ui",
            )
        )
    round_trip = Operation(
        operation_id="yaml_round_trip",
        group="format",
        source_format="yaml",
        fn=format_yaml_round_trip,
        phases=_round_trip_phases,
        yaml_typ="rt",
        cutoff_role="comparison",
    )
    safe_c = Operation(
        operation_id="yaml_safe_c",
        group="format",
        source_format="yaml",
        fn=format_yaml_safe_c,
        phases=_safe_c_phases,
        yaml_typ="safe-c",
        cutoff_role="comparison",
    )
    # Shipped format_yaml is the YAML UI path. Round-trip and safe+C follow it
    # as labeled comparisons.
    scheduled = [
        operation for operation in found if operation.operation_id != "format_yaml"
    ]
    shipped = next(
        (operation for operation in found if operation.operation_id == "format_yaml"),
        None,
    )
    insert_at = len(scheduled)
    for index, operation in enumerate(scheduled):
        if operation.group == "format" and operation.source_format != "yaml":
            insert_at = index + 1
    block: list[Operation] = []
    if shipped is not None:
        block.append(shipped)
    block.extend((round_trip, safe_c))
    scheduled[insert_at:insert_at] = block
    # New rows stay after the historical operations so a `--corpus all` table
    # keeps the earlier row order. The worker must see them even when the
    # parent run filters by corpus.
    scheduled.extend(
        (
            Operation(
                operation_id="format_yaml_realistic_pure",
                group="format",
                source_format="yaml",
                fn=core_format.format_yaml,
                phases=_format_yaml_realistic_pure_phases,
                yaml_typ="safe",
                cutoff_role="comparison",
                corpus="realistic",
            ),
            Operation(
                operation_id="format_yaml_realistic_clib",
                group="format",
                source_format="yaml",
                fn=core_format.format_yaml,
                phases=_format_yaml_shipped_clib_phases,
                yaml_typ="safe-clib",
                cutoff_role="comparison",
                corpus="realistic",
                track_path=True,
                require_path=PATH_PURE_REPARSE,
            ),
            Operation(
                operation_id="format_yaml_realistic_clib_reparse",
                group="format",
                source_format="yaml",
                fn=core_format.format_yaml,
                phases=_format_yaml_shipped_clib_phases,
                yaml_typ="safe-clib",
                cutoff_role="comparison",
                corpus="realistic-reparse",
                track_path=True,
                require_path=PATH_PURE_REPARSE,
            ),
            Operation(
                operation_id="format_yaml_plain_clib",
                group="format",
                source_format="yaml",
                fn=core_format.format_yaml,
                phases=_format_yaml_shipped_clib_phases,
                yaml_typ="safe-clib",
                cutoff_role="comparison",
                corpus="plain",
                track_path=True,
            ),
            Operation(
                operation_id="yaml_safe_c_realistic",
                group="format",
                source_format="yaml",
                fn=format_yaml_safe_c,
                phases=_safe_c_phases,
                yaml_typ="safe-c",
                cutoff_role="comparison",
                corpus="realistic",
                reported_path=PATH_C_PARSE_C_WRITER,
            ),
            _shipped_pure_operation(
                "format_yaml_realistic_clean_pure",
                corpus="realistic-clean",
            ),
            _shipped_clib_operation(
                "format_yaml_realistic_clean_clib",
                corpus="realistic-clean",
                require_path=PATH_C_PARSE_PURE_WRITER,
            ),
            _shipped_clib_operation(
                "format_yaml_realistic_only_bare_tag_clib",
                corpus="realistic-only-bare-tag",
                require_path=PATH_C_PARSE_PURE_WRITER,
            ),
            _shipped_clib_operation(
                "format_yaml_realistic_only_tab_in_quotes_clib",
                corpus="realistic-only-tab",
                require_path=PATH_PURE_REPARSE,
            ),
            _shipped_clib_operation(
                "format_yaml_realistic_only_directive_clib",
                corpus="realistic-only-directive",
                require_path=PATH_PURE_REPARSE,
            ),
        )
    )
    # Convert from Python runs on the UI thread. Its 0.5 s point is the limit
    # in galley.app.limits, so these are UI rows on the plain catalog.
    scheduled.extend(
        Operation(
            operation_id=f"python_to_{target}",
            group="convert",
            source_format="python",
            fn=convert_python,
            phases=_python_phases(target),
        )
        for target in ("json", "yaml")
    )
    # XML Format (#63) runs in the worker, like JSON and YAML. It is a UI row
    # on the plain catalog.
    scheduled.append(
        Operation(
            operation_id="format_xml",
            group="format",
            source_format="xml",
            fn=format_xml,
            phases=_xml_phases,
        )
    )
    # HTML Format (#63) also runs in the worker. Format is one pass, so the
    # row is timed whole.
    scheduled.append(
        Operation(
            operation_id="format_html",
            group="format",
            source_format="html",
            fn=format_html,
            phases=_whole_phases(format_html),
        )
    )
    scheduled.extend(
        _copy_as_operation(source_format, mode)
        for source_format in ("json", "yaml")
        for mode in COPY_AS_MODES
    )
    return scheduled, skipped


# Copy As modes the bench times. Original copies the text as it is.
COPY_AS_MODES: tuple[CopyAsMode, ...] = ("masked", "fake")


def _copy_as_operation(source_format: str, mode: CopyAsMode) -> Operation:
    def copy(text: str) -> str:
        return copy_as_text(text, source_format, mode).text

    return Operation(
        operation_id=f"copy_as_{mode}_{source_format}",
        group="copy_as",
        source_format=source_format,
        fn=copy,
        phases=_whole_phases(copy),
    )


def _shipped_pure_operation(operation_id: str, *, corpus: str) -> Operation:
    return Operation(
        operation_id=operation_id,
        group="format",
        source_format="yaml",
        fn=core_format.format_yaml,
        phases=_format_yaml_realistic_pure_phases,
        yaml_typ="safe",
        cutoff_role="comparison",
        corpus=corpus,
    )


def _shipped_clib_operation(
    operation_id: str,
    *,
    corpus: str,
    require_path: str | None = None,
    expect_path: str | None = None,
) -> Operation:
    return Operation(
        operation_id=operation_id,
        group="format",
        source_format="yaml",
        fn=core_format.format_yaml,
        phases=_format_yaml_shipped_clib_phases,
        yaml_typ="safe-clib",
        cutoff_role="comparison",
        corpus=corpus,
        track_path=True,
        require_path=require_path,
        expect_path=expect_path,
    )


def _phases_for(operation_id: str, fn: Callable[..., object]) -> PhaseFn:
    if operation_id == "format_json":
        return _json_phases
    if operation_id == "format_yaml":
        return _format_yaml_phases
    return _whole_phases(fn)


def _python_phases(target: str) -> PhaseFn:
    """Time Convert from Python to ``target`` as the window calls it."""

    def phases(text: str) -> PhaseResult:
        started = time.perf_counter()
        result = convert_python(text, target)
        total = time.perf_counter() - started
        if isinstance(result, ConvertError):
            raise RuntimeError(f"Convert from Python failed: {result.message}")
        return result, total, total, 0.0

    return phases


def _xml_phases(text: str) -> PhaseResult:
    """Time ``format_xml``. Load is the parse alone, ``validate_xml``.

    Format parses again, so dump is the whole call less one parse.
    """
    started = time.perf_counter()
    validate_xml(text)
    load_s = time.perf_counter() - started
    started = time.perf_counter()
    result = format_xml(text)
    total = time.perf_counter() - started
    return result, total, load_s, max(total - load_s, 0.0)


def _whole_phases(fn: Callable[..., object]) -> PhaseFn:
    def phases(text: str) -> PhaseResult:
        started = time.perf_counter()
        result = fn(text)
        total = time.perf_counter() - started
        return result, total, total, 0.0

    return phases


def _json_phases(text: str) -> PhaseResult:
    """Time ``format_json`` and split the parse from the write.

    Load runs from the start of the call through ``json.loads``, so the
    checks in front of the parser are included. Dump is the rest of the call,
    which is the write. Load plus dump is the whole call.
    """
    load_end: float | None = None
    real_loads = json.loads

    def timed_loads(*args: Any, **kwargs: Any) -> Any:
        nonlocal load_end
        try:
            return real_loads(*args, **kwargs)
        finally:
            load_end = time.perf_counter()

    json.loads = timed_loads
    try:
        started = time.perf_counter()
        result = core_format.format_json(text)
        total = time.perf_counter() - started
    finally:
        json.loads = real_loads
    load_s = 0.0 if load_end is None else load_end - started
    return result, total, load_s, total - load_s


def _assert_shipped_yaml_environment() -> None:
    """Fail when the C extension is only half installed.

    Shipped ``format_yaml`` uses the C parser when ``ruamel.yaml.clib``
    imports and writes with the pure-Python emitter, and the pure-Python
    parser when the library does not. A distribution with no loaded parser
    would time neither path.
    """
    clib_spec = importlib.util.find_spec("ruamel.yaml.clib")
    extension_spec = importlib.util.find_spec("_ruamel_yaml")
    installed = clib_spec is not None or extension_spec is not None
    loaded = CParser is not None or CEmitter is not None
    if installed and not loaded:
        message = "ruamel.yaml.clib is importable, but the C parser is not loaded."
        raise RuntimeError(message)
    if loaded and not installed:
        message = "The C parser is loaded, but ruamel.yaml.clib is not importable."
        raise RuntimeError(message)


def _format_yaml_phases(text: str) -> PhaseResult:
    """Time ``galley.core.format_yaml`` and split the parse from the write.

    Load runs from the start of the call through ``YAML.load``, so the checks
    in front of the parser are included. Dump is the rest of the call.
    """
    _assert_shipped_yaml_environment()
    load_end: float | None = None
    real_load = YAML.load

    def timed_load(self: YAML, stream: Any) -> Any:
        nonlocal load_end
        try:
            return real_load(self, stream)
        finally:
            load_end = time.perf_counter()

    YAML.load = timed_load  # type: ignore[method-assign]
    try:
        started = time.perf_counter()
        result = core_format.format_yaml(text)
        total = time.perf_counter() - started
    finally:
        YAML.load = real_load
    if load_end is None:
        message = "format_yaml did not call YAML.load; load time was not measured."
        raise RuntimeError(message)
    load_s = max(0.0, load_end - started)
    return result, total, load_s, total - load_s


def _require_yaml_clib() -> None:
    if CParser is None or CEmitter is None:
        message = "ruamel.yaml.clib is not installed; the safe+C comparison needs it."
        raise RuntimeError(message)


def _assert_clib_absent() -> None:
    """Fail when this process can see the ruamel C extension.

    The realistic pure row times shipped ``format_yaml`` where
    ``ruamel.yaml.clib`` is not importable. That is the same absence check
    the historical shipped row used before the C library was a project
    dependency.
    """
    clib_spec = importlib.util.find_spec("ruamel.yaml.clib")
    extension_spec = importlib.util.find_spec("_ruamel_yaml")
    if clib_spec is not None or extension_spec is not None:
        message = (
            "format_yaml_realistic_pure must run where ruamel.yaml.clib "
            "is not importable. This process can import the C extension."
        )
        raise RuntimeError(message)
    if CParser is not None or CEmitter is not None:
        message = (
            "format_yaml_realistic_pure must run where ruamel.yaml.clib "
            "is not importable. This process already loaded the C parser."
        )
        raise RuntimeError(message)


def _assert_clib_present() -> None:
    """Fail unless shipped ``format_yaml`` will take the C path."""
    clib_spec = importlib.util.find_spec("ruamel.yaml.clib")
    extension_spec = importlib.util.find_spec("_ruamel_yaml")
    if clib_spec is None and extension_spec is None:
        message = (
            "ruamel.yaml.clib is not importable. "
            f"The shipped C path needs {RUAMEL_CLIB_SPEC}."
        )
        raise RuntimeError(message)
    if CParser is None or CEmitter is None:
        message = "ruamel.yaml.clib is importable, but the C parser is not loaded."
        raise RuntimeError(message)
    if not core_format._clib_available():
        message = "format_yaml did not select the C parser and emitter."
        raise RuntimeError(message)


def _engine_is_c(engine: object, c_engine: object) -> bool:
    return c_engine is not None and engine is c_engine


def classify_observed_path(state: dict[str, bool]) -> str:
    """Classify one ``format_yaml`` call from the engines it actually used.

    ``c_scan_ok`` is a C ``YAML.scan`` that finished. ``c_load_ok`` is a
    successful C ``YAML.load``. ``pure_load`` is a pure ``YAML.load`` attempt.
    Dump flags follow ``YAML.dump``. A C error is a C scan, load, or dump
    that raised. Pure re-parse is a C scan or C load followed by a pure load,
    with no C error. That covers a second parse after a C load and a C scan
    that decides the document needs a full pure-Python parse. C rejection is
    a C error followed by pure Python. C parse plus pure writer is a C load
    and a pure dump with no pure load, which includes a value the C emitter
    hands to the pure emitter (#115). C only is a C load and C dump with no
    pure call.
    """
    c_load_ok = state.get("c_load_ok", False)
    pure_load = state.get("pure_load", False)
    c_dump = state.get("c_dump", False)
    pure_dump = state.get("pure_dump", False)
    c_scan_ok = state.get("c_scan_ok", False)
    c_error = (
        state.get("c_load_error", False)
        or state.get("c_dump_error", False)
        or state.get("c_scan_error", False)
    )
    if (c_load_ok or c_scan_ok) and pure_load and not c_error:
        return PATH_PURE_REPARSE
    if c_error and (pure_load or pure_dump):
        return PATH_C_REJECTED
    if c_load_ok and pure_dump and not pure_load:
        return PATH_C_PARSE_PURE_WRITER
    if c_load_ok and c_dump and not pure_load and not pure_dump and not c_error:
        return PATH_C_ONLY
    message = "YAML scan/load/dump calls did not match a known path."
    raise RuntimeError(message)


def _format_yaml_realistic_pure_phases(text: str) -> PhaseResult:
    """Time shipped ``format_yaml`` with the C extension absent."""
    _assert_clib_absent()
    return _format_yaml_phases(text)


def _format_yaml_shipped_clib_phases(text: str) -> PhaseResult:
    """Time shipped ``format_yaml`` and record which parser and writer ran.

    Load is every C ``YAML.scan`` and every ``YAML.load``, plus the time
    outside scan, load, and dump. Dump is every ``YAML.dump``. The path
    comes from the parser and emitter classes on those calls.
    """
    _assert_clib_present()
    state = {
        "c_scan_ok": False,
        "c_scan_error": False,
        "c_load_ok": False,
        "pure_load": False,
        "c_dump": False,
        "pure_dump": False,
        "c_load_error": False,
        "c_dump_error": False,
    }
    load_accounted = {"value": 0.0}
    dump_accounted = {"value": 0.0}
    real_scan = YAML.scan
    real_load = YAML.load
    real_dump = YAML.dump

    def wrapped_scan(self: YAML, stream: Any) -> Any:
        # ``scan`` yields tokens. Errors are raised while the caller iterates.
        started = time.perf_counter()
        is_c = _engine_is_c(self.Parser, CParser)
        iterator = real_scan(self, stream)

        def tracking() -> Any:
            try:
                yield from iterator
            except Exception:
                if is_c:
                    state["c_scan_error"] = True
                raise
            else:
                if is_c:
                    state["c_scan_ok"] = True
            finally:
                if is_c:
                    load_accounted["value"] += time.perf_counter() - started

        return tracking()

    def wrapped_load(self: YAML, stream: Any) -> Any:
        started = time.perf_counter()
        try:
            parsed = real_load(self, stream)
        except Exception:
            if _engine_is_c(self.Parser, CParser):
                state["c_load_error"] = True
            else:
                state["pure_load"] = True
            raise
        else:
            if _engine_is_c(self.Parser, CParser):
                state["c_load_ok"] = True
            else:
                state["pure_load"] = True
            return parsed
        finally:
            load_accounted["value"] += time.perf_counter() - started

    def wrapped_dump(
        self: YAML,
        data: Any,
        stream: Any = None,
        *,
        transform: Any = None,
    ) -> Any:
        started = time.perf_counter()
        try:
            rendered = real_dump(self, data, stream, transform=transform)
        except core_format._NeedsPureEmitter:
            # The C emitter stopped before writing a value it can't match,
            # and the pure emitter writes it next. That is not a C error.
            raise
        except Exception:
            if _engine_is_c(self.Emitter, CEmitter):
                state["c_dump_error"] = True
            else:
                state["pure_dump"] = True
            raise
        else:
            if _engine_is_c(self.Emitter, CEmitter):
                state["c_dump"] = True
            else:
                state["pure_dump"] = True
            return rendered
        finally:
            dump_accounted["value"] += time.perf_counter() - started

    YAML.scan = wrapped_scan  # type: ignore[method-assign]
    YAML.load = wrapped_load  # type: ignore[method-assign]
    YAML.dump = wrapped_dump  # type: ignore[method-assign]
    try:
        started = time.perf_counter()
        result = core_format.format_yaml(text)
        total = time.perf_counter() - started
    finally:
        YAML.scan = real_scan  # type: ignore[method-assign]
        YAML.load = real_load  # type: ignore[method-assign]
        YAML.dump = real_dump  # type: ignore[method-assign]
    if load_accounted["value"] <= 0.0 and dump_accounted["value"] <= 0.0:
        message = "format_yaml did not call YAML.load or YAML.dump."
        raise RuntimeError(message)
    _observed_path[0] = classify_observed_path(state)
    accounted = load_accounted["value"] + dump_accounted["value"]
    if accounted > total > 0.0:
        scale = total / accounted
        load_s = load_accounted["value"] * scale
        dump_s = dump_accounted["value"] * scale
    else:
        load_s = load_accounted["value"] + max(0.0, total - accounted)
        dump_s = dump_accounted["value"]
    return result, total, load_s, dump_s


def _yaml_phases(text: str, *, typ: str, require_c: bool) -> PhaseResult:
    started = time.perf_counter()
    if require_c:
        _require_yaml_clib()
        yaml = YAML(typ="safe", pure=False)
        if yaml.Parser is not CParser or yaml.Emitter is not CEmitter:
            message = "safe mode did not select the C parser and emitter."
            raise RuntimeError(message)
    else:
        yaml = YAML(typ=typ)
    yaml.default_flow_style = False
    parsed = yaml.load(text)
    loaded = time.perf_counter()
    buffer = StringIO()
    yaml.dump(parsed, buffer)
    dumped = time.perf_counter()
    return buffer.getvalue(), dumped - started, loaded - started, dumped - loaded


def _round_trip_phases(text: str) -> PhaseResult:
    return _yaml_phases(text, typ="rt", require_c=False)


def _safe_c_phases(text: str) -> PhaseResult:
    return _yaml_phases(text, typ="safe", require_c=True)


def format_yaml_safe_c(text: str) -> str:
    """Load and dump YAML with ruamel safe mode and the C extension.

    ``YAML(typ="safe")`` selects ``CParser`` and ``CEmitter`` when
    ``ruamel.yaml.clib`` is installed. Round-trip mode never uses that
    extension. This function is benchmark-only.
    """
    result, _total, _load, _dump = _safe_c_phases(text)
    if not isinstance(result, str):
        message = "YAML dump did not return text."
        raise RuntimeError(message)
    return result


def format_yaml_round_trip(text: str) -> str:
    """Load and dump YAML with ruamel round-trip mode.

    ``YAML(typ="rt")`` is the default ``YAML()`` and keeps comments and key
    order. The benchmark owns this path so ``galley.core.format_yaml`` can stay
    on ``typ="safe"``.
    """
    result, _total, _load, _dump = _round_trip_phases(text)
    if not isinstance(result, str):
        message = "YAML dump did not return text."
        raise RuntimeError(message)
    return result


def _record(index: int) -> dict[str, Any]:
    return {
        "id": index,
        "name": f"widget-{index:06d}",
        "sku": f"SKU-{index:08d}",
        "active": index % 2 == 0,
        "price": round(9.99 + (index % 97) * 0.25, 2),
        "quantity": index % 50,
        "tags": [
            "catalog",
            "sale" if index % 3 == 0 else "stock",
            f"bin-{(index % 12):02d}",
        ],
        "location": {
            "warehouse": f"wh-{(index % 7) + 1}",
            "aisle": (index % 40) + 1,
            "bin": f"{(index % 26) + 1:02d}-{(index % 9) + 1}",
        },
        "notes": "Packed for regional shipment with standard handling.",
    }


def _json_item(index: int) -> str:
    payload = json.dumps(_record(index), indent=2, ensure_ascii=True)
    return "\n".join(f"    {line}" for line in payload.split("\n"))


def _xml_item(index: int) -> str:
    record = _record(index)
    location = record["location"]
    active = "true" if record["active"] else "false"
    tags = "".join(f"<tag>{tag}</tag>" for tag in record["tags"])
    return (
        f'  <item id="{record["id"]}" active="{active}">\n'
        f"    <name>{record['name']}</name>\n"
        f"    <sku>{record['sku']}</sku>\n"
        f'    <price currency="USD">{record["price"]:.2f}</price>\n'
        f"    <quantity>{record['quantity']}</quantity>\n"
        f"    <tags>{tags}</tags>\n"
        f'    <location warehouse="{location["warehouse"]}" '
        f'aisle="{location["aisle"]}" bin="{location["bin"]}"/>\n'
        f"    <notes>{record['notes']}</notes>\n"
        "  </item>\n"
    )


def render_xml_catalog(target_bytes: int) -> str:
    """Return one plain-catalog XML document of exactly ``target_bytes`` bytes."""
    with tempfile.TemporaryDirectory(prefix="galley-xml-catalog-") as temporary:
        path = Path(temporary) / "catalog.xml"
        _write_document(
            path,
            target_bytes,
            header=XML_HEADER,
            separator=XML_SEPARATOR,
            footer_prefix=XML_FOOTER_PREFIX,
            footer_suffix=XML_FOOTER_SUFFIX,
            render_item=_xml_item,
        )
        return path.read_text(encoding="ascii")


def _html_item(index: int) -> str:
    record = _record(index)
    location = record["location"]
    tags = "".join(f"<li>{tag}" for tag in record["tags"])
    return (
        f'<article id="item-{record["id"]}" data-sku="{record["sku"]}">\n'
        f"  <h2>{record['name']}</h2>\n"
        f"  <p>Price <b>{record['price']:.2f}</b> USD, "
        f"<i>{record['quantity']}</i> in stock.</p>\n"
        f"  <ul>{tags}</ul>\n"
        f"  <!-- {location['warehouse']} aisle {location['aisle']} -->\n"
        f"  <p>{record['notes']}</p>\n"
        "</article>\n"
    )


def render_html_catalog(target_bytes: int) -> str:
    """Return one plain-catalog HTML page of exactly ``target_bytes`` bytes."""
    with tempfile.TemporaryDirectory(prefix="galley-html-catalog-") as temporary:
        path = Path(temporary) / "catalog.html"
        _write_document(
            path,
            target_bytes,
            header=HTML_HEADER,
            separator=HTML_SEPARATOR,
            footer_prefix=HTML_FOOTER_PREFIX,
            footer_suffix=HTML_FOOTER_SUFFIX,
            render_item=_html_item,
        )
        return path.read_text(encoding="ascii")


def _python_item(index: int) -> str:
    return f"    {_record(index)!r}"


def _yaml_item(index: int) -> str:
    record = _record(index)
    location = record["location"]
    tags = record["tags"]
    active = "true" if record["active"] else "false"
    lines = [
        f"- id: {record['id']}",
        f"  name: {json.dumps(record['name'])}",
        f"  sku: {json.dumps(record['sku'])}",
        f"  active: {active}",
        f"  price: {record['price']:.2f}",
        f"  quantity: {record['quantity']}",
        "  tags:",
        *(f"  - {json.dumps(tag)}" for tag in tags),
        "  location:",
        f"    warehouse: {json.dumps(location['warehouse'])}",
        f"    aisle: {location['aisle']}",
        f"    bin: {json.dumps(location['bin'])}",
        f"  notes: {json.dumps(record['notes'])}",
    ]
    return "\n".join(lines) + "\n"


_REALISTIC_SERVICES = ("checkout", "refunds", "ledger", "notifications", "tax")
_REALISTIC_SCRIPTS = (
    "set -euo pipefail\necho starting checkout\npython -m app.migrate\npython -m app",
    "set -eu\nkubectl rollout status deploy/checkout\nkubectl get pods",
    "python -m pytest -q\npython -m app.healthcheck",
)
_REALISTIC_FOLDED = (
    "Public checkout API for the regional\npayments cluster.",
    "Background worker that settles\ncaptured payments.",
    "CI job that builds and publishes\nthe service image.",
)
_ACCENTED_TITLES = (
    "café naïve",
    "São Paulo",
)
_CJK = (
    "東京",
    "订单处理",
)
_EMOJI = (
    "🚀",
    "😀",
)
# Indented inside a block scalar. This is scalar content, not a directive.
_EMBEDDED_DIRECTIVE = (
    "%YAML 1.2\n---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: embedded\n"
)
REALISTIC_HEADER = (
    f"# galley size bench, realistic corpus, seed {REALISTIC_SEED}\n"
    "# trailing padding is ASCII x so the file is an exact size\n"
    "apiVersion: v1\n"
    "kind: List\n"
    "metadata:\n"
    "  name: galley-size-bench\n"
    "items:\n"
)
# Rule-1 trigger for the forced reparse row and the directive-only row.
# Parsed items stay the same.
REALISTIC_DIRECTIVE = "%YAML 1.2\n---\n"


@dataclass(frozen=True)
class _RealisticMix:
    """Which chosen trigger features a realistic file includes.

    ``inject`` adds one trigger in the header. Item flags are the natural
    rates. The clean mix leaves every flag off and injects nothing.
    """

    bare_tag: bool = False
    quoted_tab: bool = False
    embedded_directive_text: bool = False
    inject: str | None = None


_NATURAL_MIX = _RealisticMix(
    bare_tag=True,
    quoted_tab=True,
    embedded_directive_text=True,
)
_CLEAN_MIX = _RealisticMix()
_REALISTIC_MIXES = {
    "natural": _NATURAL_MIX,
    "reparse": _RealisticMix(
        bare_tag=True,
        quoted_tab=True,
        embedded_directive_text=True,
        inject="directive",
    ),
    "clean": _CLEAN_MIX,
    "bare_tag": _RealisticMix(inject="bare_tag"),
    "quoted_tab": _RealisticMix(inject="quoted_tab"),
    "directive": _RealisticMix(inject="directive"),
}
_CORPUS_PROFILE = {
    "realistic": "natural",
    "realistic-reparse": "reparse",
    "realistic-clean": "clean",
    "realistic-only-bare-tag": "bare_tag",
    "realistic-only-tab": "quoted_tab",
    "realistic-only-directive": "directive",
}


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _block_lines(key: str, style: str, body: str) -> list[str]:
    lines = [f"  {key}: {style}"]
    for line in body.strip("\n").split("\n"):
        lines.append(f"    {line}" if line else "")
    return lines


def _deployment_lines(
    name: str,
    service: str,
    replicas: int,
    port: int,
    tag: str,
) -> list[str]:
    image = _yaml_quote(f"ghcr.io/example/{service}:{tag}")
    return [
        "- apiVersion: apps/v1",
        "  kind: Deployment",
        "  metadata:",
        f"    name: {name}",
        "    namespace: payments",
        "    labels:",
        f"      app: {service}",
        "      tier: api",
        "  spec:",
        f"    replicas: {replicas}",
        "    selector:",
        "      matchLabels:",
        f"        app: {service}",
        "    template:",
        "      metadata:",
        "        labels:",
        f"          app: {service}",
        "      spec:",
        "        containers:",
        "          - name: app",
        f"            image: {image}",
        "            ports:",
        "              - name: http",
        f"                containerPort: {port}",
        "            env:",
        "              - name: LOG_LEVEL",
        "                value: info",
        "              - name: SERVICE",
        f"                value: {service}",
        "            resources:",
        "              requests:",
        "                cpu: 100m",
        "                memory: 128Mi",
        "              limits:",
        "                cpu: 500m",
        "                memory: 512Mi",
    ]


def _config_lines(name: str, service: str, port: int, burst: int) -> list[str]:
    listen = _yaml_quote(f"0.0.0.0:{port}")
    return [
        "- apiVersion: v1",
        "  kind: ConfigMap",
        "  metadata:",
        f"    name: {name}",
        "    namespace: payments",
        "  data:",
        f"    service: {service}",
        f"    listen: {listen}",
        "    features:",
        "      refunds: true",
        "      tax: false",
        "    limits:",
        "      requests_per_minute: 1200",
        f"      burst: {burst}",
        "    owners:",
        "      - platform",
        "      - payments",
    ]


def _ci_lines(name: str, service: str, tag: str) -> list[str]:
    return [
        f"- name: {name}",
        "  runs-on: ubuntu-latest",
        "  timeout-minutes: 30",
        "  env:",
        f"    SERVICE: {service}",
        f"    IMAGE_TAG: {_yaml_quote(tag)}",
        "  steps:",
        "    - name: Checkout",
        "      uses: actions/checkout@v4",
        "    - name: Setup",
        "      uses: astral-sh/setup-uv@v5",
        "    - name: Test",
        "      run: python -m pytest",
        "  services:",
        "    - name: postgres",
        '      image: "postgres:16"',
        "      env:",
        "        POSTGRES_DB: app",
    ]


def _service_lines(name: str, service: str, port: int) -> list[str]:
    return [
        "- apiVersion: v1",
        "  kind: Service",
        "  metadata:",
        f"    name: {name}",
        "    namespace: payments",
        "    labels:",
        f"      app: {service}",
        "  spec:",
        "    type: ClusterIP",
        "    selector:",
        f"      app: {service}",
        "    ports:",
        "      - name: http",
        f"        port: {port}",
        "        targetPort: http",
        "        protocol: TCP",
    ]


def _realistic_header(mix: _RealisticMix) -> str:
    """Document header, including one optional trigger injection."""
    extra = ""
    if mix.inject == "bare_tag":
        extra = "  channel: ! stable\n"
    elif mix.inject == "quoted_tab":
        extra = f"  column: {_yaml_quote('name\tregion')}\n"
    header = REALISTIC_HEADER
    if extra:
        header = header.replace("items:\n", extra + "items:\n", 1)
    if mix.inject == "directive":
        header = REALISTIC_DIRECTIVE + header
    return header


def _realistic_item(index: int, mix: _RealisticMix | None = None) -> str:
    """One seeded config-shaped list item. See the module docstring for the mix."""
    if mix is None:
        mix = _NATURAL_MIX
    rng = random.Random(REALISTIC_SEED + index)
    service = _REALISTIC_SERVICES[index % len(_REALISTIC_SERVICES)]
    name = f"{service}-{index:06d}"
    replicas = 1 + rng.randrange(5)
    port = 8000 + (index % 200)
    tag = f"1.{index % 20}.{rng.randrange(10)}"
    kind = index % 4
    if kind == 0:
        lines = _deployment_lines(name, service, replicas, port, tag)
    elif kind == 1:
        lines = _config_lines(name, service, port, 20 + rng.randrange(80))
    elif kind == 2:
        lines = _ci_lines(name, service, tag)
    else:
        lines = _service_lines(name, service, port)
    lines.insert(1, f"  # record {index} for the size bench")
    if index % 6 == 5:
        lines.insert(2, "  # équipe")
    embedded = mix.embedded_directive_text and index % 17 == 4
    if index % 5 != 4 or embedded:
        if embedded:
            lines.extend(_block_lines("script", "|", _EMBEDDED_DIRECTIVE))
        elif index % 2 == 0:
            script = _REALISTIC_SCRIPTS[index % len(_REALISTIC_SCRIPTS)]
            lines.extend(_block_lines("script", "|", script))
        else:
            folded = _REALISTIC_FOLDED[index % len(_REALISTIC_FOLDED)]
            lines.extend(_block_lines("summary", ">", folded))
    if index % 4 != 3:
        text = _ACCENTED_TITLES[index % len(_ACCENTED_TITLES)]
        lines.append(f"  title: {_yaml_quote(text)}")
    if index % 20 == 17:
        lines.append(f"  region: {_yaml_quote(_CJK[index % len(_CJK)])}")
    if index % 12 == 7:
        lines.append(f"  badge: {_yaml_quote(_EMOJI[index % len(_EMOJI)])}")
    if index % 5 == 0:
        windows = "C:\\Program Files\\Galley\\" + service
        lines.append(f"  windows_path: {_yaml_quote(windows)}")
    if index % 13 == 5:
        lines.append("  payload: !!binary aGVsbG8=")
    if mix.bare_tag and index % 15 == 11:
        lines.append("  channel: ! stable")
    if mix.quoted_tab and index % 16 == 13:
        lines.append(f"  column: {_yaml_quote('name\tregion')}")
    return "\n".join(lines) + "\n"


def _item_triggers(item: str) -> dict[str, bool]:
    """Which realistic triggers this item text contains."""
    block = any(line.endswith(("|", ">")) for line in item.splitlines())
    return {
        "block_scalar": block,
        "accented": any(text in item for text in (*_ACCENTED_TITLES, "équipe")),
        "emoji": any(text in item for text in _EMOJI),
        "cjk": any(text in item for text in _CJK),
        "binary": "!!binary" in item,
        "yaml_directive_text": "%YAML" in item,
        "bare_tag": ": ! " in item,
        "backslash": "\\" in item,
        "quoted_tab": "\t" in item,
    }


def realistic_trigger_counts(item_count: int) -> dict[str, int]:
    """Count trigger kinds across the first ``item_count`` realistic items."""
    names = (
        "block_scalar",
        "accented",
        "emoji",
        "cjk",
        "binary",
        "yaml_directive_text",
        "bare_tag",
        "backslash",
        "quoted_tab",
    )
    counts = dict.fromkeys(names, 0)
    for index in range(item_count):
        flags = _item_triggers(_realistic_item(index))
        for name in names:
            if flags[name]:
                counts[name] += 1
    counts["items"] = item_count
    return counts


def _write_document(
    path: Path,
    target_bytes: int,
    *,
    header: str,
    separator: str,
    footer_prefix: str,
    footer_suffix: str,
    render_item: Callable[[int], str],
    encoding: str = "ascii",
) -> int:
    def nbytes(text: str) -> int:
        return len(text.encode(encoding))

    footer_overhead = nbytes(footer_prefix) + nbytes(footer_suffix)
    count = 0
    with path.open("w", encoding=encoding, newline="\n") as handle:
        handle.write(header)
        written = nbytes(header)
        index = 0
        while True:
            item = render_item(index)
            prefix = separator if count else ""
            projected = written + nbytes(prefix) + nbytes(item) + footer_overhead
            if projected > target_bytes:
                break
            handle.write(prefix)
            handle.write(item)
            written = projected - footer_overhead
            count += 1
            index += 1
        if count == 0:
            message = f"Target of {target_bytes} bytes is smaller than one record."
            raise RuntimeError(message)
        pad_len = target_bytes - written - footer_overhead
        if pad_len < 0:
            raise RuntimeError("Padding calculation under-ran the target.")
        handle.write(footer_prefix)
        handle.write("x" * pad_len)
        handle.write(footer_suffix)
    actual = path.stat().st_size
    if actual != target_bytes:
        message = f"Generated file is {actual} bytes; expected {target_bytes}."
        raise RuntimeError(message)
    return count


def ruamel_environment() -> dict[str, Any]:
    """How this process loads YAML. No filesystem paths."""
    try:
        clib_version = importlib.metadata.version("ruamel.yaml.clib")
    except importlib.metadata.PackageNotFoundError:
        clib_version = None
    return {
        "core_format_yaml_typ": "safe",
        "round_trip_typ": "rt",
        "round_trip_pure_python": True,
        "clib_distribution": "ruamel.yaml.clib",
        "clib_installed": clib_version is not None,
        "clib_version": clib_version,
        "c_parser": CParser is not None,
        "c_emitter": CEmitter is not None,
    }


def _default_sizes(operation: Operation) -> tuple[float, ...]:
    if operation.operation_id in _TRIGGER_OPERATION_IDS:
        return DEFAULT_TRIGGER_SIZES_MB
    if operation.operation_id in _NEW_OPERATION_IDS:
        return DEFAULT_REALISTIC_SIZES_MB
    if operation.source_format == "python":
        return DEFAULT_PYTHON_SIZES_MB
    if operation.source_format == "xml":
        return DEFAULT_XML_SIZES_MB
    if operation.source_format == "html":
        return DEFAULT_HTML_SIZES_MB
    if operation.source_format == "json":
        return DEFAULT_JSON_SIZES_MB
    if operation.yaml_typ == "safe-c":
        return DEFAULT_SAFE_C_SIZES_MB
    if operation.source_format == "yaml":
        return DEFAULT_YAML_SIZES_MB
    return DEFAULT_JSON_SIZES_MB


def _operation_selected(operation: Operation, corpus: str) -> bool:
    if corpus == "all":
        return True
    if corpus == "plain":
        return operation.operation_id not in _NEW_OPERATION_IDS
    if corpus == "realistic":
        return operation.operation_id in _NEW_OPERATION_IDS
    message = f"Unknown corpus {corpus}."
    raise RuntimeError(message)


def _sizes_for_operation(
    operation: Operation,
    requested: list[float] | None,
) -> list[float]:
    """Sizes to time for one operation.

    With no ``--sizes``, each operation uses its default ladder. An explicit
    list is used for every operation, including sizes above those defaults,
    sorted so a larger file is skipped after a smaller one runs out of memory.
    """
    if requested is None:
        return [float(size) for size in _default_sizes(operation)]
    return sorted(float(size) for size in requested)


def _format_mb(size_mb: float) -> str:
    return f"{float(size_mb):.4f}".rstrip("0").rstrip(".")


def _target_bytes(size_mb: float) -> int:
    return int(round(float(size_mb) * BYTES_PER_MB))


def generate_files(
    directory: Path,
    source_format: str,
    sizes_mb: list[float],
    *,
    corpus: str = "plain",
) -> dict[float, tuple[Path, int]]:
    encoding = "ascii"
    if source_format == "json":
        suffix = ".json"
        header = JSON_HEADER
        separator = JSON_SEPARATOR
        footer_prefix = JSON_FOOTER_PREFIX
        footer_suffix = JSON_FOOTER_SUFFIX
        render_item = _json_item
    elif source_format == "yaml" and corpus in _CORPUS_PROFILE:
        suffix = ".yaml"
        mix = _REALISTIC_MIXES[_CORPUS_PROFILE[corpus]]
        header = _realistic_header(mix)
        separator = YAML_SEPARATOR
        footer_prefix = YAML_FOOTER_PREFIX
        footer_suffix = YAML_FOOTER_SUFFIX
        render_item = _render_realistic_item(mix)
        encoding = "utf-8"
    elif source_format == "yaml":
        suffix = ".yaml"
        header = YAML_HEADER
        separator = YAML_SEPARATOR
        footer_prefix = YAML_FOOTER_PREFIX
        footer_suffix = YAML_FOOTER_SUFFIX
        render_item = _yaml_item
    elif source_format == "xml":
        suffix = ".xml"
        header = XML_HEADER
        separator = XML_SEPARATOR
        footer_prefix = XML_FOOTER_PREFIX
        footer_suffix = XML_FOOTER_SUFFIX
        render_item = _xml_item
    elif source_format == "html":
        suffix = ".html"
        header = HTML_HEADER
        separator = HTML_SEPARATOR
        footer_prefix = HTML_FOOTER_PREFIX
        footer_suffix = HTML_FOOTER_SUFFIX
        render_item = _html_item
    elif source_format == "python":
        suffix = ".py"
        header = PYTHON_HEADER
        separator = PYTHON_SEPARATOR
        footer_prefix = PYTHON_FOOTER_PREFIX
        footer_suffix = PYTHON_FOOTER_SUFFIX
        render_item = _python_item
    else:
        message = f"No synthetic generator for {source_format}."
        raise RuntimeError(message)

    written: dict[float, tuple[Path, int]] = {}
    for size_mb in sizes_mb:
        path = directory / f"{_format_mb(size_mb)}mb{suffix}"
        label = (
            f"{source_format}/{corpus}" if source_format == "yaml" else source_format
        )
        print(f"generating {label} {_format_mb(size_mb)} MB", file=sys.stderr)
        count = _write_document(
            path,
            _target_bytes(size_mb),
            header=header,
            separator=separator,
            footer_prefix=footer_prefix,
            footer_suffix=footer_suffix,
            render_item=render_item,
            encoding=encoding,
        )
        written[size_mb] = (path, count)
    _validate_sample(written[min(written)][0], source_format, encoding=encoding)
    return written


def _render_realistic_item(mix: _RealisticMix) -> Callable[[int], str]:
    def render(index: int) -> str:
        return _realistic_item(index, mix)

    return render


def render_realistic_yaml(
    target_bytes: int,
    *,
    profile: str = "natural",
    directive: bool = False,
) -> str:
    """Return one realistic document of exactly ``target_bytes`` UTF-8 bytes.

    ``profile`` selects the mix in ``_REALISTIC_MIXES``. ``directive`` is the
    natural mix plus a leading ``%YAML 1.2`` directive. List items follow that
    mix either way.
    """
    if directive:
        profile = "reparse"
    mix = _REALISTIC_MIXES[profile]
    with tempfile.TemporaryDirectory(prefix="galley-realistic-yaml-") as temporary:
        path = Path(temporary) / "realistic.yaml"
        _write_document(
            path,
            target_bytes,
            header=_realistic_header(mix),
            separator=YAML_SEPARATOR,
            footer_prefix=YAML_FOOTER_PREFIX,
            footer_suffix=YAML_FOOTER_SUFFIX,
            render_item=_render_realistic_item(mix),
            encoding="utf-8",
        )
        return path.read_text(encoding="utf-8")


def render_python_literal(target_bytes: int) -> str:
    """Return one plain-catalog Python literal of exactly ``target_bytes`` bytes."""
    with tempfile.TemporaryDirectory(prefix="galley-python-literal-") as temporary:
        path = Path(temporary) / "catalog.py"
        _write_document(
            path,
            target_bytes,
            header=PYTHON_HEADER,
            separator=PYTHON_SEPARATOR,
            footer_prefix=PYTHON_FOOTER_PREFIX,
            footer_suffix=PYTHON_FOOTER_SUFFIX,
            render_item=_python_item,
        )
        return path.read_text(encoding="ascii")


def _validate_sample(
    path: Path, source_format: str, *, encoding: str = "ascii"
) -> None:
    text = path.read_text(encoding=encoding)
    if source_format == "xml":
        validate_xml(text)
        if "<item " not in text:
            raise RuntimeError("Synthetic xml document did not parse.")
        return
    if source_format == "html":
        format_html(text)
        if "<article " not in text:
            raise RuntimeError("Synthetic html document did not parse.")
        return
    if source_format == "json":
        parsed = json.loads(text)
    elif source_format == "python":
        parsed = ast.literal_eval(text)
    else:
        parsed = YAML(typ="safe").load(text)
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items:
        message = f"Synthetic {source_format} document did not parse."
        raise RuntimeError(message)
    del parsed, text


def current_rss_bytes() -> int | None:
    """Resident set, or None when this platform cannot report it."""
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    if not text:
        return None
    try:
        return int(text.split()[0]) * 1024
    except (ValueError, IndexError):
        return None


def process_ru_maxrss_bytes() -> int | None:
    """Process high-water RSS, or None where ``resource`` is unavailable.

    Imported here so this module loads on Windows, which has no ``resource``.
    """
    try:
        import resource
    except ImportError:
        return None
    try:
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (AttributeError, OSError, ValueError):
        return None
    if sys.platform == "darwin":
        return int(raw)
    return int(raw) * 1024


class RssSampler:
    def __init__(self) -> None:
        self.peak_bytes = current_rss_bytes()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._sample,
            name="galley-rss-sampler",
            daemon=True,
        )

    def _observe(self) -> None:
        rss = current_rss_bytes()
        if rss is None:
            return
        if self.peak_bytes is None or rss > self.peak_bytes:
            self.peak_bytes = rss

    def _sample(self) -> None:
        while not self._stop.wait(SAMPLE_INTERVAL_S):
            self._observe()

    def start(self) -> None:
        self._observe()
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._observe()
        return self.peak_bytes


def release_memory() -> None:
    gc.collect()
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        return
    trim = getattr(libc, "malloc_trim", None)
    if trim is None:
        return
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    trim(0)


def _touch(result: object) -> None:
    if isinstance(result, (str, bytes, bytearray)):
        if result:
            result[-1]
        return
    repr(type(result))


def _time_once(
    operation: Operation, text: str
) -> tuple[float, float, float, int | None, str | None]:
    _observed_path[0] = None
    sampler = RssSampler()
    sampler.start()
    try:
        result, elapsed, load_s, dump_s = operation.phases(text)
        path = _observed_path[0]
        if path is None:
            path = operation.reported_path
        _touch(result)
        peak = sampler.stop()
    except Exception:
        sampler.stop()
        raise
    del result
    return elapsed, load_s, dump_s, peak, path


def _warmup(operation: Operation) -> None:
    samples = {
        "json": '{"warmup":1}\n',
        "python": "{'warmup': 1}\n",
        "xml": "<warmup/>\n",
        "html": "<p>warmup</p>\n",
    }
    operation.phases(samples.get(operation.source_format, "warmup: 1\n"))


def _measure_loaded(
    operation: Operation,
    text: str,
    size_mb: float,
    runs: int,
    item_count: int,
) -> dict[str, Any]:
    _warmup(operation)
    samples: list[dict[str, float | int | str | None]] = []
    before_rss = process_ru_maxrss_bytes()
    for run_index in range(runs):
        release_memory()
        elapsed, load_s, dump_s, peak, path = _time_once(operation, text)
        if operation.track_path and path is None:
            message = f"{operation.operation_id} did not record a YAML path."
            raise RuntimeError(message)
        if operation.require_path is not None and path != operation.require_path:
            observed = _PATH_LABELS.get(path or "", path or "unknown")
            expected = _PATH_LABELS[operation.require_path]
            message = (
                f"{operation.operation_id} at {_format_mb(size_mb)} MB "
                f"ran {observed}; expected {expected}."
            )
            raise RuntimeError(message)
        if operation.expect_path is not None and path != operation.expect_path:
            observed = _PATH_LABELS.get(path or "", path or "unknown")
            expected = _PATH_LABELS[operation.expect_path]
            path_note_mismatch = f", expected {expected}, observed {observed}"
        else:
            path_note_mismatch = ""
        sample: dict[str, float | int | str | None] = {
            "seconds": round(elapsed, 6),
            "load_s": round(load_s, 6),
            "dump_s": round(dump_s, 6),
            "peak_rss_bytes": peak,
        }
        path_note = ""
        if path is not None:
            sample["yaml_path"] = path
            path_note = f", path {_PATH_LABELS.get(path, path)}"
        samples.append(sample)
        print(
            f"{operation.operation_id} {_format_mb(size_mb)} MB "
            f"run {run_index + 1}/{runs}: {elapsed:.4f} s "
            f"(load {load_s:.4f} s, dump {dump_s:.4f} s)"
            f"{path_note}{path_note_mismatch}",
            file=sys.stderr,
        )
    after_rss = process_ru_maxrss_bytes()
    seconds = [float(sample["seconds"]) for sample in samples]
    load_seconds = [float(sample["load_s"]) for sample in samples]
    dump_seconds = [float(sample["dump_s"]) for sample in samples]
    peaks = [
        int(sample["peak_rss_bytes"])
        for sample in samples
        if sample["peak_rss_bytes"] is not None
    ]
    peak_bytes: int | None = max(peaks) if peaks else None
    if after_rss is not None and before_rss is not None and after_rss > before_rss:
        peak_bytes = after_rss if peak_bytes is None else max(peak_bytes, after_rss)
    row = {
        "operation_id": operation.operation_id,
        "group": operation.group,
        "source_format": operation.source_format,
        "size_mb": size_mb,
        "size_bytes": _target_bytes(size_mb),
        "item_count": item_count,
        "runs": samples,
        "median_s": round(statistics.median(seconds), 6),
        "median_load_s": round(statistics.median(load_seconds), 6),
        "median_dump_s": round(statistics.median(dump_seconds), 6),
        "min_s": round(min(seconds), 6),
        "max_s": round(max(seconds), 6),
        "peak_rss_bytes": peak_bytes,
        "process_ru_maxrss_bytes": after_rss,
        **_operation_meta(operation),
    }
    paths = [
        str(sample["yaml_path"])
        for sample in samples
        if sample.get("yaml_path") is not None
    ]
    if paths:
        unique = list(dict.fromkeys(paths))
        if len(unique) == 1:
            row["yaml_path"] = unique[0]
            row["yaml_path_label"] = _PATH_LABELS.get(unique[0], unique[0])
        else:
            row["yaml_path"] = "mixed"
            row["yaml_path_label"] = ", ".join(
                _PATH_LABELS.get(item, item) for item in unique
            )
    if operation.expect_path is not None:
        row["path_expected"] = operation.expect_path
        row["path_mismatch"] = row.get("yaml_path") != operation.expect_path
    return row


def _operation_meta(operation: Operation) -> dict[str, str | None]:
    return {
        "yaml_typ": operation.yaml_typ,
        "cutoff_role": operation.cutoff_role,
    }


def _error_row(
    operation: Operation,
    size_mb: float,
    item_count: int,
    error: str,
) -> dict[str, Any]:
    return {
        "operation_id": operation.operation_id,
        "group": operation.group,
        "source_format": operation.source_format,
        "size_mb": size_mb,
        "size_bytes": _target_bytes(size_mb),
        "item_count": item_count,
        "error": error,
        **_operation_meta(operation),
    }


def _uv_executable() -> str:
    uv = shutil.which("uv")
    if uv is None:
        message = "uv is required to launch the YAML benchmark workers."
        raise RuntimeError(message)
    return uv


def _venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _pure_interpreter() -> str:
    """Interpreter whose project env does not install ``ruamel.yaml.clib``."""
    global _PURE_PYTHON
    if _PURE_PYTHON is not None:
        return _PURE_PYTHON
    print(
        "preparing pure-Python environment without ruamel.yaml.clib",
        file=sys.stderr,
    )
    uv = _uv_executable()
    root = Path(tempfile.mkdtemp(prefix="galley-size-bench-pure-"))
    atexit.register(shutil.rmtree, root, True)
    venv = root / ".venv"
    subprocess.run([uv, "venv", "--quiet", str(venv)], check=True)
    env = os.environ.copy()
    # The parent `uv run` sets VIRTUAL_ENV to the project env, which still
    # has the C extension. Point this sync at the pure env only.
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    subprocess.run(
        [
            uv,
            "sync",
            "--quiet",
            "--frozen",
            "--no-dev",
            "--no-install-package",
            "ruamel.yaml.clib",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    python = _venv_python(venv)
    if not python.is_file():
        message = "Pure-Python benchmark environment has no interpreter."
        raise RuntimeError(message)
    _PURE_PYTHON = str(python)
    return _PURE_PYTHON


def _shipped_interpreter() -> str:
    """Interpreter synced once from the lockfile, including ``ruamel.yaml.clib``.

    ``uv run --isolated`` builds this same environment (default groups, editable
    project, lockfile packages) and then throws it away. Doing that for every
    size put environment setup next to the timed call. This sync runs once,
    before any size is timed, and each size reuses the interpreter.
    """
    global _SHIPPED_PYTHON
    if _SHIPPED_PYTHON is not None:
        return _SHIPPED_PYTHON
    print(
        "preparing shipped environment from the project lock",
        file=sys.stderr,
    )
    uv = _uv_executable()
    root = Path(tempfile.mkdtemp(prefix="galley-size-bench-shipped-"))
    atexit.register(shutil.rmtree, root, True)
    venv = root / ".venv"
    subprocess.run([uv, "venv", "--quiet", str(venv)], check=True)
    env = os.environ.copy()
    # The parent `uv run` sets VIRTUAL_ENV to the project env, which may not
    # match the lock (CI can omit ruamel.yaml.clib). Point this sync at the
    # shipped env only.
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    subprocess.run(
        [uv, "sync", "--quiet", "--frozen"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    python = _venv_python(venv)
    if not python.is_file():
        message = "Shipped benchmark environment has no interpreter."
        raise RuntimeError(message)
    _SHIPPED_PYTHON = str(python)
    return _SHIPPED_PYTHON


def _clib_worker_prefix() -> list[str]:
    return [_uv_executable(), "run", "--with", RUAMEL_CLIB_SPEC, "--"]


def _worker_command(
    operation: Operation,
    path: Path,
    size_mb: float,
    runs: int,
    item_count: int,
) -> list[str]:
    """Command for one size.

    Shipped ``format_yaml`` reuses one interpreter synced from the project
    lock, including ``ruamel.yaml.clib`` when that dependency is present.
    That is the environment ``uv run --isolated`` would install, prepared
    once before timing. Safe+C and the shipped C-path rows use
    ``uv run --with`` at the pinned version. The realistic pure row uses an
    environment synced without ``ruamel.yaml.clib`` and asserts it is absent.
    """
    script = str(Path(__file__).resolve())
    worker = [
        "python",
        script,
        "--worker",
        "--operation",
        operation.operation_id,
        "--input-file",
        str(path),
        "--size-mb",
        _format_mb(size_mb),
        "--runs",
        str(runs),
        "--item-count",
        str(item_count),
    ]
    if operation.operation_id == "format_yaml":
        return [_shipped_interpreter(), *worker[1:]]
    if operation.operation_id in _CLIB_WORKER_IDS:
        return [*_clib_worker_prefix(), *worker]
    if operation.operation_id in _PURE_OPERATION_IDS:
        return [_pure_interpreter(), *worker[1:]]
    return [sys.executable, *worker[1:]]


def _worker_env(operation: Operation) -> dict[str, str] | None:
    if operation.operation_id not in _PURE_OPERATION_IDS:
        return None
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _measure_in_subprocess(
    operation: Operation,
    path: Path,
    size_mb: float,
    runs: int,
    item_count: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        _worker_command(operation, path, size_mb, runs, item_count),
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        text=True,
        env=_worker_env(operation),
    )
    payload: dict[str, Any] | None = None
    if completed.stdout.strip():
        try:
            parsed = json.loads(completed.stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    if completed.returncode == 0 and payload and "median_s" in payload:
        return payload
    signal = -completed.returncode if completed.returncode < 0 else None
    out_of_memory = completed.returncode == 2 or signal == 9
    if payload and payload.get("error") and not out_of_memory:
        if completed.returncode != 0:
            out_of_memory = payload.get("error") == "MemoryError"
    if out_of_memory:
        reason = "out of memory"
        if signal is not None:
            reason = f"process killed by signal {signal}"
        elif payload and payload.get("error"):
            reason = str(payload["error"])
        return _error_row(operation, size_mb, item_count, reason)
    detail = ""
    if payload and payload.get("error"):
        detail = f": {payload['error']}"
    message = (
        f"{operation.operation_id} {_format_mb(size_mb)} MB "
        f"worker exited {completed.returncode}{detail}"
    )
    raise RuntimeError(message)


def measure_operation(
    operation: Operation,
    files: dict[float, tuple[Path, int]],
    runs: int,
    on_row: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    blocked = False
    for size_mb, (path, item_count) in files.items():
        if blocked:
            row = _error_row(
                operation,
                size_mb,
                item_count,
                "skipped after a smaller size ran out of memory",
            )
        else:
            print(
                f"timing {operation.operation_id} {_format_mb(size_mb)} MB",
                file=sys.stderr,
            )
            row = _measure_in_subprocess(operation, path, size_mb, runs, item_count)
            if row.get("error"):
                blocked = True
        rows.append(row)
        if on_row is not None:
            on_row(row)
    return rows


def run_worker(
    operation_id: str,
    input_file: Path,
    size_mb: float,
    runs: int,
    item_count: int,
) -> int:
    operations, _skipped = discover_operations()
    matches = [item for item in operations if item.operation_id == operation_id]
    if not matches:
        print(json.dumps({"error": f"{operation_id} is not available"}))
        return 1
    try:
        text = input_file.read_text(encoding="utf-8")
        row = _measure_loaded(matches[0], text, size_mb, runs, item_count)
    except MemoryError:
        print(json.dumps({"error": "MemoryError"}))
        return 2
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(row))
    return 0


def _linear_size(
    size_0: float,
    time_0: float,
    size_1: float,
    time_1: float,
    target_s: float,
) -> float:
    if time_1 == time_0:
        return math.nan
    span = (target_s - time_0) / (time_1 - time_0)
    return size_0 + (size_1 - size_0) * span


def _loglog_size(
    size_0: float,
    time_0: float,
    size_1: float,
    time_1: float,
    target_s: float,
) -> float:
    if min(size_0, size_1, time_0, time_1, target_s) <= 0:
        return math.nan
    exponent = math.log(time_1 / time_0) / math.log(size_1 / size_0)
    if not math.isfinite(exponent) or abs(exponent) < 1e-9:
        return math.nan
    size = size_0 * (target_s / time_0) ** (1.0 / exponent)
    if not math.isfinite(size) or size <= 0:
        return math.nan
    return size


def _cutoff_point(
    size_0: float,
    time_0: float,
    size_1: float,
    time_1: float,
    target_s: float,
    *,
    extrapolated: bool,
    method: str,
) -> dict[str, Any]:
    size = _loglog_size(size_0, time_0, size_1, time_1, target_s)
    used_method = method
    if math.isnan(size):
        size = _linear_size(size_0, time_0, size_1, time_1, target_s)
        used_method = "linear" if method == "log-log" else f"{method}-linear"
    if math.isnan(size) or size <= 0:
        return {
            "target_s": target_s,
            "size_mb": None,
            "size_bytes": None,
            "extrapolated": None,
            "method": "unavailable",
            "bracket_mb": [size_0, size_1],
            "bracket_s": [time_0, time_1],
        }
    return {
        "target_s": target_s,
        "size_mb": round(size, 4),
        "size_bytes": int(round(size * BYTES_PER_MB)),
        "extrapolated": extrapolated,
        "method": used_method,
        "bracket_mb": [size_0, size_1],
        "bracket_s": [round(time_0, 6), round(time_1, 6)],
    }


def _regression_cutoff(
    series: list[tuple[float, float]],
    target_s: float,
) -> dict[str, Any]:
    logs_s = [math.log(size) for size, _seconds in series]
    logs_t = [math.log(seconds) for _size, seconds in series]
    count = len(series)
    mean_s = sum(logs_s) / count
    mean_t = sum(logs_t) / count
    variance = sum((value - mean_s) ** 2 for value in logs_s)
    bracket = {
        "target_s": target_s,
        "size_mb": None,
        "size_bytes": None,
        "extrapolated": None,
        "method": "unavailable",
        "bracket_mb": [series[0][0], series[-1][0]],
        "bracket_s": [series[0][1], series[-1][1]],
    }
    if variance == 0 or target_s <= 0:
        return bracket
    covariance = sum(
        (logs_s[index] - mean_s) * (logs_t[index] - mean_t) for index in range(count)
    )
    exponent = covariance / variance
    if not math.isfinite(exponent) or abs(exponent) < 1e-9:
        return bracket
    intercept = mean_t - exponent * mean_s
    size = math.exp((math.log(target_s) - intercept) / exponent)
    if not math.isfinite(size) or size <= 0:
        return bracket
    return {
        "target_s": target_s,
        "size_mb": round(size, 4),
        "size_bytes": int(round(size * BYTES_PER_MB)),
        "extrapolated": size < series[0][0] or size > series[-1][0],
        "method": "log-log-regression",
        "bracket_mb": [series[0][0], series[-1][0]],
        "bracket_s": [round(series[0][1], 6), round(series[-1][1], 6)],
    }


def _unavailable_cutoff(target_s: float) -> dict[str, Any]:
    return {
        "target_s": target_s,
        "size_mb": None,
        "size_bytes": None,
        "summary": None,
        "extrapolated": None,
        "method": "unavailable",
        "bracket_mb": None,
        "bracket_s": None,
    }


def _outside_measured_cutoff(
    target_s: float,
    series: list[tuple[float, float]],
    *,
    relation: str,
    size_mb: float,
) -> dict[str, Any]:
    """Name the edge of the measured ladder instead of extrapolating past it."""
    summary = f"{relation} {_format_mb(size_mb)} MB"
    return {
        "target_s": target_s,
        "size_mb": None,
        "size_bytes": None,
        "summary": summary,
        "bound": relation,
        "bound_size_mb": size_mb,
        "extrapolated": False,
        "method": "outside measured sizes",
        "bracket_mb": [series[0][0], series[-1][0]],
        "bracket_s": [round(series[0][1], 6), round(series[-1][1], 6)],
    }


def _clamp_to_measured_range(
    point: dict[str, Any],
    series: list[tuple[float, float]],
    target_s: float,
) -> dict[str, Any]:
    size = point.get("size_mb")
    if size is None:
        return point
    smallest = series[0][0]
    largest = series[-1][0]
    if float(size) > largest:
        return _outside_measured_cutoff(
            target_s, series, relation="above", size_mb=largest
        )
    if float(size) < smallest:
        return _outside_measured_cutoff(
            target_s, series, relation="below", size_mb=smallest
        )
    return point


def interpolate_cutoff(
    rows: list[dict[str, Any]],
    target_s: float,
) -> dict[str, Any]:
    series = [
        (float(row["size_mb"]), float(row["median_s"]))
        for row in rows
        if float(row["size_mb"]) > 0 and float(row["median_s"]) > 0
    ]
    series.sort(key=lambda pair: pair[0])
    if len(series) < 2:
        if len(series) == 1 and series[0][1] < target_s:
            return _outside_measured_cutoff(
                target_s, series, relation="above", size_mb=series[0][0]
            )
        if len(series) == 1 and series[0][1] > target_s:
            return _outside_measured_cutoff(
                target_s, series, relation="below", size_mb=series[0][0]
            )
        return _unavailable_cutoff(target_s)
    for (size_0, time_0), (size_1, time_1) in zip(series, series[1:]):
        low, high = sorted((time_0, time_1))
        if low <= target_s <= high and time_0 != time_1:
            return _cutoff_point(
                size_0,
                time_0,
                size_1,
                time_1,
                target_s,
                extrapolated=False,
                method="log-log",
            )
    # Not reached on this ladder, or already passed at the smallest file.
    if all(seconds < target_s for _size, seconds in series):
        return _outside_measured_cutoff(
            target_s, series, relation="above", size_mb=series[-1][0]
        )
    if all(seconds > target_s for _size, seconds in series):
        return _outside_measured_cutoff(
            target_s, series, relation="below", size_mb=series[0][0]
        )
    return _clamp_to_measured_range(
        _regression_cutoff(series, target_s),
        series,
        target_s,
    )


def cutoffs_for(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if "median_s" not in row:
            continue
        if row["group"] != "format" and row["source_format"] != "python":
            continue
        role = row.get("cutoff_role", "ui")
        if role not in ("ui", "comparison"):
            continue
        grouped.setdefault(row["operation_id"], []).append(row)
    reports: list[dict[str, Any]] = []
    for measured in grouped.values():
        sample = measured[0]
        points = [interpolate_cutoff(measured, target) for target in CUTOFF_SECONDS]
        by_target = {point["target_s"]: point for point in points}
        slow = by_target[0.5]
        progress = by_target[2.0]
        reports.append(
            {
                "source_format": sample["source_format"],
                "operation_id": sample["operation_id"],
                "role": sample.get("cutoff_role", "ui"),
                "yaml_typ": sample.get("yaml_typ"),
                "no_indicator_below_s": 0.5,
                "spinner_until_s": 2.0,
                "progress_bar_from_s": 2.0,
                "no_indicator_below_mb": slow["size_mb"],
                "no_indicator_summary": slow.get("summary"),
                "spinner_only_below_mb": progress["size_mb"],
                "progress_bar_from_mb": progress["size_mb"],
                "progress_bar_summary": progress.get("summary"),
                "points": points,
            }
        )
    return reports


def machine_info() -> dict[str, Any]:
    system = platform.system()
    machine = platform.machine()
    apple_silicon = system == "Darwin" and machine == "arm64"
    info: dict[str, Any] = {
        "system": system,
        "machine": machine,
        "python": platform.python_version(),
        "apple_silicon": apple_silicon,
        "label": "Apple Silicon" if apple_silicon else "non-Apple-Silicon",
    }
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
    return info


def _basis(point: dict[str, Any]) -> str:
    if point.get("summary"):
        return "outside measured sizes"
    if point.get("size_mb") is None:
        return "unavailable"
    method = str(point["method"])
    if point.get("extrapolated"):
        return f"{method} extrapolation"
    return f"{method} interpolation"


def _cutoff_cell(point: dict[str, Any]) -> str:
    summary = point.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return _cell_mb(point.get("size_mb"))


def _cutoff_phrase(point: dict[str, Any]) -> str:
    summary = point.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return f"{_cell_mb(point.get('size_mb'))} MB"


def _mb(num_bytes: object) -> str:
    if num_bytes is None:
        return "n/a"
    return f"{int(num_bytes) / BYTES_PER_MB:.1f}"


def _cell_mb(value: object) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    if number < 10:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:.2f}"


def render_markdown(report: dict[str, Any]) -> str:
    machine = report["machine"]
    ruamel = report.get("ruamel") or {}
    clib_state = "installed" if ruamel.get("clib_installed") else "not installed"
    lines = [
        "# Galley core file-size benchmark",
        "",
        (
            f"Machine: {machine['system']} {machine['machine']} "
            f"({machine['label']}). Python {machine['python']}."
        ),
    ]
    if machine.get("cpu_model"):
        lines.append(f"CPU: {machine['cpu_model']}.")
    if not machine["apple_silicon"]:
        lines.append(
            "Provisional Linux x86 timings. The macos-14 run happens after "
            "the workflow is on main."
        )
    lines.extend(
        [
            "",
            (
                "Each size is exact "
                f"({report['bytes_per_mb']} bytes = 1 MB). "
                "Each size runs in a fresh process. Median is the whole call. "
                "Load is the parse. Dump is the write. "
                f"Each figure is the median of {report['runs']} runs. "
                "Peak RSS is the high "
                "resident set during those runs (sampled from the process, and "
                "`resource` ru_maxrss when the run sets a new high-water mark). "
                "`format_json` and `format_yaml` are galley.core. "
                '`format_yaml` is ruamel `typ="safe"` as shipped: the C loader '
                "parses and the pure-Python emitter writes when "
                "`ruamel.yaml.clib` imports, except documents that need a full "
                "pure-Python reparse. Without the library the pure-Python "
                "parser does both. YAML UI cutoffs use that row. "
                "Default JSON sizes are 1, 5, 10, 50, and 100 MB. "
                "Default shipped YAML, round-trip, and safe+C sizes are "
                "0.05, 0.1, 0.25, 0.5, 1, 5, and 10 MB. "
                "`--sizes` replaces those ladders for every row. "
                '`yaml_round_trip` is benchmark-local `YAML(typ="rt")`, '
                "always pure Python, and is a comparison. "
                '`yaml_safe_c` is benchmark-local `YAML(typ="safe")` with '
                f"`{RUAMEL_CLIB_SPEC}` and is a comparison. "
                f"In this parent process ruamel.yaml.clib is {clib_state}."
            ),
            "",
            "## Results",
            "",
        ]
    )
    headers = [
        "Operation",
        "YAML mode",
        "Size (MB)",
        "Median (s)",
        "Load (s)",
        "Dump (s)",
        "Min (s)",
        "Max (s)",
        "Peak RSS (MB)",
    ]
    table_rows = [_measurement_cells(row) for row in report["measurements"]]
    if table_rows:
        lines.extend(_markdown_table(headers, table_rows))
    else:
        lines.append("No core operations were available to time.")
    failures = [row for row in report["measurements"] if row.get("error")]
    if failures:
        lines.extend(["", "Sizes that did not finish:", ""])
        for row in failures:
            lines.append(
                f"- `{row['operation_id']}` {_format_mb(row['size_mb'])} MB: "
                f"{row['error']}."
            )
    lines.extend(["", "## UI cutoffs", ""])
    lines.append(
        "Formatting time bands: under 0.5 s no indicator, "
        "0.5 s to 2 s spinner only, over 2 s progress bar with Cancel. "
        "YAML bands use shipped `format_yaml` "
        '(`typ="safe"`, C extension when installed). '
        "Convert from Python shows no indicator. Its limits in "
        "`galley.app.limits` stay below the 0.5 s point of `python_to_json` "
        "and `python_to_yaml`."
    )
    lines.extend(
        _cutoff_section(
            [
                cutoff
                for cutoff in report["cutoffs"]
                if cutoff.get("role", "ui") == "ui"
            ],
            lead_in=True,
        )
    )
    comparisons = [
        cutoff for cutoff in report["cutoffs"] if cutoff.get("role") == "comparison"
    ]
    if comparisons:
        lines.extend(["", "## Comparisons", ""])
        compared = {cutoff["operation_id"] for cutoff in comparisons}
        notes: list[str] = []
        if "yaml_round_trip" in compared or "yaml_safe_c" in compared:
            notes.append(
                "`yaml_round_trip` and `yaml_safe_c` are not the UI bands. "
                'Round-trip is benchmark-local `YAML(typ="rt")` and stays pure '
                "Python. "
                '`yaml_safe_c` is benchmark-local `YAML(typ="safe")` with the '
                f"C extension (`{RUAMEL_CLIB_SPEC}`)."
            )
        if compared & set(_NEW_OPERATION_IDS):
            notes.append(
                "`format_yaml_realistic_pure` is shipped `format_yaml` with "
                "`ruamel.yaml.clib` not installed. "
                "`format_yaml_realistic_clib` is shipped `format_yaml` with "
                f"`{RUAMEL_CLIB_SPEC}` on the realistic corpus, including "
                "whatever parse and writer that file takes. "
                "`format_yaml_realistic_clib_reparse` is the same call with a "
                "leading `%YAML 1.2` directive so the path is a C parse and a "
                "full pure-Python re-parse. "
                "`format_yaml_plain_clib` is shipped `format_yaml` with the C "
                "extension on the plain corpus. "
                "`yaml_safe_c_realistic` is benchmark-local C parse plus C "
                "writer on the realistic corpus. "
                "`format_yaml_realistic_clean_pure` and "
                "`format_yaml_realistic_clean_clib` use the same shape with "
                "the rule-1 triggers turned off. Each `only_*` row is that "
                "clean file plus one header injection and stops at 1 MB. "
                "The natural rows, the clean rows, and the forced reparse "
                "row stop at 5 MB."
            )
        for index, note in enumerate(notes):
            if index:
                lines.append("")
            lines.append(note)
        lines.extend(_cutoff_section(comparisons, lead_in=False))
        ratio_rows = _rt_vs_safe_c_rows(report["measurements"])
        if ratio_rows:
            lines.extend(
                [
                    "",
                    "Round-trip median divided by safe+C median (cost of round-trip):",
                    "",
                    *_markdown_table(
                        ["Size (MB)", "Round-trip (s)", "Safe + C (s)", "Ratio"],
                        ratio_rows,
                    ),
                ]
            )
        speedup = _realistic_speedup_line(report["measurements"])
        if speedup:
            lines.extend(["", speedup])
        clean_speedup = _clean_speedup_line(report["measurements"])
        if clean_speedup:
            lines.extend(["", clean_speedup])
        writer_cost = _realistic_writer_cost_line(report["measurements"])
        if writer_cost:
            lines.extend(["", writer_cost])
    lines.extend(_path_section(report))
    if report["skipped"]:
        lines.extend(["", "## Not on this checkout", ""])
        for skipped in report["skipped"]:
            lines.append(
                f"- `{skipped['operation_id']}` ({skipped['source_format']}): "
                f"{skipped['reason']}."
            )
    lines.append("")
    return "\n".join(lines)


def _realistic_speedup_line(measurements: list[dict[str, Any]]) -> str | None:
    """Pure median / clib median at 0.1 MB and 1 MB, when both rows finished."""
    pure: dict[str, dict[str, Any]] = {}
    clib: dict[str, dict[str, Any]] = {}
    for row in measurements:
        if "median_s" not in row:
            continue
        size = _format_mb(float(row["size_mb"]))
        if row["operation_id"] == "format_yaml_realistic_pure":
            pure[size] = row
        elif row["operation_id"] == "format_yaml_realistic_clib":
            clib[size] = row
    parts: list[str] = []
    for size in ("0.1", "1"):
        if size not in pure or size not in clib:
            continue
        pure_s = float(pure[size]["median_s"])
        clib_s = float(clib[size]["median_s"])
        if clib_s <= 0:
            continue
        parts.append(
            f"{size} MB {pure_s / clib_s:.2f}x "
            f"(pure {pure_s:.4f} s, clib {clib_s:.4f} s)"
        )
    if not parts:
        return None
    joined = "; ".join(parts)
    return (
        "Realistic clib versus realistic pure speedup "
        f"(pure median / clib median): {joined}."
    )


def _clean_speedup_line(measurements: list[dict[str, Any]]) -> str | None:
    """Clean pure median / clean clib median at 0.1 MB and 1 MB."""
    pure: dict[str, dict[str, Any]] = {}
    clib: dict[str, dict[str, Any]] = {}
    for row in measurements:
        if "median_s" not in row:
            continue
        size = _format_mb(float(row["size_mb"]))
        if row["operation_id"] == "format_yaml_realistic_clean_pure":
            pure[size] = row
        elif row["operation_id"] == "format_yaml_realistic_clean_clib":
            clib[size] = row
    parts: list[str] = []
    for size in ("0.1", "1"):
        if size not in pure or size not in clib:
            continue
        pure_s = float(pure[size]["median_s"])
        clib_s = float(clib[size]["median_s"])
        if clib_s <= 0:
            continue
        parts.append(
            f"{size} MB {pure_s / clib_s:.2f}x "
            f"(pure {pure_s:.4f} s, clib {clib_s:.4f} s)"
        )
    if not parts:
        return None
    joined = "; ".join(parts)
    return (
        f"Clean clib versus clean pure speedup (pure median / clib median): {joined}."
    )


def _realistic_writer_cost_line(measurements: list[dict[str, Any]]) -> str | None:
    """Shipped realistic clib median / realistic safe+C median."""
    shipped: dict[str, dict[str, Any]] = {}
    safe_c: dict[str, dict[str, Any]] = {}
    for row in measurements:
        if "median_s" not in row:
            continue
        size = _format_mb(float(row["size_mb"]))
        if row["operation_id"] == "format_yaml_realistic_clib":
            shipped[size] = row
        elif row["operation_id"] == "yaml_safe_c_realistic":
            safe_c[size] = row
    parts: list[str] = []
    for size in ("0.1", "1"):
        if size not in shipped or size not in safe_c:
            continue
        shipped_s = float(shipped[size]["median_s"])
        safe_s = float(safe_c[size]["median_s"])
        if safe_s <= 0:
            continue
        parts.append(
            f"{size} MB {shipped_s / safe_s:.2f}x "
            f"(shipped clib {shipped_s:.4f} s, safe+C {safe_s:.4f} s)"
        )
    if not parts:
        return None
    joined = "; ".join(parts)
    return (
        "Cost of leaving the C writer on the realistic corpus "
        "(shipped clib median / safe+C median): "
        f"{joined}."
    )


def _path_section(report: dict[str, Any]) -> list[str]:
    rows: list[list[str]] = []
    natural: list[dict[str, Any]] = []
    forced: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for row in report["measurements"]:
        if "yaml_path" not in row:
            continue
        rows.append(
            [
                str(row["operation_id"]),
                _format_mb(float(row["size_mb"])),
                str(row.get("yaml_path_label", row["yaml_path"])),
            ]
        )
        if row["operation_id"] == "format_yaml_realistic_clib":
            natural.append(row)
        elif row["operation_id"] == "format_yaml_realistic_clib_reparse":
            forced.append(row)
        if row.get("path_mismatch"):
            mismatches.append(row)
    if not rows:
        return []
    lines = [
        "",
        "## YAML path",
        "",
        (
            "Shipped C-path rows record the engines `YAML.scan`, `YAML.load`, "
            "and `YAML.dump` used. Pure re-parse is a C scan or C load followed "
            "by a pure-Python parse, with no C error. C parse plus pure writer "
            "is a C load and a pure dump with no second parse. C rejected and "
            "fell back is a C error and then pure Python. C only is a C load "
            "and a C dump. "
            "`yaml_safe_c_realistic` is C parse plus C writer by construction. "
            "The forced reparse row and the directive-only row must take the "
            "pure re-parse path at every size. The clean clib row and the "
            "bare-tag row must be C parse plus pure writer. The unforced "
            "realistic row and the quoted-tab row must be a pure re-parse, "
            "because a tab forces that path. A different path fails the "
            "bench. The unforced fraction below is how many realistic "
            "sizes kept the C value."
        ),
        "",
        *_markdown_table(
            ["Operation", "Size (MB)", "Path"],
            rows,
        ),
    ]
    if natural:
        kept = sum(
            1
            for row in natural
            if row["yaml_path"] in {PATH_C_PARSE_PURE_WRITER, PATH_C_ONLY}
        )
        lines.append("")
        lines.append(
            "Unforced realistic size files that kept the C value: "
            f"{kept}/{len(natural)}."
        )
    if forced:
        fired = sum(1 for row in forced if row["yaml_path"] == PATH_PURE_REPARSE)
        lines.append(
            f"Forced reparse sizes on the pure re-parse path: {fired}/{len(forced)}."
        )
    if mismatches:
        lines.append("Sizes that did not take the expected path:")
        for row in mismatches:
            expected = _PATH_LABELS.get(
                str(row.get("path_expected", "")),
                str(row.get("path_expected", "")),
            )
            lines.append(
                f"- `{row['operation_id']}` {_format_mb(float(row['size_mb']))} MB "
                f"ran {row.get('yaml_path_label', row['yaml_path'])}; "
                f"expected {expected}."
            )
    trigger_groups: dict[str, list[dict[str, Any]]] = {}
    for row in report["measurements"]:
        operation_id = row.get("operation_id")
        if operation_id in _TRIGGER_OPERATION_IDS and "yaml_path" in row:
            trigger_groups.setdefault(str(operation_id), []).append(row)
    for operation_id, group in trigger_groups.items():
        labels = list(
            dict.fromkeys(
                str(row.get("yaml_path_label", row["yaml_path"])) for row in group
            )
        )
        lines.append(
            f"`{operation_id}`: {', '.join(labels)} at {len(group)}/{len(group)} sizes."
        )
    triggers = report.get("realistic_triggers")
    if isinstance(triggers, dict) and triggers.get("items"):
        total = int(triggers["items"])
        labels = (
            ("block_scalar", "block scalar"),
            ("accented", "accented text"),
            ("emoji", "emoji"),
            ("cjk", "CJK"),
            ("binary", "`!!binary`"),
            ("yaml_directive_text", "`%YAML` text"),
            ("bare_tag", "bare `!` tag"),
            ("backslash", "backslash escape"),
            ("quoted_tab", "tab inside quotes"),
        )
        parts = [
            f"{label} {int(triggers.get(key, 0))}/{total}" for key, label in labels
        ]
        lines.append(
            "Realistic items in the largest generated file "
            f"({total}): {'; '.join(parts)}."
        )
    return lines


def _rt_vs_safe_c_rows(measurements: list[dict[str, Any]]) -> list[list[str]]:
    round_trip: dict[float, dict[str, Any]] = {}
    safe_c: dict[float, dict[str, Any]] = {}
    for row in measurements:
        if "median_s" not in row:
            continue
        size = float(row["size_mb"])
        if row["operation_id"] == "yaml_round_trip":
            round_trip[size] = row
        elif row["operation_id"] == "yaml_safe_c":
            safe_c[size] = row
    rows: list[list[str]] = []
    for size in sorted(set(round_trip) & set(safe_c)):
        rt_s = float(round_trip[size]["median_s"])
        safe_s = float(safe_c[size]["median_s"])
        if safe_s <= 0:
            continue
        rows.append(
            [
                _format_mb(size),
                f"{rt_s:.4f}",
                f"{safe_s:.4f}",
                f"{rt_s / safe_s:.2f}x",
            ]
        )
    return rows


def _yaml_mode(row: dict[str, Any]) -> str:
    operation_id = row.get("operation_id")
    if operation_id == "format_yaml":
        return "safe, shipped"
    if operation_id in _PURE_OPERATION_IDS:
        return "safe, shipped, no C"
    if operation_id == "format_yaml_realistic_clib_reparse":
        return "safe, shipped, C, reparse"
    if operation_id == "format_yaml_realistic_clean_clib":
        return "safe, shipped, C, clean"
    if operation_id in _TRIGGER_OPERATION_IDS:
        return "safe, shipped, C, one trigger"
    if operation_id in _SHIPPED_CLIB_IDS:
        return "safe, shipped, C"
    if row.get("yaml_typ") == "safe":
        return "safe"
    if row.get("yaml_typ") == "safe-c":
        return "safe + C"
    if row.get("yaml_typ") == "rt":
        return "round-trip"
    return "—"


def _cutoff_section(cutoffs: list[dict[str, Any]], *, lead_in: bool) -> list[str]:
    if not cutoffs:
        if lead_in:
            return ["", "No format operation was available to interpolate."]
        return []
    rows = []
    for cutoff in cutoffs:
        points = {point["target_s"]: point for point in cutoff["points"]}
        rows.append(
            [
                cutoff["source_format"],
                cutoff["operation_id"],
                _cutoff_cell(points[0.5]),
                _cutoff_cell(points[2.0]),
                _basis(points[0.5]),
                _basis(points[2.0]),
            ]
        )
    lines = [
        "",
        *_markdown_table(
            [
                "Format",
                "Operation",
                "0.5 s (MB)",
                "2 s (MB)",
                "0.5 s basis",
                "2 s basis",
            ],
            rows,
        ),
        "",
    ]
    for cutoff in cutoffs:
        points = {point["target_s"]: point for point in cutoff["points"]}
        slow = points[0.5]
        progress = points[2.0]
        slow_phrase = _cutoff_phrase(slow)
        progress_phrase = _cutoff_phrase(progress)
        prefix = f"- {cutoff['source_format']} (`{cutoff['operation_id']}`): "
        if slow.get("summary") or progress.get("summary"):
            lines.append(
                f"{prefix}0.5 s cutoff is {slow_phrase}; "
                f"2 s cutoff is {progress_phrase}."
            )
            continue
        lines.append(
            f"{prefix}no indicator under {slow_phrase}; "
            f"spinner only from {slow_phrase} up to {progress_phrase}; "
            f"progress bar with Cancel at {progress_phrase} and above."
        )
    return lines


def _measurement_cells(row: dict[str, Any]) -> list[str]:
    mode = _yaml_mode(row)
    size = _format_mb(row["size_mb"])
    if "median_s" not in row:
        return [row["operation_id"], mode, size, *["n/a"] * 6]
    return [
        row["operation_id"],
        mode,
        size,
        f"{row['median_s']:.4f}",
        f"{row['median_load_s']:.4f}",
        f"{row['median_dump_s']:.4f}",
        f"{row['min_s']:.4f}",
        f"{row['max_s']:.4f}",
        _mb(row["peak_rss_bytes"]),
    ]


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(row: list[str]) -> str:
        cells = [cell.ljust(widths[index]) for index, cell in enumerate(row)]
        return "| " + " | ".join(cells) + " |"

    divider = "| " + " | ".join("-" * width for width in widths) + " |"
    return [format_row(headers), divider, *(format_row(row) for row in rows)]


def build_report(
    sizes_mb: list[float] | None,
    runs: int,
    output_dir: Path,
    *,
    corpus: str = "plain",
) -> dict[str, Any]:
    operations, skipped = discover_operations()
    selected = [
        operation for operation in operations if _operation_selected(operation, corpus)
    ]
    if not selected:
        raise RuntimeError("galley.core has no format, validate, or convert functions.")
    requested = None if sizes_mb is None else [float(size) for size in sizes_mb]
    planned = {
        operation.operation_id: _sizes_for_operation(operation, requested)
        for operation in selected
    }
    by_corpus: dict[tuple[str, str], set[float]] = {}
    for operation in selected:
        by_corpus.setdefault((operation.source_format, operation.corpus), set()).update(
            planned[operation.operation_id]
        )
    measurements: list[dict[str, Any]] = []
    machine = machine_info()
    ruamel = ruamel_environment()
    realistic_triggers: dict[str, int] | None = None

    def report_so_far() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bytes_per_mb": BYTES_PER_MB,
            "corpus": corpus,
            "sizes_mb": requested,
            "ladders_mb": planned,
            "runs": runs,
            "memory_source": "rss",
            "isolated_process": True,
            "machine": machine,
            "ruamel": ruamel,
            "measurements": measurements,
            "cutoffs": cutoffs_for(measurements),
            "skipped": skipped,
        }
        if realistic_triggers is not None:
            payload["realistic_triggers"] = realistic_triggers
        return payload

    def checkpoint(row: dict[str, Any]) -> None:
        # Rewrite both result files after every size, before the next one starts.
        measurements.append(row)
        _write_report(report_so_far(), output_dir, to_stdout=False)

    with tempfile.TemporaryDirectory(prefix="galley-size-bench-") as temporary:
        root = Path(temporary)
        generated: dict[tuple[str, str], dict[float, tuple[Path, int]]] = {}
        for source_format, file_corpus in sorted(by_corpus):
            directory = root / f"{file_corpus}-{source_format}"
            directory.mkdir()
            generated[(source_format, file_corpus)] = generate_files(
                directory,
                source_format,
                sorted(by_corpus[(source_format, file_corpus)]),
                corpus=file_corpus,
            )
        realistic_files = generated.get(("yaml", "realistic"))
        if realistic_files:
            largest = max(realistic_files)
            realistic_triggers = realistic_trigger_counts(realistic_files[largest][1])
        # Build the shipped environment before the first timed size. Later
        # format_yaml sizes reuse the cached interpreter.
        if any(operation.operation_id == "format_yaml" for operation in selected):
            _shipped_interpreter()
        for operation in selected:
            wanted = planned[operation.operation_id]
            if not wanted:
                continue
            files = {
                size: generated[(operation.source_format, operation.corpus)][size]
                for size in wanted
            }
            print(f"timing {operation.operation_id}", file=sys.stderr)
            measure_operation(
                operation,
                files,
                runs,
                on_row=checkpoint,
            )
    report = report_so_far()
    _write_report(report, output_dir, to_stdout=True)
    return report


def _atomic_write(path: Path, text: str) -> None:
    temporary = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        temporary.write(text)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary_path, path)
    except Exception:
        temporary.close()
        temporary_path.unlink(missing_ok=True)
        raise


def _write_report(report: dict[str, Any], output_dir: Path, *, to_stdout: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report)
    payload = json.dumps(report, indent=2) + "\n"
    _atomic_write(output_dir / "size_bench.md", markdown)
    _atomic_write(output_dir / "size_bench.json", payload)
    if to_stdout:
        print(markdown, end="")


def parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=float,
        nargs="+",
        default=None,
        help=(
            "File sizes in MB (1 MB = 1,048,576 bytes) for every selected row. "
            "Omit to use the default ladders: JSON 1, 5, 10, 50, 100; "
            "shipped YAML, round-trip, and safe+C "
            "0.05, 0.1, 0.25, 0.5, 1, 5, 10; "
            "natural realistic rows, clean rows, forced reparse, "
            "plain shipped C, and realistic safe+C "
            "0.05, 0.1, 0.25, 0.5, 1, 5; "
            "single-trigger rows "
            "0.05, 0.1, 0.25, 0.5, 1; "
            "Convert from Python rows "
            "0.1, 0.25, 0.5, 1, 2, 5. "
            "Sizes above those caps are timed when passed here."
        ),
    )
    parser.add_argument(
        "--corpus",
        choices=("plain", "realistic", "all"),
        default="plain",
        help=(
            "plain (default): historical rows on the synthetic catalog. "
            "realistic: natural, clean, single-trigger, forced reparse, "
            "plain clib, and realistic safe+C. "
            "all: historical rows and those new rows."
        ),
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "benchmarks" / "results",
    )
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--operation")
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--size-mb", type=float)
    parser.add_argument("--item-count", type=int)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.worker:
        if not args.operation or args.input_file is None:
            parser.error("--worker requires --operation and --input-file")
        if args.size_mb is None or args.item_count is None:
            parser.error("--worker requires --size-mb and --item-count")
        return args
    if args.sizes is not None and any(size <= 0 for size in args.sizes):
        parser.error("--sizes values must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.worker:
        return run_worker(
            args.operation,
            args.input_file,
            args.size_mb,
            args.runs,
            args.item_count,
        )
    build_report(args.sizes, args.runs, args.output_dir, corpus=args.corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
