"""Benchmark galley.core against synthetic JSON and YAML files.

Generates nested documents in a temporary directory, times the format,
validate, and convert callables that exist on this checkout, and writes a
markdown table plus JSON. Generated inputs are deleted with the temp directory.

    uv run python benchmarks/size_bench.py
"""

import argparse
import ctypes
import gc
import inspect
import json
import math
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

import galley.core
from galley.core import format as core_format

BYTES_PER_MB = 1024 * 1024
DEFAULT_SIZES_MB = (1, 5, 10, 50, 100, 200)
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


@dataclass(frozen=True)
class Operation:
    operation_id: str
    group: str
    source_format: str
    fn: Callable[..., object]


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
        found.append(
            Operation(
                operation_id=spec["operation_id"],
                group=spec["group"],
                source_format=spec["source_format"],
                fn=fn,
            )
        )
    return found, skipped


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


def _write_document(
    path: Path,
    target_bytes: int,
    *,
    header: str,
    separator: str,
    footer_prefix: str,
    footer_suffix: str,
    render_item: Callable[[int], str],
) -> int:
    footer_overhead = len(footer_prefix) + len(footer_suffix)
    count = 0
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(header)
        written = len(header)
        index = 0
        while True:
            item = render_item(index)
            prefix = separator if count else ""
            projected = written + len(prefix) + len(item) + footer_overhead
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


def generate_files(
    directory: Path,
    source_format: str,
    sizes_mb: tuple[int, ...] | list[int],
) -> dict[int, tuple[Path, int]]:
    if source_format == "json":
        suffix = ".json"
        header = JSON_HEADER
        separator = JSON_SEPARATOR
        footer_prefix = JSON_FOOTER_PREFIX
        footer_suffix = JSON_FOOTER_SUFFIX
        render_item = _json_item
    elif source_format == "yaml":
        suffix = ".yaml"
        header = YAML_HEADER
        separator = YAML_SEPARATOR
        footer_prefix = YAML_FOOTER_PREFIX
        footer_suffix = YAML_FOOTER_SUFFIX
        render_item = _yaml_item
    else:
        message = f"No synthetic generator for {source_format}."
        raise RuntimeError(message)

    written: dict[int, tuple[Path, int]] = {}
    for size_mb in sizes_mb:
        path = directory / f"{size_mb}mb{suffix}"
        print(f"generating {source_format} {size_mb} MB", file=sys.stderr)
        count = _write_document(
            path,
            size_mb * BYTES_PER_MB,
            header=header,
            separator=separator,
            footer_prefix=footer_prefix,
            footer_suffix=footer_suffix,
            render_item=render_item,
        )
        written[size_mb] = (path, count)
    _validate_sample(written[min(written)][0], source_format)
    return written


def _validate_sample(path: Path, source_format: str) -> None:
    text = path.read_text(encoding="ascii")
    if source_format == "json":
        parsed = json.loads(text)
    else:
        parsed = YAML(typ="safe").load(text)
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items:
        message = f"Synthetic {source_format} document did not parse."
        raise RuntimeError(message)
    del parsed, text


def current_rss_bytes() -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    completed = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(completed.stdout.split()[0]) * 1024


def process_ru_maxrss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
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
        if rss > self.peak_bytes:
            self.peak_bytes = rss

    def _sample(self) -> None:
        while not self._stop.wait(SAMPLE_INTERVAL_S):
            self._observe()

    def start(self) -> None:
        self._observe()
        self._thread.start()

    def stop(self) -> int:
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


def _time_once(fn: Callable[..., object], text: str) -> tuple[float, int]:
    sampler = RssSampler()
    sampler.start()
    try:
        started = time.perf_counter()
        result = fn(text)
        elapsed = time.perf_counter() - started
        _touch(result)
        peak = sampler.stop()
    except Exception:
        sampler.stop()
        raise
    del result
    return elapsed, peak


def _warmup(operation: Operation) -> None:
    sample = '{"warmup":1}\n' if operation.source_format == "json" else "warmup: 1\n"
    operation.fn(sample)


