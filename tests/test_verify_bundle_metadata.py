"""The disk-image metadata check reads the spec and the built app."""

import importlib.util
import plistlib
import struct
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


def _floor(verifier) -> str:
    """The spec's ``MINIMUM_MACOS``, shared by the icon and Mach-O checks."""
    return verifier.expected_minimum(verifier.spec_path())


def _app(
    tmp_path: Path,
    *,
    plist: bool = True,
    minimum: str | None = "14.0",
    licenses: bool = True,
    license_file: bool = True,
    icon_key: str | None = "galley.icns",
    icon_file: str | None = "galley.icns",
) -> Path:
    app = tmp_path / "Galley.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    if plist:
        data: dict[str, str] = {"CFBundleIdentifier": "com.ericmercurypeck.galley"}
        if minimum is not None:
            data["LSMinimumSystemVersion"] = minimum
        if icon_key is not None:
            data["CFBundleIconFile"] = icon_key
        (contents / "Info.plist").write_bytes(plistlib.dumps(data))
    if licenses:
        folder = contents / "Resources" / "LICENSES"
        folder.mkdir(parents=True)
        if license_file:
            (folder / "README.md").write_text("licenses\n", encoding="utf-8")
    if icon_file is not None:
        resources = contents / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / icon_file).write_bytes(b"icns")
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
    minimum = verifier.expected_minimum(verifier.spec_path())
    icon_name = verifier.expected_icon_name(verifier.spec_path())
    app = _app(tmp_path, minimum=minimum)
    assert verifier.main([str(app)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        f"LSMinimumSystemVersion is {minimum!r} and "
        "Contents/Resources/LICENSES is present. "
        f"CFBundleIconFile is {icon_name!r}.\n"
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


def _macho(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def _arm64() -> bytes:
    return struct.pack("<III", 0xFEEDFACF, 0x0100000C, 0)


def _universal() -> bytes:
    blob = struct.pack(">II", 0xCAFEBABE, 2)
    blob += struct.pack(">IIIII", 0x01000007, 3, 0, 0, 0)
    blob += struct.pack(">IIIII", 0x0100000C, 0, 0, 0, 0)
    return blob


# minos is the deployment target. version under LC_BUILD_VERSION is the linker.
_AT_FLOOR = """\
Galley (architecture arm64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 14.0.0
      sdk 14.0
   ntools 1
     tool LD
  version 26.0
Load command 11
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 26.0
"""

_VERSION_MIN_LOW = """\
Galley (architecture x86_64):
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 10.15
"""

_BUILD_HIGH = """\
Galley (architecture arm64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 26.0
      sdk 26.0
   ntools 1
     tool LD
  version 1115.7.3
"""

_SOURCE_ONLY = """\
liblegacy.dylib (architecture arm64):
Load command 9
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 26.0
"""

_VERSION_MIN_HIGH = """\
liblegacy.dylib (architecture arm64):
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 15.1
      sdk 15.1
"""


def test_macho_at_or_below_the_floor_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _verifier()
    app = _app(tmp_path, minimum=_floor(verifier))
    _macho(app / "Contents" / "MacOS" / "Galley", _arm64())

    assert verifier.main([str(app)], run_vtool=lambda _args: _AT_FLOOR) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "26.0" not in captured.out


def test_macho_above_the_floor_lists_each_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(tmp_path, minimum=floor)
    _macho(app / "Contents" / "MacOS" / "Galley", _universal())
    _macho(app / "Contents" / "Frameworks" / "QtCore", _arm64())
    _macho(app / "Contents" / "Frameworks" / "liblegacy.dylib", _arm64())
    (app / "Contents" / "Resources" / "note.txt").write_text(
        "minos 26.0\n", encoding="utf-8"
    )
    calls: list[str] = []

    def run(args: list[str]) -> str:
        name = Path(args[-1]).name
        arch = args[args.index("-arch") + 1]
        calls.append(f"{name}:{args[0]}:{arch}")
        if name == "Galley" and arch == "arm64":
            return _BUILD_HIGH
        if name == "Galley" and arch == "x86_64":
            return _VERSION_MIN_LOW
        if name == "QtCore":
            return _AT_FLOOR
        if name == "liblegacy.dylib" and args[0] == "-show-build":
            return _SOURCE_ONLY
        if name == "liblegacy.dylib":
            return _VERSION_MIN_HIGH
        raise AssertionError(args)

    assert verifier.main([str(app)], run_vtool=run) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "Verify bundle metadata failed:\n"
        f"2 Mach-O files record a minimum macOS higher than {floor}:\n"
        f"Contents/Frameworks/liblegacy.dylib arm64 minos 15.1 floor {floor}\n"
        f"Contents/MacOS/Galley arm64 minos 26.0 floor {floor}\n"
        "pin dependencies lower or raise MINIMUM_MACOS in galley.spec\n"
    )
    assert "QtCore" not in captured.out
    assert "note.txt" not in captured.out
    assert "10.13" not in captured.out
    assert "Galley:-show-build:x86_64" in calls
    assert "Galley:-show-build:arm64" in calls
    assert "liblegacy.dylib:-show-version-min:arm64" in calls


def test_expected_icon_name_matches_the_spec() -> None:
    verifier = _verifier()
    assert verifier.expected_icon_name(verifier.spec_path()) == "galley.icns"


def test_missing_icon_key_names_the_expected_file(tmp_path: Path) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(tmp_path, minimum=floor, icon_key=None)
    assert verifier.bundle_problems(app, floor) == [
        "CFBundleIconFile is missing from "
        f"{app}/Contents/Info.plist; expected galley.icns"
    ]


def test_generic_icon_is_rejected(tmp_path: Path) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(
        tmp_path,
        minimum=floor,
        icon_key="icon-windowed.icns",
        icon_file="icon-windowed.icns",
    )
    assert verifier.bundle_problems(app, floor) == [
        "CFBundleIconFile is 'icon-windowed.icns', expected 'galley.icns'",
        f"Contents/Resources/galley.icns is missing from {app}",
    ]


def test_missing_icon_file_is_named(tmp_path: Path) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(tmp_path, minimum=floor, icon_file=None)
    assert verifier.bundle_problems(app, floor) == [
        f"Contents/Resources/galley.icns is missing from {app}"
    ]


def test_empty_icon_file_is_named(tmp_path: Path) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(tmp_path, minimum=floor)
    icon = app / "Contents" / "Resources" / "galley.icns"
    icon.write_bytes(b"")
    assert verifier.bundle_problems(app, floor) == [
        f"Contents/Resources/galley.icns in {app} is empty"
    ]


def test_non_icns_file_is_named(tmp_path: Path) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(tmp_path, minimum=floor)
    icon = app / "Contents" / "Resources" / "galley.icns"
    icon.write_bytes(b"not-an-icon")
    assert verifier.bundle_problems(app, floor) == [
        f"Contents/Resources/galley.icns in {app} is not an icns file"
    ]


def test_icon_stem_matches_the_icns_file(tmp_path: Path) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(tmp_path, minimum=floor, icon_key="galley")
    assert verifier.bundle_problems(app, floor) == []


def test_main_reports_a_generic_icon(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _verifier()
    floor = _floor(verifier)
    app = _app(
        tmp_path,
        minimum=floor,
        icon_key="icon-windowed.icns",
        icon_file="icon-windowed.icns",
    )
    assert verifier.main([str(app)]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "CFBundleIconFile is 'icon-windowed.icns'" in captured.out
    assert "expected 'galley.icns'" in captured.out
    assert "Contents/Resources/galley.icns is missing" in captured.out
