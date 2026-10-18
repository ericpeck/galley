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
        "LSMinimumSystemVersion is '14.0' and "
        "Contents/Resources/LICENSES is present.\n"
        "Mach-O minimums are at or below 14.0 (0 files checked).\n"
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


# Sample `vtool -show-build` / `-show-version-min` text. Indentation matches
# the macOS vtool layout: minos is the deployment target, and version under
# LC_BUILD_VERSION is the linker tool rather than the minimum OS.
SAMPLE_UNIVERSAL = """\
test (architecture x86_64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 12.0
      sdk 12.3
   ntools 1
     tool LD
  version 764.0
test (architecture arm64):
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

SAMPLE_NUMERIC_PLATFORM = """\
Galley (architecture arm64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 11.0
      sdk 14.4
   ntools 1
     tool 3
  version 26.0
Load command 11
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 26.0
Load command 12
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.15
      sdk 14.4
"""

SAMPLE_MIXED_ARCH = """\
lib (architecture x86_64):
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 14.4
lib (architecture arm64):
Load command 9
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 11.0
      sdk 14.4
   ntools 1
     tool LD
  version 1053.12
"""

SAMPLE_VERSION_MIN_ONLY = """\
plugin.dylib:
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.12
      sdk 10.14
"""

SAMPLE_SIMULATOR_AND_MACOS = """\
plugin (architecture arm64):
Load command 9
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOSSIMULATOR
    minos 26.0
      sdk 26.0
   ntools 1
     tool LD
  version 1.0
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 14.4
      sdk 14.4
   ntools 1
     tool LD
  version 1.0
"""

SAMPLE_BUILD_14_4_0 = """\
QtCore (architecture arm64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 14.4.0
      sdk 14.4
   ntools 1
     tool LD
  version 1115.7.3
"""

SAMPLE_SOURCE_ONLY = """\
liblegacy.dylib (architecture arm64):
Load command 9
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 26.0
"""

SAMPLE_VERSION_MIN_15_1 = """\
liblegacy.dylib (architecture arm64):
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 15.1
      sdk 15.1
"""

SAMPLE_VERSION_MIN_10_15 = """\
libold.dylib (architecture arm64):
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.15
      sdk 10.15
"""


def _arm64(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<III", 0xFEEDFACF, 0x0100000C, 0))


def test_version_compare_is_numeric() -> None:
    verifier = _verifier()
    assert verifier.version_is_above("26.0", "14.4")
    assert verifier.version_is_above("14.4.1", "14.4")
    assert verifier.version_is_above("10.15", "10.9")
    assert not verifier.version_is_above("14.4", "14.4")
    assert not verifier.version_is_above("14.4.0", "14.4")
    assert not verifier.version_is_above("11.0", "14.4")
    assert not verifier.version_is_above("10.9", "10.15")
    assert not verifier.version_is_above("10.15", "14.4")


def test_parse_vtool_samples() -> None:
    verifier = _verifier()
    universal = verifier.parse_vtool_output(SAMPLE_UNIVERSAL)
    assert verifier.chosen_minima(universal, "x86_64") == ["12.0"]
    assert verifier.chosen_minima(universal, "arm64") == ["26.0"]

    numeric = verifier.parse_vtool_output(SAMPLE_NUMERIC_PLATFORM)
    assert verifier.chosen_minima(numeric, "arm64") == ["11.0"]

    mixed = verifier.parse_vtool_output(SAMPLE_MIXED_ARCH)
    assert verifier.chosen_minima(mixed, "x86_64") == ["10.13"]
    assert verifier.chosen_minima(mixed, "arm64") == ["11.0"]

    old = verifier.parse_vtool_output(SAMPLE_VERSION_MIN_ONLY)
    assert verifier.chosen_minima(old, "arm64") == ["10.12"]
    assert old[0].command == "LC_VERSION_MIN_MACOSX"

    both_platforms = verifier.parse_vtool_output(SAMPLE_SIMULATOR_AND_MACOS)
    assert verifier.chosen_minima(both_platforms, "arm64") == ["14.4"]

    legacy_build = verifier.parse_vtool_output(SAMPLE_SOURCE_ONLY)
    assert verifier.chosen_minima(legacy_build, "arm64") == []
    legacy_min = verifier.parse_vtool_output(SAMPLE_VERSION_MIN_15_1)
    assert verifier.chosen_minima(legacy_min, "arm64") == ["15.1"]


def test_macho_arch_names(tmp_path: Path) -> None:
    verifier = _verifier()
    cases = {
        "arm64": struct.pack("<III", 0xFEEDFACF, 0x0100000C, 0),
        "arm64-capability": struct.pack("<III", 0xFEEDFACF, 0x0100000C, 0x80000000),
        "arm64e": struct.pack("<III", 0xFEEDFACF, 0x0100000C, 0x80000002),
        "x86_64": struct.pack("<III", 0xFEEDFACF, 0x01000007, 3),
        "x86_64h": struct.pack("<III", 0xFEEDFACF, 0x01000007, 8),
    }
    expected = {
        "arm64": ["arm64"],
        "arm64-capability": ["arm64"],
        "arm64e": ["arm64e"],
        "x86_64": ["x86_64"],
        "x86_64h": ["x86_64h"],
    }
    for name, blob in cases.items():
        path = tmp_path / name
        path.write_bytes(blob)
        assert verifier.macho_archs(path) == expected[name]


def test_universal_header_lists_each_arch(tmp_path: Path) -> None:
    verifier = _verifier()
    blob = struct.pack(">II", 0xCAFEBABE, 2)
    blob += struct.pack(">IIIII", 0x01000007, 3, 0, 0, 0)
    blob += struct.pack(">IIIII", 0x0100000C, 2, 0, 0, 0)
    path = tmp_path / "fat"
    path.write_bytes(blob)
    assert verifier.macho_archs(path) == ["x86_64", "arm64e"]


def test_java_class_magic_is_not_macho(tmp_path: Path) -> None:
    verifier = _verifier()
    blob = struct.pack(">IHH", 0xCAFEBABE, 0, 61) + bytes(16)
    path = tmp_path / "Foo.class"
    path.write_bytes(blob)
    assert verifier.macho_archs(path) == []


def test_fat_file_is_checked_per_architecture(tmp_path: Path) -> None:
    verifier = _verifier()
    app = tmp_path / "Galley.app"
    binary = app / "Contents" / "MacOS" / "Galley"
    binary.parent.mkdir(parents=True)
    blob = struct.pack(">II", 0xCAFEBABE, 2)
    blob += struct.pack(">IIIII", 0x01000007, 3, 0, 0, 0)
    blob += struct.pack(">IIIII", 0x0100000C, 0, 0, 0, 0)
    binary.write_bytes(blob)
    seen: list[str] = []

    def run(args: list[str]) -> str:
        arch = args[args.index("-arch") + 1]
        seen.append(arch)
        if arch == "arm64":
            return SAMPLE_UNIVERSAL.split("test (architecture arm64):", 1)[1]
        if arch == "x86_64":
            return """\
Galley (architecture x86_64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 11.0
      sdk 12.3
   ntools 1
     tool LD
  version 764.0
"""
        raise AssertionError(arch)

    violations, unread, checked = verifier.macho_minimum_violations(
        app, "14.4", run_vtool=run
    )
    assert seen == ["x86_64", "arm64"]
    assert unread == []
    assert checked == 1
    assert [(item.relative_path, item.arch, item.minos) for item in violations] == [
        ("Contents/MacOS/Galley", "arm64", "26.0")
    ]


def test_rejected_arch_retries_without_arch_flag(tmp_path: Path) -> None:
    verifier = _verifier()
    app = tmp_path / "Galley.app"
    _arm64(app / "Contents" / "MacOS" / "Galley")
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(list(args))
        if "-arch" in args:
            return "error: Galley: does not contain architecture arm64\n"
        return """\
Galley:
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 26.0
      sdk 26.0
   ntools 1
     tool LD
  version 1.0
"""

    violations, unread, checked = verifier.macho_minimum_violations(
        app, "14.4", run_vtool=run
    )
    assert unread == []
    assert checked == 1
    assert [(item.relative_path, item.arch, item.minos) for item in violations] == [
        ("Contents/MacOS/Galley", "arm64", "26.0")
    ]
    assert calls[0][:3] == ["-show-build", "-arch", "arm64"]
    assert calls[1] == ["-show-build", str(app / "Contents" / "MacOS" / "Galley")]


def test_main_lists_every_macho_above_the_plist_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _verifier()
    app = _app(tmp_path)
    _arm64(app / "Contents" / "MacOS" / "Galley")
    _arm64(app / "Contents" / "Frameworks" / "QtCore")
    _arm64(app / "Contents" / "Frameworks" / "PySide6" / "QtWidgets.abi3.so")
    _arm64(app / "Contents" / "Frameworks" / "libold.dylib")
    _arm64(app / "Contents" / "Frameworks" / "liblegacy.dylib")
    (app / "Contents" / "Resources" / "note.txt").write_text(
        "minos 26.0\n", encoding="utf-8"
    )
    java = app / "Contents" / "Resources" / "Foo.class"
    java.write_bytes(struct.pack(">IHH", 0xCAFEBABE, 0, 61) + bytes(16))
    link = app / "Contents" / "MacOS" / "GalleyLink"
    try:
        link.symlink_to(app / "Contents" / "MacOS" / "Galley")
    except OSError:
        link = None
    samples = {
        ("Galley", "-show-build"): SAMPLE_NUMERIC_PLATFORM,
        ("QtCore", "-show-build"): SAMPLE_BUILD_14_4_0,
        ("QtWidgets.abi3.so", "-show-build"): SAMPLE_UNIVERSAL,
        ("libold.dylib", "-show-build"): SAMPLE_VERSION_MIN_10_15,
        ("liblegacy.dylib", "-show-build"): SAMPLE_SOURCE_ONLY,
        ("liblegacy.dylib", "-show-version-min"): SAMPLE_VERSION_MIN_15_1,
    }
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(list(args))
        key = (Path(args[-1]).name, args[0])
        if key not in samples:
            raise AssertionError(key)
        return samples[key]

    assert verifier.main([str(app)], run_vtool=run) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "Verify bundle metadata failed:\n"
        "3 Mach-O files record a minimum macOS higher than 14.0:\n"
        "Contents/Frameworks/PySide6/QtWidgets.abi3.so "
        "arm64 minos 26.0 floor 14.0\n"
        "Contents/Frameworks/QtCore arm64 minos 14.4.0 floor 14.0\n"
        "Contents/Frameworks/liblegacy.dylib arm64 minos 15.1 floor 14.0\n"
        "pin dependencies lower or raise MINIMUM_MACOS in galley.spec\n"
    )
    names = {Path(args[-1]).name for args in calls}
    assert "note.txt" not in names
    assert "Foo.class" not in names
    assert "GalleyLink" not in names
    legacy_modes = [
        args[0] for args in calls if Path(args[-1]).name == "liblegacy.dylib"
    ]
    assert legacy_modes == ["-show-build", "-show-version-min"]
    galley_modes = [args[0] for args in calls if Path(args[-1]).name == "Galley"]
    assert galley_modes == ["-show-build"]
    assert link is None or link.is_symlink()