def _measure_loaded(
    operation: Operation,
    text: str,
    size_mb: int,
    runs: int,
    item_count: int,
) -> dict[str, Any]:
    _warmup(operation)
    samples: list[dict[str, float | int]] = []
    before_rss = process_ru_maxrss_bytes()
    for run_index in range(runs):
        release_memory()
        elapsed, peak = _time_once(operation.fn, text)
        samples.append({"seconds": round(elapsed, 6), "peak_rss_bytes": peak})
        print(
            f"{operation.operation_id} {size_mb} MB "
            f"run {run_index + 1}/{runs}: {elapsed:.4f} s",
            file=sys.stderr,
        )
    after_rss = process_ru_maxrss_bytes()
    seconds = [float(sample["seconds"]) for sample in samples]
    peak_bytes = max(int(sample["peak_rss_bytes"]) for sample in samples)
    if after_rss > before_rss:
        peak_bytes = max(peak_bytes, after_rss)
    return {
        "operation_id": operation.operation_id,
        "group": operation.group,
        "source_format": operation.source_format,
        "size_mb": size_mb,
        "size_bytes": size_mb * BYTES_PER_MB,
        "item_count": item_count,
        "runs": samples,
        "median_s": round(statistics.median(seconds), 6),
        "min_s": round(min(seconds), 6),
        "max_s": round(max(seconds), 6),
        "peak_rss_bytes": peak_bytes,
        "process_ru_maxrss_bytes": after_rss,
    }


def _error_row(
    operation: Operation,
    size_mb: int,
    item_count: int,
    error: str,
) -> dict[str, Any]:
    return {
        "operation_id": operation.operation_id,
        "group": operation.group,
        "source_format": operation.source_format,
        "size_mb": size_mb,
        "size_bytes": size_mb * BYTES_PER_MB,
        "item_count": item_count,
        "error": error,
    }


def _measure_in_subprocess(
    operation: Operation,
    path: Path,
    size_mb: int,
    runs: int,
    item_count: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--operation",
            operation.operation_id,
            "--input-file",
            str(path),
            "--size-mb",
            str(size_mb),
            "--runs",
            str(runs),
            "--item-count",
            str(item_count),
        ],
        check=False,
        stdout=subprocess.PIPE,
        text=True,
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
    message = (
        f"{operation.operation_id} {size_mb} MB worker exited {completed.returncode}"
    )
    raise RuntimeError(message)


