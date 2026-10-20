"""The copyright gate is tested with a fake About module."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "check_copyright_notice.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release-macos.yml"
MODULE = "galley.app.about"


def _checker():
    spec = importlib.util.spec_from_file_location("check_copyright_notice", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install(monkeypatch: pytest.MonkeyPatch, notice: object) -> None:
    module = types.ModuleType(MODULE)
    module.COPYRIGHT_NOTICE = notice
    monkeypatch.setitem(sys.modules, MODULE, module)


def test_placeholder_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()
    _install(monkeypatch, "© 2026 [copyright holder TBD]")
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "galley.app.about.COPYRIGHT_NOTICE still contains TBD.\n"
    _install(monkeypatch, "copyright tbd")
    assert checker.main() == 1
    folded = capsys.readouterr()
    assert folded.err == ""
    assert folded.out == "galley.app.about.COPYRIGHT_NOTICE still contains TBD.\n"


def test_real_notice_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()
    _install(monkeypatch, "© 2026 Galley.")
    assert checker.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "Copyright notice: ok\n"


def test_empty_notice_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()
    _install(monkeypatch, "")
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "galley.app.about.COPYRIGHT_NOTICE is empty.\n"


def test_missing_attribute_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()
    module = types.ModuleType(MODULE)
    monkeypatch.setitem(sys.modules, MODULE, module)
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert captured.out == "galley.app.about.COPYRIGHT_NOTICE is missing.\n"


def test_missing_module_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()

    def explode(name: str, package: str | None = None) -> object:
        raise ModuleNotFoundError(f"No module named {name!r}")

    monkeypatch.setattr(checker.importlib, "import_module", explode)
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "could not import galley.app.about" in captured.out
    assert "COPYRIGHT_NOTICE" in captured.out


def test_copyright_step_follows_the_tag_check() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    tag_check = workflow.find("- name: Check tag matches version")
    notice = workflow.find("- name: Check copyright notice")
    build = workflow.find("- name: Build Galley.app")
    assert tag_check != -1
    assert tag_check < notice < build
    block = workflow[notice:build]
    assert "if: startsWith(github.ref, 'refs/tags/v')" in block
    assert "packaging/check_copyright_notice.py" in block
    assert "uv run --frozen --no-dev --no-editable python" in block
