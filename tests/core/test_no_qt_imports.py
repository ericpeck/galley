"""Fail if anything under galley.core imports a Qt binding."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "src" / "galley" / "core"

BANNED_ROOTS = frozenset(
    {
        "PySide6",
        "PySide2",
        "PyQt6",
        "PyQt5",
        "shiboken6",
        "shiboken2",
        "qtpy",
        "Qt",
    }
)


def _dynamic_imports(node: ast.Call) -> list[tuple[int, str]]:
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return []
    if name not in {"__import__", "import_module"} or not node.args:
        return []
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return [(node.lineno, argument.value)]
    return [(node.lineno, "<non-literal import>")]


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Call):
            found.extend(_dynamic_imports(node))
    return found


def _is_banned(module: str) -> bool:
    if module == "<non-literal import>":
        return True
    root = module.split(".", 1)[0]
    return root in BANNED_ROOTS


def test_core_does_not_import_qt() -> None:
    offenders: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, module in _imported_modules(tree):
            if _is_banned(module):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {module}")
    assert offenders == []