def measure_operation(
    operation: Operation,
    files: dict[int, tuple[Path, int]],
    runs: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    blocked = False
    for size_mb, (path, item_count) in files.items():
        if blocked:
            rows.append(
                _error_row(
                    operation,
                    size_mb,
                    item_count,
                    "skipped after a smaller size ran out of memory",
                )
            )
            continue
        print(f"timing {operation.operation_id} {size_mb} MB", file=sys.stderr)
        row = _measure_in_subprocess(operation, path, size_mb, runs, item_count)
        rows.append(row)
        if row.get("error"):
            blocked = True
    return rows


def run_worker(
    operation_id: str,
    input_file: Path,
    size_mb: int,
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
        return {
            "target_s": target_s,
            "size_mb": None,
            "size_bytes": None,
            "extrapolated": None,
            "method": "unavailable",
            "bracket_mb": None,
            "bracket_s": None,
        }
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
    if target_s >= max(seconds for _size, seconds in series):
        (size_0, time_0), (size_1, time_1) = series[-2], series[-1]
        if time_1 > time_0:
            return _cutoff_point(
                size_0,
                time_0,
                size_1,
                time_1,
                target_s,
                extrapolated=True,
                method="log-log",
            )
    if target_s <= min(seconds for _size, seconds in series):
        (size_0, time_0), (size_1, time_1) = series[0], series[1]
        if time_0 != time_1:
            return _cutoff_point(
                size_0,
                time_0,
                size_1,
                time_1,
                target_s,
                extrapolated=True,
                method="log-log",
            )
    return _regression_cutoff(series, target_s)


def cutoffs_for(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    format_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["group"] != "format" or "median_s" not in row:
            continue
        format_rows.setdefault(row["source_format"], []).append(row)
    for source_format, measured in format_rows.items():
        operation_id = measured[0]["operation_id"]
        points = [interpolate_cutoff(measured, target) for target in CUTOFF_SECONDS]
        by_target = {point["target_s"]: point for point in points}
        slow = by_target[0.5]
        progress = by_target[2.0]
        reports.append(
            {
                "source_format": source_format,
                "operation_id": operation_id,
                "no_indicator_below_s": 0.5,
                "spinner_until_s": 2.0,
                "progress_bar_from_s": 2.0,
                "no_indicator_below_mb": slow["size_mb"],
                "spinner_only_below_mb": progress["size_mb"],
                "progress_bar_from_mb": progress["size_mb"],
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
    if point.get("size_mb") is None:
        return "unavailable"
    method = str(point["method"])
    if point.get("extrapolated"):
        return f"{method} extrapolation"
    return f"{method} interpolation"


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / BYTES_PER_MB:.1f}"


def _cell_mb(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def render_markdown(report: dict[str, Any]) -> str:
    machine = report["machine"]
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
        lines.append("These timings are not from an Apple Silicon macos-14 runner.")
    lines.extend(
        [
            "",
            (
                "Each size is exact "
                f"({report['bytes_per_mb']} bytes = 1 MB). "
                "Each size runs in a fresh process. Times are wall time inside "
                "the core function after the text is already in memory. "
                f"The median is {report['runs']} runs. Peak RSS is the high "
                "resident set during those runs (sampled from the process, and "
                "`resource` ru_maxrss when the run sets a new high-water mark)."
            ),
            "",
            "## Results",
            "",
        ]
    )
    headers = [
        "Operation",
        "Size (MB)",
        "Median (s)",
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
                f"- `{row['operation_id']}` {row['size_mb']} MB: {row['error']}."
            )
    lines.extend(["", "## UI cutoffs", ""])
    lines.append(
        "Formatting time bands: under 0.5 s no indicator, "
        "0.5 s to 2 s spinner only, over 2 s progress bar with Cancel."
    )
    lines.append("")
    cutoff_rows = []
    for cutoff in report["cutoffs"]:
        points = {point["target_s"]: point for point in cutoff["points"]}
        cutoff_rows.append(
            [
                cutoff["source_format"],
                cutoff["operation_id"],
                _cell_mb(cutoff["no_indicator_below_mb"]),
                _cell_mb(cutoff["progress_bar_from_mb"]),
                _basis(points[0.5]),
                _basis(points[2.0]),
            ]
        )
    if cutoff_rows:
        lines.extend(
            _markdown_table(
                [
                    "Format",
                    "Operation",
                    "0.5 s (MB)",
                    "2 s (MB)",
                    "0.5 s basis",
                    "2 s basis",
                ],
                cutoff_rows,
            )
        )
        lines.append("")
        for cutoff in report["cutoffs"]:
            spinner = _cell_mb(cutoff["no_indicator_below_mb"])
            progress = _cell_mb(cutoff["progress_bar_from_mb"])
            lines.append(
                f"- {cutoff['source_format']}: no indicator under {spinner} MB; "
                f"spinner only from {spinner} MB up to {progress} MB; "
                f"progress bar with Cancel at {progress} MB and above."
            )
    else:
        lines.append("No format operation was available to interpolate.")
    if report["skipped"]:
        lines.extend(["", "## Not on this checkout", ""])
        for skipped in report["skipped"]:
            lines.append(
                f"- `{skipped['operation_id']}` ({skipped['source_format']}): "
                f"{skipped['reason']}."
            )
    lines.append("")
    return "\n".join(lines)


def _measurement_cells(row: dict[str, Any]) -> list[str]:
    if "median_s" not in row:
        return [row["operation_id"], str(row["size_mb"]), "n/a", "n/a", "n/a", "n/a"]
    return [
        row["operation_id"],
        str(row["size_mb"]),
        f"{row['median_s']:.4f}",
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
    sizes_mb: list[int],
    runs: int,
    output_dir: Path,
) -> dict[str, Any]:
    operations, skipped = discover_operations()
    if not operations:
        raise RuntimeError("galley.core has no format, validate, or convert functions.")
    needed = sorted({operation.source_format for operation in operations})
    measurements: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="galley-size-bench-") as temporary:
        root = Path(temporary)
        generated: dict[str, dict[int, tuple[Path, int]]] = {}
        for source_format in needed:
            generated[source_format] = generate_files(
                root,
                source_format,
                sizes_mb,
            )
        for operation in operations:
            print(f"timing {operation.operation_id}", file=sys.stderr)
            measurements.extend(
                measure_operation(
                    operation,
                    generated[operation.source_format],
                    runs,
                )
            )
    report = {
        "bytes_per_mb": BYTES_PER_MB,
        "sizes_mb": sizes_mb,
        "runs": runs,
        "memory_source": "rss",
        "isolated_process": True,
        "machine": machine_info(),
        "measurements": measurements,
        "cutoffs": cutoffs_for(measurements),
        "skipped": skipped,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report)
    (output_dir / "size_bench.md").write_text(markdown, encoding="utf-8")
    (output_dir / "size_bench.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(markdown, end="")
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SIZES_MB),
        help="File sizes in MB (1 MB = 1,048,576 bytes).",
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
    parser.add_argument("--size-mb", type=int)
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
    if any(size < 1 for size in args.sizes):
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
    build_report(args.sizes, args.runs, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
