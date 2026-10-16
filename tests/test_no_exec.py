"""Grep the tree for banned exec and eval builtins."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "src", ROOT / "tests")

# Qt's event loop is an attribute call. These patterns match the builtins only.
EXEC_CALL = re.compile(
    r"(?<![\w.])exec\s*\(|(?<!\w)(?:builtins|__builtins__)\s*\.\s*exec\s*\("
)
EVAL_CALL = re.compile(
    r"(?<![\w.])eval\s*\(|(?<!\w)(?:builtins|__builtins__)\s*\.\s*eval\s*\("
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for scan_root in SCAN_ROOTS:
        files.extend(scan_root.rglob("*.py"))
    return files


def _code_without_comments(path: Path) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        code, _separator, _comment = line.partition("#")
        if code.strip():
            lines.append((lineno, code))
    return lines


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{lineno}"
        for path in _python_files()
        for lineno, code in _code_without_comments(path)
        if pattern.search(code)
    ]


def test_source_does_not_call_exec() -> None:
    assert _offenders(EXEC_CALL) == []


def test_source_does_not_call_eval() -> None:
    assert _offenders(EVAL_CALL) == []


def test_ruff_bans_eval_and_exec() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    select = config["tool"]["ruff"]["lint"]["select"]
    assert "S307" in select
    assert "S102" in select
