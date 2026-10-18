"""``--self-check`` verifies ruamel.yaml.clib and does not open a window."""

import sys

from ruamel.yaml.main import CParser

import galley.app.main as main_module
from galley.app.main import clib_self_check, main


def _block_window(monkeypatch) -> None:
    def reject(*_args, **_kwargs):
        raise AssertionError("self-check created a window")

    monkeypatch.setattr(main_module, "QApplication", reject)
    monkeypatch.setattr(main_module, "create_main_window", reject)


def _assert_real_result(code: int, output: str) -> None:
    assert output.count("\n") == 1
    if CParser is None:
        assert code == 1
        assert output.startswith("ruamel.yaml.clib self-check: failed:")
        return
    assert code == 0
    assert output == "ruamel.yaml.clib self-check: ok\n"


def test_self_check_flag_skips_the_window(capsys, monkeypatch) -> None:
    _block_window(monkeypatch)
    code = main(["galley", "--self-check"])
    _assert_real_result(code, capsys.readouterr().out)


def test_self_check_reads_process_arguments(capsys, monkeypatch) -> None:
    _block_window(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["galley", "--self-check"])
    code = main()
    _assert_real_result(code, capsys.readouterr().out)


def test_self_check_failure_is_one_line(capsys, monkeypatch) -> None:
    _block_window(monkeypatch)
    monkeypatch.setattr(main_module, "clib_self_check", lambda: "C parser did not load")
    assert main(["galley", "--self-check"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "ruamel.yaml.clib self-check: failed: C parser did not load\n"
    )


def test_missing_clib_names_the_import(monkeypatch) -> None:
    def missing(_name: str):
        raise ModuleNotFoundError("No module named '_ruamel_yaml'")

    monkeypatch.setattr(main_module.importlib, "import_module", missing)
    problem = clib_self_check()
    assert problem is not None
    assert problem.startswith("ruamel.yaml.clib did not import")
    assert "_ruamel_yaml" in problem
