"""The disk-image metadata check reads the spec and the built app."""

import importlib.util
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "verify_bundle_metadata.py"


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_bundle_metadata", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app(
    tmp_path: Path,
    *,
    plist: bool = True,
    minimum: str | None = "14.0",
    licenses: bool = True,
    license_file: bool = True,
) -> Path:
    app = tmp_path / "Galley.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    if plist:
        data: dict[str, str] = {"CFBundleIdentifier": "com.ericmercurypeck.galley"}
        if minimum is not None:
            data["LSMinimumSystemVersion"] = minimum
        (contents / "Info.plist").write_bytes(plistlib.dumps(data))
    if licenses:
        folder = contents / "Resources" / "LICENSES"
        folder.mkdir(parents=True)
        if license_file:
            (folder / "README.md").write_text("licenses\n", encoding="utf-8")
    return app


def test_expected_minimum_matches_the_spec() -> None:
    verifier = _verifier()
    assert verifier.expected_minimum(verifier.spec_path()) == "14.0"


def test_matching_bundle_has_no_problems(tmp_path: Path) -> None:
    verifier = _verifier()
    assert verifier.bundle_problems(_app(tmp_path), "14.0") == []


def test_missing_plist_and_licenses_are_named(tmp_path: Path) -> None:
    verifier = _verifier()
    problems = verifier.bundle_problems(
        _app(tmp_path, plist=False, licenses=False),
        "14.0",
    )
    app = tmp_path / "Galley.app"
    assert problems == [
        f"Info.plist is missing from {app}",
        f"Contents/Resources/LICENSES is missing from {app}",
    ]


def test_missing_minimum_key_names_the_expected_value(tmp_path: Path) -> None:
    verifier = _verifier()
    app = _app(tmp_path, minimum=None)
    problems = verifier.bundle_problems(app, "14.0")
    assert problems == [
        "LSMinimumSystemVersion is missing from "
        f"{app}/Contents/Info.plist; expected 14.0"
    ]


def test_wrong_minimum_quotes_both_values(tmp_path: Path) -> None:
    verifier = _verifier()
    problems = verifier.bundle_problems(_app(tmp_path, minimum="11.0"), "14.0")
    assert problems == ["LSMinimumSystemVersion is '11.0', expected '14.0'"]


def test_empty_license_directory_is_named(tmp_path: Path) -> None:
    verifier = _verifier()
    app = _app(tmp_path, license_file=False)
    problems = verifier.bundle_problems(app, "14.0")
    assert problems == [f"Contents/Resources/LICENSES in {app} is empty"]


def test_main_prints_each_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _verifier()
    app = _app(tmp_path, minimum=None, licenses=False)
    assert verifier.main([str(app)]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("Verify bundle metadata failed:\n")
    assert "LSMinimumSystemVersion is missing" in captured.out
    assert "Contents/Resources/LICENSES is missing" in captured.out


def test_main_success_names_the_minimum(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _verifier()
    app = _app(tmp_path)
    assert verifier.main([str(app)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "LSMinimumSystemVersion is '14.0' and Contents/Resources/LICENSES is present.\n"
    )


def test_main_usage(capsys: pytest.CaptureFixture[str]) -> None:
    verifier = _verifier()
    assert verifier.main([]) == 2
    captured = capsys.readouterr()
    assert "usage: verify_bundle_metadata.py Galley.app" in captured.err


def test_license_readme_names_every_file_and_version() -> None:
    folder = ROOT / "LICENSES"
    readme = (folder / "README.md").read_text(encoding="utf-8")
    names = [path.name for path in folder.iterdir() if path.name != "README.md"]
    assert names
    for name in names:
        assert name in readme
    for version in (
        "3.12.10",
        "3.0.16",
        "2.7.1",
        "5.2.3",
        "2.5.1",
        "6.9.3",
        "0.19.1",
        "0.2.15",
        "6.22.3",
    ):
        assert version in readme
    assert (
        "https://download.qt.io/archive/qt/6.9/6.9.3/single/"
        "qt-everywhere-src-6.9.3.tar.xz"
    ) in readme
    assert (
        "https://download.qt.io/official_releases/QtForPython/pyside6/"
        "PySide6-6.9.3-src/pyside-setup-everywhere-src-6.9.3.tar.xz"
    ) in readme
