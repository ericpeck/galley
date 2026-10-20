"""The release version check compares strings and does not need macOS."""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "check_release_version.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release-macos.yml"
SPEC = ROOT / "packaging" / "galley.spec"


def _checker():
    spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matching_versions_pass() -> None:
    checker = _checker()
    assert checker.failure_message("0.1.0", "0.1.0", "0.1.0") is None


def test_tag_mismatch_names_every_value_and_the_fix() -> None:
    checker = _checker()
    message = checker.failure_message("0.1.0", "0.1.0", "0.2.0")
    assert message == "\n".join(
        [
            "Check release version failed.",
            "galley.__version__ is 0.1.0",
            "CFBundleShortVersionString is 0.1.0",
            "tag is 0.2.0",
            "Bump galley.__version__ or re-tag.",
        ]
    )


def test_plist_mismatch_names_every_value_and_the_fix() -> None:
    checker = _checker()
    message = checker.failure_message("0.1.0", "0.9.0", "0.1.0")
    assert message == "\n".join(
        [
            "Check release version failed.",
            "galley.__version__ is 0.1.0",
            "CFBundleShortVersionString is 0.9.0",
            "tag is 0.1.0",
            "Bump galley.__version__ or re-tag.",
        ]
    )


def test_no_plist_compares_only_the_tag() -> None:
    checker = _checker()
    assert checker.failure_message("0.1.0", None, "0.1.0") is None
    message = checker.failure_message("0.1.0", None, "0.2.0")
    assert message == "\n".join(
        [
            "Check release version failed.",
            "galley.__version__ is 0.1.0",
            "tag is 0.2.0",
            "Bump galley.__version__ or re-tag.",
        ]
    )
    assert "CFBundleShortVersionString" not in message


def test_main_no_plist_reports_a_tag_mismatch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _checker()
    status = checker.main(["--package", "0.1.0", "--tag", "0.2.0"])
    assert status == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "CFBundleShortVersionString" not in captured.out
    assert "galley.__version__ is 0.1.0" in captured.out
    assert "tag is 0.2.0" in captured.out
    assert "Bump galley.__version__ or re-tag." in captured.out


def test_main_no_plist_match_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    checker = _checker()
    assert checker.main(["--package", "0.1.0", "--tag", "0.1.0"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "CFBundleShortVersionString" not in captured.out


def test_main_requires_plist_or_tag(capsys: pytest.CaptureFixture[str]) -> None:
    checker = _checker()
    with pytest.raises(SystemExit) as raised:
        checker.main(["--package", "0.1.0"])
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert "pass --plist, --tag, or both" in captured.err


def test_non_tag_run_skips_the_tag_comparison() -> None:
    checker = _checker()
    assert checker.failure_message("0.1.0", "0.1.0", None) is None
    skipped = checker.failure_message("0.1.0", "0.9.0", None)
    assert skipped is not None
    assert "tag is" not in skipped
    assert "CFBundleShortVersionString is 0.9.0" in skipped
    compared = checker.failure_message("0.1.0", "0.1.0", "9.9.9")
    assert compared is not None
    assert "tag is 9.9.9" in compared


def test_main_reports_a_tag_mismatch(capsys: pytest.CaptureFixture[str]) -> None:
    checker = _checker()
    status = checker.main(["--package", "0.1.0", "--plist", "0.1.0", "--tag", "0.2.0"])
    assert status == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Check release version failed." in captured.out
    assert "Bump galley.__version__ or re-tag." in captured.out


def test_main_non_tag_match_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    checker = _checker()
    assert checker.main(["--package", "0.1.0", "--plist", "0.1.0"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "tag is" not in captured.out


def test_spec_sets_short_version_from_package_version() -> None:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
    imported = False
    versions: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "galley":
            imported = any(alias.name == "__version__" for alias in node.names)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "BUNDLE":
            continue
        for keyword in node.keywords:
            if keyword.arg == "version":
                versions.append(keyword.value)
    assert imported
    assert len(versions) == 1
    version = versions[0]
    assert isinstance(version, ast.Name)
    assert version.id == "__version__"


def test_check_release_version_step_follows_the_build() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    install = workflow.find("- name: Install Galley")
    tag_check = workflow.find("- name: Check tag matches version")
    copyright_check = workflow.find("- name: Check copyright notice")
    unbuilt = workflow.find("- name: Check unbuilt actions")
    build = workflow.find("- name: Build Galley.app")
    check = workflow.find("- name: Check release version")
    bundle = workflow.find("- name: Check the app bundle")
    assert install != -1
    assert install < tag_check < copyright_check < unbuilt < build < check < bundle
    early = workflow[tag_check:copyright_check]
    assert "if: startsWith(github.ref, 'refs/tags/v')" in early
    assert "packaging/check_release_version.py" in early
    assert '--package "${package}"' in early
    assert '--tag "${GITHUB_REF_NAME#v}"' in early
    assert "--plist" not in early
    assert "CFBundleShortVersionString" not in early
    assert (
        "uv run --frozen --no-dev --no-editable python -c "
        "'from galley import __version__; print(__version__)'"
    ) in early
    block = workflow[check:bundle]
    assert (
        "uv run --frozen --no-dev --no-editable python -c "
        "'from galley import __version__; print(__version__)'"
    ) in block
    assert "CFBundleShortVersionString" in block
    assert "dist/Galley.app/Contents/Info.plist" in block
    assert "/usr/libexec/PlistBuddy" in block
    assert '[[ "${GITHUB_REF}" == refs/tags/v* ]]' in block
    assert '"${GITHUB_REF_NAME#v}"' in block
    assert "packaging/check_release_version.py" in block
