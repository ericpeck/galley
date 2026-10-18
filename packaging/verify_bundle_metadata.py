"""Check Galley.app metadata and Mach-O deployment minima.

The Release macOS workflow runs this after the ruamel.yaml.clib self-check.
It compares ``LSMinimumSystemVersion`` with ``MINIMUM_MACOS`` in the spec,
checks that license texts are present, and fails when any Mach-O file records
a minimum macOS higher than ``LSMinimumSystemVersion``.
"""

import ast
import plistlib
import re
import struct
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

NEXT_STEP = "pin dependencies lower or raise MINIMUM_MACOS in galley.spec"

_MAX_FAT_ARCHS = 8
_THIN_ENDIAN = {
    b"\xcf\xfa\xed\xfe": "<",  # MH_MAGIC_64
    b"\xce\xfa\xed\xfe": "<",  # MH_MAGIC
    b"\xfe\xed\xfa\xcf": ">",  # MH_CIGAM_64
    b"\xfe\xed\xfa\xce": ">",  # MH_CIGAM
}
# (endian, fat_arch entry size). CAFEBABE is also Java class magic; a class
# file fails the fat-header checks below and is not treated as Mach-O.
_FAT_HEADERS = {
    b"\xca\xfe\xba\xbe": (">", 20),
    b"\xbe\xba\xfe\xca": ("<", 20),
    b"\xca\xfe\xba\xbf": (">", 32),
    b"\xbf\xba\xfe\xca": ("<", 32),
}
_ARCH_RE = re.compile(r"\(architecture ([A-Za-z0-9_]+)\)")
_LOAD_RE = re.compile(r"^\s*Load command\b")
_CMD_RE = re.compile(r"^\s*cmd\s+(LC_[A-Z0-9_]+)\s*$")
_PLATFORM_RE = re.compile(r"^\s*platform\s+(\S+)\s*$")
_MINOS_RE = re.compile(r"^\s*minos\s+(\d+(?:\.\d+)*)\s*$")
_VERSION_RE = re.compile(r"^\s*version\s+(\d+(?:\.\d+)*)\s*$")
_VERSION_TEXT = re.compile(r"\d+(?:\.\d+)*")
_RESOLVED_VTOOL: list[str] | None = None
_VTOOL_MODES = ("-show-build", "-show-version-min")


class VtoolUnavailable(RuntimeError):
    """``vtool`` is not on PATH and ``xcrun`` could not provide it."""


@dataclass(frozen=True)
class ParsedMinimum:
    """A deployment minimum taken from one vtool load command."""

    arch: str
    minimum: str
    command: str


@dataclass(frozen=True, order=True)
class MachOViolation:
    """One architecture whose recorded minimum is above the declared floor."""

    relative_path: str
    arch: str
    minos: str
    floor: str


def _version_parts(value: str) -> tuple[int, ...]:
    if not _VERSION_TEXT.fullmatch(value):
        raise ValueError(value)
    return tuple(int(part) for part in value.split("."))


def version_is_above(minimum: str, floor: str) -> bool:
    """Return whether ``minimum`` is a higher dotted version than ``floor``.

    Missing trailing components are zeros, so ``14.4`` and ``14.4.0`` match.
    """
    left = _version_parts(minimum)
    right = _version_parts(floor)
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


def _is_macos_platform(platform: str) -> bool:
    return platform == "" or platform == "1" or platform.upper() == "MACOS"


def parse_vtool_output(text: str) -> list[ParsedMinimum]:
    """Read deployment minima from ``vtool -show-build`` or ``-show-version-min``.

    ``minos`` on ``LC_BUILD_VERSION`` is the modern minimum. ``version`` on
    ``LC_BUILD_VERSION`` is the linker tool, and ``LC_SOURCE_VERSION`` is not
    a deployment target. Those values are ignored. ``LC_VERSION_MIN_MACOSX``
    supplies the minimum when the file has no ``LC_BUILD_VERSION``.
    """
    arch = ""
    command = ""
    platform = ""
    found: list[ParsedMinimum] = []
    for line in text.splitlines():
        arch_match = _ARCH_RE.search(line)
        if arch_match:
            arch = arch_match.group(1)
            command = ""
            platform = ""
            continue
        if _LOAD_RE.match(line):
            command = ""
            platform = ""
            continue
        cmd_match = _CMD_RE.match(line)
        if cmd_match:
            command = cmd_match.group(1)
            platform = ""
            continue
        platform_match = _PLATFORM_RE.match(line)
        if platform_match:
            platform = platform_match.group(1)
            continue
        minos_match = _MINOS_RE.match(line)
        if minos_match and command == "LC_BUILD_VERSION":
            if _is_macos_platform(platform):
                found.append(
                    ParsedMinimum(arch, minos_match.group(1), "LC_BUILD_VERSION")
                )
            continue
        if minos_match and command == "LC_VERSION_MIN_MACOSX":
            found.append(
                ParsedMinimum(arch, minos_match.group(1), "LC_VERSION_MIN_MACOSX")
            )
            continue
        version_match = _VERSION_RE.match(line)
        if version_match and command == "LC_VERSION_MIN_MACOSX":
            found.append(
                ParsedMinimum(arch, version_match.group(1), "LC_VERSION_MIN_MACOSX")
            )
    return found


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def chosen_minima(parsed: list[ParsedMinimum], arch: str) -> list[str]:
    """Prefer ``LC_BUILD_VERSION`` for ``arch``, else ``LC_VERSION_MIN_MACOSX``."""
    if arch:
        labeled = [row for row in parsed if row.arch == arch]
        rows = labeled or [row for row in parsed if row.arch == ""]
    else:
        rows = list(parsed)
    build = _unique(row.minimum for row in rows if row.command == "LC_BUILD_VERSION")
    if build:
        return build
    return _unique(
        row.minimum for row in rows if row.command == "LC_VERSION_MIN_MACOSX"
    )


