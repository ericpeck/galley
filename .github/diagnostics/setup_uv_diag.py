#!/usr/bin/env python3
"""Isolate one Galley macOS test sample for the setup-uv pin diagnostic.

The workflow checks out this helper and, separately, the frozen app tree.
Each sample copies that tree, then runs the same ``uv sync`` and pytest
command as the Test (macOS arm64) job. The sample subprocess gets a
controlled environment: the baseline PATH plus only this sample's uv
directory, and sample-local uv cache, project environment, and Python
install directories. The helper never writes ``GITHUB_PATH``.

Stdlib only. It does not run ``eval`` or ``exec``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

FROZEN_APP_SHA = "50950262452a27cfe0faf07746f60f89d508887d"
EXPECTED_UV = "0.13.6"
EXPECTED_PYTHON = "3.12.10"
ACTION_SHA = {
    "A": "07a047e608db84b3d8c06cd9a7c7dcc9c355c18e",
    "B": "68559e99cc807c1eb7e9238fd43a15bba460eec2",
}
SAMPLE_ORDER = ("B", "A", "A", "B", "B", "A")
CLIB_MESSAGE = "ruamel.yaml.clib is not installed"
HALT_NAME = "HALT"

_UV_VERSION_LINE = re.compile(r"^uv (?P<version>\d+\.\d+\.\d+)\b")
_SUMMARY_LINE = re.compile(
    r"^=+\s+(?P<body>.+?)\s+in\s+\d+(?:\.\d+)?s(?:\s+\([^)]*\))?\s+=+\s*$"
)
_SUMMARY_COUNT = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|skipped|deselected|failed|errors?|xfailed|xpassed|warnings?)"
)
_SUMMARY_LABELS = {
    "passed": "pass",
    "skipped": "skip",
    "deselected": "deselect",
    "failed": "fail",
    "error": "error",
    "errors": "error",
    "xfailed": "xfail",
    "xpassed": "xpass",
}
_SKIP_NAMES = frozenset(
    {
        ".git",
        ".hypothesis",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "__pycache__",
        "diag-state",
        "diag-results",
    }
)
_SENSITIVE_PARTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTHORIZATION",
    "COOKIE",
    "SESSION",
    "PRIVATE",
)
_TOKEN_MARKERS = ("ghp_", "ghs_", "gho_", "github_pat_", "xox")
# Replaced after the action diff is applied. PATH is rebuilt; the uv
# directories are sample-local; VIRTUAL_ENV would point at another sample.
_OVERRIDDEN = frozenset(
    {
        "PATH",
        "UV_CACHE_DIR",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON_INSTALL_DIR",
        "UV_PYTHON",
        "QT_QPA_PLATFORM",
        "GALLEY_BIGCOMPARE_LOG",
        "GITHUB_PATH",
        "VIRTUAL_ENV",
    }
)
_PROBE_SOURCE = "import sys\nprint(sys.version.split()[0])\nprint(sys.executable)\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Record the frozen app SHA, lock hash, and baseline environment",
    )
    prepare.add_argument("--app-source", type=Path, required=True)
    prepare.add_argument("--state-dir", type=Path, required=True)
    prepare.add_argument("--results-dir", type=Path, required=True)
    _add_pin_args(prepare)
    prepare.set_defaults(func=cmd_prepare)

    mark = subparsers.add_parser(
        "mark",
        help="Snapshot PATH and the clock immediately before setup-uv",
    )
    mark.add_argument("--index", type=int, required=True)
    mark.add_argument("--state-dir", type=Path, required=True)
    mark.set_defaults(func=cmd_mark)

    sample = subparsers.add_parser(
        "sample",
        help="Copy the app tree and time uv sync plus pytest for one pin",
    )
    sample.add_argument("--index", type=int, required=True)
    sample.add_argument("--variant", choices=("A", "B"), required=True)
    sample.add_argument("--app-source", type=Path, required=True)
    sample.add_argument("--uv", default="")
    sample.add_argument("--output", type=Path, required=True)
    sample.add_argument("--action-sha", required=True)
    sample.add_argument("--state-dir", type=Path, required=True)
    sample.add_argument("--work-root", type=Path, required=True)
    sample.add_argument("--reported-uv-version", default="")
    sample.add_argument("--reported-python-version", default="")
    _add_pin_args(sample)
    sample.set_defaults(func=cmd_sample)

    summarize = subparsers.add_parser(
        "summarize",
        help="Write the step summary and fail if any sample or pin check failed",
    )
    summarize.add_argument("--input", type=Path, required=True)
    summarize.add_argument("--identity", type=Path, required=True)
    summarize.add_argument("--summary", type=Path)
    summarize.set_defaults(func=cmd_summarize)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _add_pin_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-sha", default=FROZEN_APP_SHA)
    parser.add_argument("--expected-uv", default=EXPECTED_UV)
    parser.add_argument("--expected-python", default=EXPECTED_PYTHON)


def cmd_prepare(args: argparse.Namespace) -> int:
    args.app_source = args.app_source.resolve()
    args.state_dir = args.state_dir.resolve()
    args.results_dir = args.results_dir.resolve()
    reasons = _constant_pin_reasons(
        args.expected_sha, args.expected_uv, args.expected_python
    )
    observed = git_head(args.app_source)
    lock_hash = sha256_file(args.app_source / "uv.lock")
    if observed is None:
        reasons.append("git rev-parse HEAD failed for the app tree")
    elif observed != FROZEN_APP_SHA:
        reasons.append(f"app SHA {observed} != {FROZEN_APP_SHA}")
    if lock_hash is None:
        reasons.append("uv.lock is missing from the app tree")
    identity = {
        "expected_sha": FROZEN_APP_SHA,
        "app_sha": observed,
        "lock_hash": lock_hash,
        "mismatch": bool(reasons),
        "reasons": reasons,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(args.results_dir / "identity.json", identity)
    _write_json(args.state_dir / "identity.json", identity)
    (args.results_dir / "samples.jsonl").write_text("", encoding="utf-8")
    _write_private_json(
        args.state_dir / "baseline.json",
        {"time": time.time(), "env": dict(os.environ)},
    )
    print(f"app {observed}", flush=True)
    print(f"lock {lock_hash}", flush=True)
    if reasons:
        _write_halt(args.state_dir, "; ".join(reasons))
        print("identity mismatch: " + "; ".join(reasons), flush=True)
        return 1
    print("identity matches the frozen app tree", flush=True)
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    if args.index < 1 or args.index > len(SAMPLE_ORDER):
        print(f"sample index {args.index} is outside 1..6", flush=True)
        return 1
    args.state_dir.mkdir(parents=True, exist_ok=True)
    _write_private_json(
        args.state_dir / f"mark-{args.index}.json",
        {"index": args.index, "time": time.time(), "env": dict(os.environ)},
    )
    print(f"marked sample {args.index}", flush=True)
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """Run one predeclared sample, or record that a pin mismatch stopped it."""
    args.app_source = args.app_source.resolve()
    args.state_dir = args.state_dir.resolve()
    args.work_root = args.work_root.resolve()
    args.output = args.output.resolve()
    wall_start = time.time()
    static = static_pin_reasons(
        args.index,
        args.variant,
        args.action_sha,
        args.expected_sha,
        args.expected_uv,
        args.expected_python,
    )
    record = _base_record(args.index, args.variant, args.action_sha)
    record["reported_uv_version"] = args.reported_uv_version.strip() or None
    record["reported_python_version"] = args.reported_python_version.strip() or None
    record["runner_image_version"] = os.environ.get("ImageVersion")
    record["runner_image_os"] = os.environ.get("ImageOS")
    record["runner_os"] = os.environ.get("RUNNER_OS")

    if (args.state_dir / HALT_NAME).is_file():
        record["status"] = "mismatch" if static else "skipped"
        record["mismatch"] = bool(static)
        record["mismatch_reasons"] = static
        record["notes"] = static or ["not launched: an earlier pin check mismatched"]
        record["start"] = _iso(wall_start)
        record["end"] = _iso(wall_start)
        record["total_seconds"] = 0.0
        return _finish(args.output, record, 1 if static else 0)

    mark, baseline, identity, load_reasons = _load_sample_state(args)
    reasons = [*static, *load_reasons]
    mark_time = mark.get("time") if mark else None
    if isinstance(mark_time, int | float):
        record["setup_seconds"] = round(wall_start - float(mark_time), 3)
        record["start"] = _iso(float(mark_time))
    else:
        record["start"] = _iso(wall_start)
    if mark and baseline:
        record["env_diff"] = env_diff(mark["env"], dict(os.environ))

    observed = git_head(args.app_source) if args.app_source.is_dir() else None
    source_lock = sha256_file(args.app_source / "uv.lock")
    record["app_sha"] = observed
    record["lock_hash"] = source_lock
    reasons.extend(_identity_reasons(observed, source_lock, identity))

    uv_path = Path(args.uv) if args.uv else None
    version_env = dict(os.environ)
    if uv_path is not None and uv_path.is_file():
        uv_version, uv_error = probe_uv_version(uv_path, version_env, args.work_root)
        record["uv_version"] = uv_version
        if uv_error:
            reasons.append(uv_error)
    else:
        reasons.append("uv binary is missing")
    reasons.extend(_reported_version_reasons(args))
    uv_python = os.environ.get("UV_PYTHON")
    if uv_python is not None and _version_token(uv_python) != EXPECTED_PYTHON:
        reasons.append(f"UV_PYTHON is {uv_python}, expected {EXPECTED_PYTHON}")

    tree_bad = _blocks_python_install(reasons)
    baseline_env = _as_env(None if baseline is None else baseline.get("env"))
    mark_env = _as_env(None if mark is None else mark.get("env"))
    if baseline_env is None or mark_env is None:
        if not any(reason.startswith("missing ") for reason in reasons):
            reasons.append("missing baseline environment")
        tree_bad = True
    if tree_bad or uv_path is None or not uv_path.is_file():
        return _mismatch(args, record, reasons, wall_start)
    assert baseline_env is not None
    assert mark_env is not None

    sample_dir = args.work_root / f"sample{args.index}"
    cache_dir = args.work_root / f"sample{args.index}-cache"
    venv_dir = args.work_root / f"sample{args.index}-venv"
    python_dir = args.work_root / f"sample{args.index}-python"
    junit_path = args.work_root / f"sample{args.index}-pytest.xml"
    compare_log = args.work_root / f"sample{args.index}-bigcompare.log"
    record["junit_path"] = str(junit_path)
    sample_env = build_sample_env(
        baseline=baseline_env,
        mark=mark_env,
        current=dict(os.environ),
        uv_path=uv_path,
        cache_dir=cache_dir,
        venv_dir=venv_dir,
        python_dir=python_dir,
        compare_log=compare_log,
    )
    record["controlled_path"] = [
        entry for entry in sample_env.get("PATH", "").split(os.pathsep) if entry
    ]

    copy_started = time.time()
    try:
        copy_app(args.app_source, sample_dir)
    except OSError as exc:
        record["copy_seconds"] = round(time.time() - copy_started, 3)
        reasons.append(f"copying the app tree failed ({exc.__class__.__name__})")
        return _mismatch(args, record, reasons, wall_start)
    record["copy_seconds"] = round(time.time() - copy_started, 3)
    copy_lock = sha256_file(sample_dir / "uv.lock")
    if copy_lock != source_lock:
        reasons.append("copied uv.lock hash does not match the app tree")
        return _mismatch(args, record, reasons, wall_start)

    cache_dir.mkdir(parents=True, exist_ok=True)
    python_dir.mkdir(parents=True, exist_ok=True)
    python_version, interpreter, install_seconds, python_error = (
        install_and_probe_python(
            uv_path,
            sample_env,
            sample_dir,
            args.work_root / f"sample{args.index}-probe.py",
        )
    )
    record["python_install_seconds"] = round(install_seconds, 3)
    record["python_version"] = python_version
    record["interpreter_path"] = interpreter
    if python_error:
        reasons.append(python_error)
    if reasons:
        return _mismatch(args, record, reasons, wall_start)

    record["launched"] = True
    sync_code, _sync_output, sync_seconds = stream_command(
        [str(uv_path), "sync"], sample_env, sample_dir
    )
    record["sync_seconds"] = round(sync_seconds, 3)
    record["exit_codes"]["sync"] = sync_code
    pytest_code: int | None = None
    clib_code: int | None = None
    test_seconds = 0.0
    if sync_code == 0:
        pytest_code, pytest_output, pytest_seconds = stream_command(
            [
                str(uv_path),
                "run",
                "pytest",
                "-rfEP",
                f"--junitxml={junit_path}",
            ],
            sample_env,
            sample_dir,
        )
        if pytest_code == 0:
            clib_started = time.time()
            clib_code = run_clib_guard(junit_path)
            test_seconds = pytest_seconds + (time.time() - clib_started)
        else:
            test_seconds = pytest_seconds
        record["pytest"] = _counts_for(pytest_output, junit_path)
    record["test_seconds"] = round(test_seconds, 3) if sync_code == 0 else None
    record["exit_codes"]["pytest"] = pytest_code
    record["exit_codes"]["clib"] = clib_code
    failed = (
        sync_code != 0 or pytest_code not in (0, None) or clib_code not in (0, None)
    )
    record["status"] = "failed" if failed else "ok"
    if failed:
        record["notes"] = ["uv sync or pytest or the clib guard failed"]
    return _finish(args.output, record, 1 if failed else 0, wall_start)


def cmd_summarize(args: argparse.Namespace) -> int:
    records, read_error = _read_jsonl(args.input)
    identity, identity_error = _read_json(args.identity)
    failures = summary_failures(records, identity, read_error, identity_error)
    text = summary_markdown(records, identity, failures)
    print(text, flush=True)
    destination = args.summary
    if destination is None and os.environ.get("GITHUB_STEP_SUMMARY"):
        destination = Path(os.environ["GITHUB_STEP_SUMMARY"])
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
    return 1 if failures else 0


def static_pin_reasons(
    index: int,
    variant: str,
    action_sha: str,
    expected_sha: str,
    expected_uv: str,
    expected_python: str,
) -> list[str]:
    reasons = _constant_pin_reasons(expected_sha, expected_uv, expected_python)
    if index < 1 or index > len(SAMPLE_ORDER):
        reasons.append(f"sample index {index} is outside 1..{len(SAMPLE_ORDER)}")
        return reasons
    expected = SAMPLE_ORDER[index - 1]
    if variant != expected:
        reasons.append(
            f"sample {index} variant {variant} does not match fixed order {expected}"
        )
        return reasons
    if action_sha != ACTION_SHA[variant]:
        reasons.append(f"action SHA for variant {variant} does not match the pin")
    return reasons


def _constant_pin_reasons(
    expected_sha: str, expected_uv: str, expected_python: str
) -> list[str]:
    reasons: list[str] = []
    if expected_sha != FROZEN_APP_SHA:
        reasons.append("expected app SHA is not the frozen commit")
    if expected_uv != EXPECTED_UV:
        reasons.append(f"expected uv {expected_uv} is not {EXPECTED_UV}")
    if expected_python != EXPECTED_PYTHON:
        reasons.append(f"expected Python {expected_python} is not {EXPECTED_PYTHON}")
    return reasons


def _identity_reasons(
    observed: str | None,
    source_lock: str | None,
    identity: Mapping[str, object] | None,
) -> list[str]:
    reasons: list[str] = []
    if identity is None:
        reasons.append("identity record is missing")
        return reasons
    if observed is None:
        reasons.append("git rev-parse HEAD failed for the app tree")
    elif observed != FROZEN_APP_SHA:
        reasons.append(f"app SHA {observed} != {FROZEN_APP_SHA}")
    if identity.get("app_sha") != FROZEN_APP_SHA:
        reasons.append("recorded app SHA is not the frozen commit")
    if source_lock is None:
        reasons.append("uv.lock is missing from the app tree")
    elif source_lock != identity.get("lock_hash"):
        reasons.append("uv.lock hash does not match the hash recorded at start")
    return reasons


def _reported_version_reasons(args: argparse.Namespace) -> list[str]:
    reasons: list[str] = []
    reported_uv = _version_token(args.reported_uv_version)
    reported_python = _version_token(args.reported_python_version)
    if reported_uv != EXPECTED_UV:
        shown = _short_text(args.reported_uv_version)
        reasons.append(f"setup-uv reported uv {shown}, expected {EXPECTED_UV}")
    if reported_python != EXPECTED_PYTHON:
        shown = _short_text(args.reported_python_version)
        reasons.append(f"setup-uv reported Python {shown}, expected {EXPECTED_PYTHON}")
    return reasons


def _short_text(value: str) -> str:
    text = value.strip() or "(empty)"
    text = " ".join(text.split())
    if len(text) > 80:
        return text[:77] + "..."
    return text


def _blocks_python_install(reasons: Sequence[str]) -> bool:
    """A bad uv or a bad tree cannot produce a meaningful interpreter probe."""
    for reason in reasons:
        if reason.startswith("uv ") or reason.startswith("uv binary"):
            return True
        if "app SHA" in reason or "uv.lock" in reason or "identity" in reason:
            return True
        if reason.startswith("action SHA") or reason.startswith("sample "):
            return True
        if reason.startswith("expected "):
            return True
        if reason.startswith("missing "):
            return True
    return False


def _mismatch(
    args: argparse.Namespace,
    record: dict[str, object],
    reasons: list[str],
    wall_start: float,
) -> int:
    del wall_start
    record["mismatch"] = True
    record["mismatch_reasons"] = reasons
    record["status"] = "mismatch"
    record["notes"] = reasons
    record["launched"] = False
    _write_halt(args.state_dir, "; ".join(reasons))
    print("mismatch: " + "; ".join(reasons), flush=True)
    return _finish(args.output, record, 1)


def _finish(
    output: Path,
    record: dict[str, object],
    code: int,
    mark_fallback: float | None = None,
) -> int:
    end = time.time()
    record["end"] = _iso(end)
    exits = record["exit_codes"]
    if isinstance(exits, dict):
        exits["sample"] = code
    if record.get("start") is None:
        record["start"] = _iso(mark_fallback if mark_fallback is not None else end)
    start_text = record.get("start")
    if isinstance(start_text, str) and record.get("total_seconds") is None:
        record["total_seconds"] = round(end - _from_iso(start_text), 3)
    append_jsonl(output, record)
    print(
        f"sample {record['index']} variant {record['variant']} "
        f"status {record['status']}",
        flush=True,
    )
    return code


def git_head(source: Path) -> str | None:
    code, stdout, _stderr = capture_command(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        os.environ,
        source if source.is_dir() else Path("."),
    )
    if code != 0:
        return None
    sha = stdout.strip()
    if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
        return None
    return sha


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_uv_version(
    uv: Path, env: Mapping[str, str], cwd: Path
) -> tuple[str | None, str | None]:
    cwd.mkdir(parents=True, exist_ok=True)
    code, stdout, stderr = capture_command([str(uv), "--version"], env, cwd)
    line = next((item.strip() for item in stdout.splitlines() if item.strip()), "")
    match = _UV_VERSION_LINE.match(line)
    if code != 0 or match is None:
        detail = line or stderr.strip() or f"exit {code}"
        return None, f"uv --version failed ({detail})"
    version = match.group("version")
    if version != EXPECTED_UV:
        return version, f"uv version {version} != {EXPECTED_UV}"
    return version, None


def install_and_probe_python(
    uv: Path,
    env: Mapping[str, str],
    cwd: Path,
    probe_path: Path,
) -> tuple[str | None, str | None, float, str | None]:
    started = time.time()
    install_code, _output, _elapsed = stream_command(
        [str(uv), "python", "install", EXPECTED_PYTHON], env, cwd
    )
    if install_code != 0:
        return (
            None,
            None,
            time.time() - started,
            f"uv python install exited {install_code}",
        )
    find_code, stdout, stderr = capture_command(
        [str(uv), "python", "find", EXPECTED_PYTHON], env, cwd
    )
    interpreter = next(
        (line.strip() for line in stdout.splitlines() if line.strip()), ""
    )
    if find_code != 0 or not interpreter:
        detail = stderr.strip() or stdout.strip() or f"exit {find_code}"
        return None, None, time.time() - started, f"uv python find failed ({detail})"
    probe_path.write_text(_PROBE_SOURCE, encoding="utf-8")
    probe_code, probe_out, probe_err = capture_command(
        [interpreter, str(probe_path)], env, cwd
    )
    lines = [line.strip() for line in probe_out.splitlines() if line.strip()]
    version = lines[0] if lines else None
    executable = lines[1] if len(lines) > 1 else interpreter
    elapsed = time.time() - started
    if probe_code != 0 or version is None:
        detail = probe_err.strip() or probe_out.strip() or f"exit {probe_code}"
        return version, executable, elapsed, f"Python probe failed ({detail})"
    if version != EXPECTED_PYTHON:
        return (
            version,
            executable,
            elapsed,
            f"Python version {version} != {EXPECTED_PYTHON}",
        )
    return version, executable, elapsed, None


def copy_app(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination, symlinks=True, ignore=_ignore_sample_junk)
    _make_writable(destination)
    hypothesis = destination / ".hypothesis"
    if hypothesis.exists():
        shutil.rmtree(hypothesis)


def _ignore_sample_junk(directory: str, names: list[str]) -> set[str]:
    del directory
    ignored = {name for name in names if name in _SKIP_NAMES}
    ignored.update(name for name in names if name.endswith(".pyc"))
    return ignored


def _make_writable(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if current.is_symlink():
            continue
        os.chmod(current, current.stat().st_mode | 0o700)
        for name in filenames:
            path = current / name
            if path.is_symlink():
                continue
            os.chmod(path, path.stat().st_mode | 0o600)


def build_sample_env(
    *,
    baseline: Mapping[str, str],
    mark: Mapping[str, str],
    current: Mapping[str, str],
    uv_path: Path,
    cache_dir: Path,
    venv_dir: Path,
    python_dir: Path,
    compare_log: Path,
) -> dict[str, str]:
    env = {
        key: value
        for key, value in baseline.items()
        if key != "GITHUB_PATH" and not _is_sensitive(key)
    }
    for key in set(mark) | set(current):
        if key in _OVERRIDDEN or _is_sensitive(key) or not _is_forwardable(key):
            continue
        if mark.get(key) == current.get(key):
            continue
        after = current.get(key)
        if after is None:
            env.pop(key, None)
        else:
            env[key] = after
    added = path_additions(mark.get("PATH", ""), current.get("PATH", ""))
    uv_dir = str(uv_path.resolve().parent)
    prefix: list[str] = []
    for entry in added:
        if entry not in prefix:
            prefix.append(entry)
    if uv_dir not in prefix:
        prefix.insert(0, uv_dir)
    baseline_entries = [
        entry for entry in baseline.get("PATH", "").split(os.pathsep) if entry
    ]
    env["PATH"] = os.pathsep.join([*prefix, *baseline_entries])
    env["UV_CACHE_DIR"] = str(cache_dir)
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
    env["UV_PYTHON_INSTALL_DIR"] = str(python_dir)
    env["UV_PYTHON"] = EXPECTED_PYTHON
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["GALLEY_BIGCOMPARE_LOG"] = str(compare_log)
    env.pop("GITHUB_PATH", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def path_additions(before: str, after: str) -> list[str]:
    before_entries = {entry for entry in before.split(os.pathsep) if entry}
    added: list[str] = []
    for entry in after.split(os.pathsep):
        if entry and entry not in before_entries and entry not in added:
            added.append(entry)
    return added


def env_diff(mark: Mapping[str, str], current: Mapping[str, str]) -> dict[str, object]:
    added: dict[str, object] = {}
    removed: dict[str, object] = {}
    changed: dict[str, object] = {}
    redacted: list[str] = []
    unforwarded: list[str] = []
    for key in sorted(set(mark) | set(current)):
        before = mark.get(key)
        after = current.get(key)
        if before == after:
            continue
        if _is_sensitive(key):
            redacted.append(key)
            continue
        if key == "PATH":
            payload: object = {
                "before": _path_entries(before or ""),
                "after": _path_entries(after or ""),
            }
            changed[key] = payload
            continue
        displayed_before = _display_env_value(key, before)
        displayed_after = _display_env_value(key, after)
        if not _is_forwardable(key) and key not in _OVERRIDDEN:
            unforwarded.append(key)
            displayed_before = "<redacted>" if before is not None else None
            displayed_after = "<redacted>" if after is not None else None
        if before is None:
            added[key] = displayed_after
        elif after is None:
            removed[key] = displayed_before
        else:
            changed[key] = {"before": displayed_before, "after": displayed_after}
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "redacted_keys": redacted,
        "unforwarded_keys": unforwarded,
    }


def _display_env_value(key: str, value: str | None) -> str | None:
    del key
    if value is None:
        return None
    return _redact_text(value)


def _path_entries(value: str) -> list[str]:
    return [_redact_text(entry) for entry in value.split(os.pathsep) if entry]


def _redact_text(value: str) -> str:
    if any(marker in value for marker in _TOKEN_MARKERS):
        return "<redacted>"
    return value


def _as_env(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        cleaned[key] = item
    return cleaned


def _is_sensitive(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in _SENSITIVE_PARTS)


def _is_forwardable(key: str) -> bool:
    if _is_sensitive(key) or key in _OVERRIDDEN:
        return False
    return key.startswith("UV_")


def parse_pytest_summary(output: str) -> dict[str, int] | None:
    match: re.Match[str] | None = None
    for line in output.splitlines():
        found = _SUMMARY_LINE.match(line.strip())
        if found and _SUMMARY_COUNT.search(found.group("body")):
            match = found
    if match is None:
        return None
    counts = {
        "pass": 0,
        "skip": 0,
        "deselect": 0,
        "fail": 0,
        "xfail": 0,
        "error": 0,
        "xpass": 0,
    }
    for item in _SUMMARY_COUNT.finditer(match.group("body")):
        label = _SUMMARY_LABELS.get(item.group("label"))
        if label is None:
            continue
        counts[label] += int(item.group("count"))
    return counts


def parse_junit_counts(path: Path) -> dict[str, int]:
    counts = {"pass": 0, "skip": 0, "fail": 0, "xfail": 0, "error": 0, "xpass": 0}
    tree = ET.parse(path)
    for case in tree.iter("testcase"):
        if case.find("failure") is not None:
            counts["fail"] += 1
        elif case.find("error") is not None:
            counts["error"] += 1
        else:
            skipped = case.find("skipped")
            if skipped is None:
                counts["pass"] += 1
            elif _is_xfail(skipped):
                counts["xfail"] += 1
            else:
                counts["skip"] += 1
    return counts


def clib_skip_names(path: Path) -> list[str]:
    tree = ET.parse(path)
    skipped: list[str] = []
    for case in tree.iter("testcase"):
        for node in case.findall("skipped"):
            message = node.get("message") or (node.text or "")
            if CLIB_MESSAGE in message:
                skipped.append(f"{case.get('classname')}::{case.get('name')}")
    return skipped


def run_clib_guard(path: Path) -> int:
    if not path.is_file():
        print(f"junit file is missing: {path.name}", flush=True)
        return 1
    try:
        skipped = clib_skip_names(path)
    except ET.ParseError as exc:
        print(f"could not parse junit XML ({exc})", flush=True)
        return 1
    if skipped:
        print("clib tests skipped on macOS:", flush=True)
        print("\n".join(skipped), flush=True)
        return 1
    print("clib-only tests ran", flush=True)
    return 0


def _counts_for(pytest_output: str, junit_path: Path) -> dict[str, int | None]:
    summary = parse_pytest_summary(pytest_output)
    junit: dict[str, int] | None = None
    if junit_path.is_file():
        try:
            junit = parse_junit_counts(junit_path)
        except ET.ParseError:
            junit = None
    counts: dict[str, int | None] = {
        "pass": None,
        "skip": None,
        "deselect": None,
        "fail": None,
        "xfail": None,
        "error": None,
        "xpass": None,
    }
    if summary is not None:
        counts.update(summary)
    if junit is not None:
        for key, value in junit.items():
            if counts.get(key) is None:
                counts[key] = value
    return counts


def _is_xfail(node: ET.Element) -> bool:
    kind = (node.get("type") or "").lower()
    message = (node.get("message") or (node.text or "")).lower()
    return (
        "xfail" in kind or message.startswith("xfail") or message.startswith("[xfail]")
    )


def summary_failures(
    records: list[dict[str, object]],
    identity: Mapping[str, object] | None,
    read_error: str | None,
    identity_error: str | None,
) -> list[str]:
    failures: list[str] = []
    if read_error:
        failures.append(read_error)
    if identity_error:
        failures.append(identity_error)
    elif identity is None:
        failures.append("identity record is missing")
    else:
        if identity.get("mismatch") is True:
            failures.append("app identity mismatched")
        if identity.get("app_sha") != FROZEN_APP_SHA:
            failures.append("recorded app SHA is not the frozen commit")
        if not identity.get("lock_hash"):
            failures.append("recorded lock hash is missing")
    if len(records) != len(SAMPLE_ORDER):
        failures.append(f"expected {len(SAMPLE_ORDER)} samples, found {len(records)}")
    seen: set[int] = set()
    locks: set[object] = set()
    for record in records:
        index = record.get("index")
        variant = record.get("variant")
        status = record.get("status")
        if not isinstance(index, int):
            failures.append("a sample record has no index")
            continue
        seen.add(index)
        expected = SAMPLE_ORDER[index - 1] if 1 <= index <= len(SAMPLE_ORDER) else None
        if variant != expected:
            failures.append(f"sample {index} variant is {variant}, expected {expected}")
        if status == "mismatch":
            failures.append(f"sample {index} pin check mismatched")
        elif status == "skipped":
            failures.append(f"sample {index} did not launch")
        elif status == "failed":
            failures.append(f"sample {index} failed")
        elif status != "ok":
            failures.append(f"sample {index} status {status} is not a result")
        if status in {"ok", "failed"}:
            failures.extend(_launched_sample_failures(record))
            locks.add(record.get("lock_hash"))
        if record.get("mismatch") is True and status != "mismatch":
            failures.append(f"sample {index} recorded a mismatch")
    for index in range(1, len(SAMPLE_ORDER) + 1):
        if index not in seen:
            failures.append(f"sample {index} is missing")
    if len(locks) > 1:
        failures.append("samples recorded more than one uv.lock hash")
    return failures


def _launched_sample_failures(record: Mapping[str, object]) -> list[str]:
    index = record.get("index")
    failures: list[str] = []
    if record.get("uv_version") != EXPECTED_UV:
        failures.append(f"sample {index} uv version is {record.get('uv_version')}")
    if record.get("python_version") != EXPECTED_PYTHON:
        failures.append(
            f"sample {index} Python version is {record.get('python_version')}"
        )
    if record.get("app_sha") != FROZEN_APP_SHA:
        failures.append(f"sample {index} app SHA does not match the frozen commit")
    if record.get("action_sha") != ACTION_SHA.get(str(record.get("variant"))):
        failures.append(f"sample {index} action SHA does not match its variant")
    if not record.get("interpreter_path"):
        failures.append(f"sample {index} is missing an interpreter path")
    if not record.get("lock_hash"):
        failures.append(f"sample {index} is missing a lock hash")
    exits = record.get("exit_codes")
    if not isinstance(exits, dict):
        failures.append(f"sample {index} is missing exit codes")
        return failures
    if exits.get("sync") != 0:
        failures.append(f"sample {index} uv sync exited {exits.get('sync')}")
    elif exits.get("pytest") != 0:
        failures.append(f"sample {index} pytest exited {exits.get('pytest')}")
    elif exits.get("clib") != 0:
        failures.append(f"sample {index} clib guard exited {exits.get('clib')}")
    return failures


def summary_markdown(
    records: Sequence[Mapping[str, object]],
    identity: Mapping[str, object] | None,
    failures: Sequence[str],
) -> str:
    app_sha = identity.get("app_sha") if identity else None
    lock_hash = identity.get("lock_hash") if identity else None
    image = next(
        (
            (
                f"{record.get('runner_image_os') or ''} "
                f"{record.get('runner_image_version') or ''}"
            ).strip()
            for record in records
            if record.get("runner_image_version") or record.get("runner_image_os")
        ),
        "",
    )
    lines = [
        "## setup-uv pin diagnostic",
        "",
        f"Frozen app `{app_sha}`",
        "",
        f"uv.lock sha256 `{lock_hash}`",
        "",
        f"Runner image `{image or 'unknown'}`",
        "",
        (
            "| # | Variant | Setup s | Sync s | Test s | Total s "
            "| Pass | Skip | Deselect | Fail | Xfail | Status |"
        ),
        (
            "| --- | --- | ---: | ---: | ---: | ---: "
            "| ---: | ---: | ---: | ---: | ---: | --- |"
        ),
    ]
    for record in records:
        pytest = record.get("pytest")
        counts = pytest if isinstance(pytest, dict) else {}
        lines.append(
            (
                "| {index} | {variant} | {setup} | {sync} | {test} | {total} "
                "| {passed} | {skip} | {deselect} | {fail} | {xfail} | {status} |"
            ).format(
                index=record.get("index"),
                variant=record.get("variant"),
                setup=_cell(record.get("setup_seconds")),
                sync=_cell(record.get("sync_seconds")),
                test=_cell(record.get("test_seconds")),
                total=_cell(record.get("total_seconds")),
                passed=_cell(counts.get("pass")),
                skip=_cell(counts.get("skip")),
                deselect=_cell(counts.get("deselect")),
                fail=_cell(counts.get("fail")),
                xfail=_cell(counts.get("xfail")),
                status=_cell(record.get("status")),
            )
        )
    lines.append("")
    if failures:
        lines.append("Result: failed")
        lines.append("")
        for failure in failures:
            lines.append(f"- {_cell(failure)}")
    else:
        lines.append(
            "Result: six samples matched the pinned uv, Python, app SHA, and lock."
        )
    lines.append("")
    return "\n".join(lines)


def _cell(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).replace("|", "/").replace("\n", " ")
    return text


def capture_command(
    command: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def stream_command(
    command: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
) -> tuple[int, str, float]:
    print("+ " + " ".join(command), flush=True)
    started = time.time()
    chunks: list[str] = []
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        print(f"command failed to start ({exc.__class__.__name__})", flush=True)
        return 127, str(exc), time.time() - started
    if process.stdout is None:
        return 127, "", time.time() - started
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        chunks.append(line)
    return process.wait(), "".join(chunks), time.time() - started


def append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    _write_json(path, payload)
    os.chmod(path, 0o600)


def _write_halt(state_dir: Path, reason: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / HALT_NAME
    if not path.exists():
        path.write_text(reason + "\n", encoding="utf-8")


def _load_sample_state(
    args: argparse.Namespace,
) -> tuple[
    dict[str, object] | None,
    dict[str, object] | None,
    dict[str, object] | None,
    list[str],
]:
    reasons: list[str] = []
    mark = _read_snapshot(args.state_dir / f"mark-{args.index}.json")
    baseline = _read_snapshot(args.state_dir / "baseline.json")
    identity, error = _read_json(args.state_dir / "identity.json")
    if mark is None:
        reasons.append("missing setup marker")
    if baseline is None:
        reasons.append("missing baseline environment")
    if error or identity is None:
        reasons.append("missing identity record")
    return mark, baseline, identity, reasons


def _read_snapshot(path: Path) -> dict[str, object] | None:
    payload, error = _read_json(path)
    if error or payload is None:
        return None
    env = payload.get("env")
    if not isinstance(env, dict):
        return None
    cleaned = {str(key): value for key, value in env.items() if isinstance(value, str)}
    payload["env"] = cleaned
    return payload


def _read_json(path: Path) -> tuple[dict[str, object] | None, str | None]:
    if not path.is_file():
        return None, f"{path.name} is missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, f"{path.name} is not JSON"
    if not isinstance(payload, dict):
        return None, f"{path.name} is not an object"
    return payload, None


def _read_jsonl(path: Path) -> tuple[list[dict[str, object]], str | None]:
    if not path.is_file():
        return [], f"{path.name} is missing"
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return records, f"samples.jsonl line {line_number} is not JSON"
        if not isinstance(payload, dict):
            return records, f"samples.jsonl line {line_number} is not an object"
        records.append(payload)
    return records, None


def _base_record(index: int, variant: str, action_sha: str) -> dict[str, object]:
    return {
        "action_sha": action_sha,
        "app_sha": None,
        "controlled_path": None,
        "copy_seconds": None,
        "end": None,
        "env_diff": None,
        "exit_codes": {"clib": None, "pytest": None, "sample": None, "sync": None},
        "index": index,
        "interpreter_path": None,
        "junit_path": None,
        "launched": False,
        "lock_hash": None,
        "mismatch": False,
        "mismatch_reasons": [],
        "notes": [],
        "pytest": {
            "deselect": None,
            "error": None,
            "fail": None,
            "pass": None,
            "skip": None,
            "xfail": None,
            "xpass": None,
        },
        "python_install_seconds": None,
        "python_version": None,
        "reported_python_version": None,
        "reported_uv_version": None,
        "runner_image_os": None,
        "runner_image_version": None,
        "runner_os": None,
        "setup_seconds": None,
        "start": None,
        "status": "skipped",
        "sync_seconds": None,
        "test_seconds": None,
        "total_seconds": None,
        "uv_version": None,
        "variant": variant,
    }


def _version_token(value: str) -> str:
    token = value.strip()
    if token.startswith("v") and len(token) > 1 and token[1].isdigit():
        return token[1:]
    return token


def _iso(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _from_iso(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.timestamp()


if __name__ == "__main__":
    raise SystemExit(main())
