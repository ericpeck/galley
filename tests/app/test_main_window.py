"""Smoke tests for the Galley window and process start method."""

import ast
import subprocess
import sys
from pathlib import Path

from galley.app.main import create_main_window

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "galley" / "app"


def test_main_window_opens(qtbot) -> None:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()
    assert window.windowTitle() == "Galley"
    assert window.centralWidget() is None
    assert window.styleSheet() == ""


def test_configure_runtime_sets_spawn() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from galley.app.runtime import configure_runtime",
                    "import multiprocessing",
                    "configure_runtime()",
                    "print(multiprocessing.get_start_method())",
                ]
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "spawn"


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_entry_point_uses_freeze_support_and_spawn() -> None:
    runtime_tree = ast.parse((APP_ROOT / "runtime.py").read_text(encoding="utf-8"))
    configure_calls = [
        node
        for node in ast.walk(_function(runtime_tree, "configure_runtime"))
        if isinstance(node, ast.Call)
    ]
    assert any(
        _call_name(node) == "multiprocessing.freeze_support" for node in configure_calls
    )
    assert any(
        _call_name(node) == "multiprocessing.set_start_method"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "spawn"
        for node in configure_calls
    )

    main_tree = ast.parse((APP_ROOT / "main.py").read_text(encoding="utf-8"))
    main_body = _function(main_tree, "main").body
    if (
        main_body
        and isinstance(main_body[0], ast.Expr)
        and isinstance(main_body[0].value, ast.Constant)
        and isinstance(main_body[0].value.value, str)
    ):
        main_body = main_body[1:]
    first_statement = main_body[0]
    assert isinstance(first_statement, ast.Expr)
    assert isinstance(first_statement.value, ast.Call)
    assert _call_name(first_statement.value) == "configure_runtime"