def _arch_name(cputype: int, cpusubtype: int) -> str | None:
    subtype = cpusubtype & 0x00FFFFFF
    if cputype == 0x0100000C:
        return "arm64e" if subtype == 2 else "arm64"
    if cputype == 0x01000007:
        return "x86_64h" if subtype == 8 else "x86_64"
    names = {
        7: "i386",
        12: "arm",
        18: "ppc",
        0x01000012: "ppc64",
        0x0200000C: "arm64_32",
    }
    return names.get(cputype)


def macho_archs(path: Path) -> list[str]:
    """Return vtool architecture names for a Mach-O file, else an empty list."""
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
            if magic in _THIN_ENDIAN:
                rest = handle.read(8)
                if len(rest) < 8:
                    return []
                cputype, cpusubtype = struct.unpack(_THIN_ENDIAN[magic] + "II", rest)
                name = _arch_name(cputype, cpusubtype)
                return [] if name is None else [name]
            fat = _FAT_HEADERS.get(magic)
            if fat is None:
                return []
            endian, entry_size = fat
            count_raw = handle.read(4)
            if len(count_raw) < 4:
                return []
            (count,) = struct.unpack(endian + "I", count_raw)
            if count < 1 or count > _MAX_FAT_ARCHS:
                return []
            names: list[str] = []
            for _slice in range(count):
                entry = handle.read(entry_size)
                if len(entry) < 8:
                    return []
                cputype, cpusubtype = struct.unpack_from(endian + "II", entry)
                name = _arch_name(cputype, cpusubtype)
                if name is None:
                    return []
                if name not in names:
                    names.append(name)
            return names
    except OSError:
        return []


