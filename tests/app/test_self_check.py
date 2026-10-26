"""``--self-check`` verifies ruamel.yaml.clib and expat, and opens no window."""

import pyexpat
import sys
from pathlib import Path

from ruamel.yaml.main import CParser

import galley.app.about as about
import galley.app.main as main_module
from galley.app.main import clib_self_check, main
from galley.core import xml_format


def _block_window(monkeypatch) -> None:
    def reject(*_args, **_kwargs):
        raise AssertionError("self-check created a window")

    monkeypatch.setattr(main_module, "QApplication", reject)
    monkeypatch.setattr(main_module, "create_main_window", reject)


_EXPAT_OK = f"expat self-check: ok {pyexpat.EXPAT_VERSION}\n"


def _assert_real_result(code: int, output: str) -> None:
    assert output.count("\n") == 2
    assert output.endswith(_EXPAT_OK)
    if CParser is None:
        assert code == 1
        assert output.startswith("ruamel.yaml.clib self-check: failed:")
        return
    assert code == 0
    assert output == "ruamel.yaml.clib self-check: ok\n" + _EXPAT_OK


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
        "ruamel.yaml.clib self-check: failed: C parser did not load\n" + _EXPAT_OK
    )


def test_self_check_fails_on_an_expat_older_than_2_6_0(capsys, monkeypatch) -> None:
    _block_window(monkeypatch)
    monkeypatch.setattr(main_module, "clib_self_check", lambda: None)
    monkeypatch.setattr(xml_format.expat, "version_info", (2, 5, 0))
    assert main(["galley", "--self-check"]) == 1
    assert capsys.readouterr().out == (
        "ruamel.yaml.clib self-check: ok\n"
        "expat self-check: failed: XML needs expat 2.6.0 or newer, "
        "and this Python has expat 2.5.0.\n"
    )


def _freeze(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "Galley"))


def test_frozen_self_check_names_the_license_folder(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    folder = tmp_path / "LICENSES"
    folder.mkdir()
    (folder / "Qt-LGPL-3.0-only.txt").write_text("license\n", encoding="utf-8")
    _freeze(monkeypatch, tmp_path)
    _block_window(monkeypatch)
    code = main(["galley", "--self-check"])
    output = capsys.readouterr().out
    assert f"LICENSES self-check: ok {folder.resolve()}\n" in output
    if CParser is None:
        assert code == 1
        return
    assert code == 0
    assert output.startswith("ruamel.yaml.clib self-check: ok\n")


def test_frozen_self_check_fails_when_the_folder_is_missing(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    origin = tmp_path / "pkg" / "app" / "about.py"
    origin.parent.mkdir(parents=True)
    monkeypatch.setattr(about, "__file__", str(origin))
    _freeze(monkeypatch, tmp_path / "bundle")
    _block_window(monkeypatch)
    code = main(["galley", "--self-check"])
    output = capsys.readouterr().out
    tried = tmp_path / "bundle" / "LICENSES"
    assert code == 1
    assert "LICENSES self-check: failed:" in output
    assert "LICENSES folder was not found" in output
    assert f"tried {tried}" in output


def test_frozen_self_check_fails_when_the_folder_has_no_license_files(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    folder = tmp_path / "LICENSES"
    folder.mkdir()
    (folder / "README.md").write_text("notes\n", encoding="utf-8")
    _freeze(monkeypatch, tmp_path)
    _block_window(monkeypatch)
    code = main(["galley", "--self-check"])
    output = capsys.readouterr().out
    assert code == 1
    assert "LICENSES folder has no license files" in output
    assert str(folder.resolve()) in output


def test_frozen_self_check_rejects_a_checkout_outside_the_app(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Galley"))
    _block_window(monkeypatch)
    code = main(["galley", "--self-check"])
    output = capsys.readouterr().out
    assert code == 1
    assert "LICENSES folder is outside the app" in output
    assert f"tried {tmp_path / 'LICENSES'}" in output


def test_missing_clib_names_the_import(monkeypatch) -> None:
    def missing(_name: str):
        raise ModuleNotFoundError("No module named '_ruamel_yaml'")

    monkeypatch.setattr(main_module.importlib, "import_module", missing)
    problem = clib_self_check()
    assert problem is not None
    assert problem.startswith("ruamel.yaml.clib did not import")
    assert "_ruamel_yaml" in problem