def _default_vtool(args: list[str]) -> str:
    """Run ``vtool`` and return its combined stdout and stderr."""
    global _RESOLVED_VTOOL
    prefixes: tuple[list[str], ...]
    if _RESOLVED_VTOOL is None:
        prefixes = (["vtool"], ["xcrun", "vtool"])
    else:
        prefixes = (_RESOLVED_VTOOL,)
    missing: FileNotFoundError | None = None
    for prefix in prefixes:
        try:
            completed = subprocess.run(
                [*prefix, *args],
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
        except FileNotFoundError as exc:
            missing = exc
            continue
        except subprocess.TimeoutExpired:
            return "error: vtool timed out"
        _RESOLVED_VTOOL = prefix
        return f"{completed.stdout}\n{completed.stderr}"
    raise VtoolUnavailable("vtool is not available") from missing


def _excerpt(text: str, binary: Path) -> str:
    cleaned = text.replace(str(binary), binary.name)
    pieces = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not pieces:
        return ""
    excerpt = " | ".join(pieces[:3])
    if len(excerpt) > 160:
        return excerpt[:157] + "..."
    return excerpt


def _rejected_arch(text: str) -> bool:
    lowered = text.lower()
    return (
        "does not contain architecture" in lowered or "unknown architecture" in lowered
    )


def _vtool_args(mode: str, path: Path, arch: str) -> list[str]:
    args = [mode]
    if arch:
        args.extend(["-arch", arch])
    args.append(str(path))
    return args


def _minima_for_arch(
    runner: Callable[[list[str]], str],
    path: Path,
    arch: str,
) -> tuple[list[str], str]:
    outputs: list[str] = []
    for mode in _VTOOL_MODES:
        text = runner(_vtool_args(mode, path, arch))
        outputs.append(text)
        minima = chosen_minima(parse_vtool_output(text), arch)
        if minima:
            return minima, ""
        if arch and _rejected_arch(text):
            plain = runner(_vtool_args(mode, path, ""))
            outputs.append(plain)
            minima = chosen_minima(parse_vtool_output(plain), arch)
            if minima:
                return minima, ""
    return [], _excerpt("\n".join(outputs), path)


def macho_minimum_violations(
    app: Path,
    floor: str,
    *,
    run_vtool: Callable[[list[str]], str] | None = None,
) -> tuple[list[MachOViolation], list[str], int]:
    """Return minima above ``floor``, unreadable files, and files checked.

    ``floor`` is ``LSMinimumSystemVersion`` from the app's Info.plist.
    ``run_vtool`` receives the arguments after the ``vtool`` executable.
    The count is Mach-O files, not architectures.
    """
    try:
        _version_parts(floor)
    except ValueError:
        return [], [f"LSMinimumSystemVersion {floor!r} is not a dotted version"], 0
    runner = _default_vtool if run_vtool is None else run_vtool
    violations: set[MachOViolation] = set()
    unread: list[str] = []
    checked = 0
    if not app.is_dir():
        return [], unread, checked
    for path in sorted(app.rglob("*")):
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        archs = macho_archs(path)
        if not archs:
            continue
        checked += 1
        relative = path.relative_to(app).as_posix()
        for arch in archs:
            minima, note = _minima_for_arch(runner, path, arch)
            label = arch or "unknown"
            if not minima:
                message = (
                    f"{relative} {label}: no LC_BUILD_VERSION or "
                    "LC_VERSION_MIN_MACOSX minimum"
                )
                if note:
                    message = f"{message} ({note})"
                unread.append(message)
                continue
            for minimum in minima:
                if version_is_above(minimum, floor):
                    violations.add(MachOViolation(relative, label, minimum, floor))
    return sorted(violations), unread, checked


def format_macho_violations(violations: list[MachOViolation]) -> str:
    """Summarize files whose Mach-O minimum is above the declared floor."""
    files = sorted({item.relative_path for item in violations})
    floor = violations[0].floor
    if len(files) == 1:
        header = f"1 Mach-O file records a minimum macOS higher than {floor}:"
    else:
        header = (
            f"{len(files)} Mach-O files record a minimum macOS higher than {floor}:"
        )
    lines = [header]
    for item in violations:
        lines.append(
            f"{item.relative_path} {item.arch} minos {item.minos} floor {item.floor}"
        )
    lines.append(NEXT_STEP)
    return "\n".join(lines)


def spec_path() -> Path:
    """Return the PyInstaller spec that records ``MINIMUM_MACOS``."""
    return Path(__file__).resolve().parent / "galley.spec"


def expected_minimum(spec: Path) -> str:
    """Return the single ``MINIMUM_MACOS`` string constant in ``spec``."""
    tree = ast.parse(spec.read_text(encoding="utf-8"), filename=str(spec))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "MINIMUM_MACOS":
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            raise SystemExit("MINIMUM_MACOS must be a string constant")
        found.append(value.value)
    if len(found) != 1:
        raise SystemExit("expected one MINIMUM_MACOS string")
    return found[0]


def bundle_problems(app: Path, expected: str) -> list[str]:
    """Return human-readable problems for ``app``, or an empty list."""
    problems: list[str] = []
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        problems.append(f"Info.plist is missing from {app}")
    else:
        try:
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            problems.append(f"Info.plist in {app} could not be read ({exc})")
        else:
            actual = plist.get("LSMinimumSystemVersion")
            if actual is None:
                problems.append(
                    "LSMinimumSystemVersion is missing from "
                    f"{app}/Contents/Info.plist; expected {expected}"
                )
            elif actual != expected:
                problems.append(
                    f"LSMinimumSystemVersion is {actual!r}, expected {expected!r}"
                )
    licenses = app / "Contents" / "Resources" / "LICENSES"
    if not licenses.is_dir():
        problems.append(f"Contents/Resources/LICENSES is missing from {app}")
    elif not any(path.is_file() for path in licenses.rglob("*")):
        problems.append(f"Contents/Resources/LICENSES in {app} is empty")
    return problems


def plist_minimum(app: Path) -> str | None:
    """Return ``LSMinimumSystemVersion`` when Info.plist stores it as a string."""
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return None
    value = plist.get("LSMinimumSystemVersion")
    if not isinstance(value, str) or value == "":
        return None
    return value


def main(
    argv: list[str] | None = None,
    *,
    run_vtool: Callable[[list[str]], str] | None = None,
) -> int:
    """Check one ``Galley.app``. Return 0 when it matches the spec."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(
            "usage: verify_bundle_metadata.py Galley.app",
            file=sys.stderr,
        )
        return 2
    expected = expected_minimum(spec_path())
    app = Path(args[0])
    problems = bundle_problems(app, expected)
    floor = plist_minimum(app)
    violations: list[MachOViolation] = []
    checked = 0
    if floor is None:
        if not problems:
            problems.append(
                "LSMinimumSystemVersion could not be read from "
                f"{app}/Contents/Info.plist"
            )
    else:
        try:
            violations, unread, checked = macho_minimum_violations(
                app, floor, run_vtool=run_vtool
            )
        except VtoolUnavailable:
            problems.append("vtool is not available")
        else:
            problems.extend(unread)
    if problems or violations:
        print("Verify bundle metadata failed:")
        for problem in problems:
            print(problem)
        if violations:
            print(format_macho_violations(violations))
        return 1
    print(
        f"LSMinimumSystemVersion is {expected!r} and "
        "Contents/Resources/LICENSES is present."
    )
    print(f"Mach-O minimums are at or below {floor} ({checked} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
