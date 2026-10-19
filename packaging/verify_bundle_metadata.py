"""Check a Galley.app bundle's minimum macOS, license texts, and icon."""

import ast
import plistlib
import sys
from pathlib import Path


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


def expected_icon_name(spec: Path) -> str:
    """Return the ``.icns`` file name in the ``BUNDLE`` icon argument."""
    tree = ast.parse(spec.read_text(encoding="utf-8"), filename=str(spec))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "BUNDLE":
            continue
        for keyword in node.keywords:
            if keyword.arg != "icon":
                continue
            for child in ast.walk(keyword.value):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value.endswith(".icns")
                ):
                    found.append(Path(child.value).name)
    if len(found) != 1:
        raise SystemExit(
            f"expected one .icns name in the BUNDLE icon of {spec}, found {found}"
        )
    return found[0]


def _icns_filename(declared: str) -> str:
    """Return ``declared`` as a bare ``.icns`` file name."""
    name = declared.replace("\\", "/").rsplit("/", 1)[-1]
    if name.endswith(".icns"):
        return name
    return f"{name}.icns"


def bundle_problems(
    app: Path,
    expected: str,
    icon_name: str = "galley.icns",
) -> list[str]:
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
            declared = plist.get("CFBundleIconFile")
            if not isinstance(declared, str) or not declared.strip():
                problems.append(
                    "CFBundleIconFile is missing from "
                    f"{app}/Contents/Info.plist; expected {icon_name}"
                )
            else:
                filename = _icns_filename(declared.strip())
                if filename != icon_name:
                    problems.append(
                        f"CFBundleIconFile is {declared!r}, expected {icon_name!r}"
                    )
    licenses = app / "Contents" / "Resources" / "LICENSES"
    if not licenses.is_dir():
        problems.append(f"Contents/Resources/LICENSES is missing from {app}")
    elif not any(path.is_file() for path in licenses.rglob("*")):
        problems.append(f"Contents/Resources/LICENSES in {app} is empty")
    icon_path = app / "Contents" / "Resources" / icon_name
    if not icon_path.is_file():
        problems.append(f"Contents/Resources/{icon_name} is missing from {app}")
    elif icon_path.stat().st_size == 0:
        problems.append(f"Contents/Resources/{icon_name} in {app} is empty")
    elif icon_path.read_bytes()[:4] != b"icns":
        problems.append(f"Contents/Resources/{icon_name} in {app} is not an icns file")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check one ``Galley.app``. Return 0 when it matches the spec."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(
            "usage: verify_bundle_metadata.py Galley.app",
            file=sys.stderr,
        )
        return 2
    spec = spec_path()
    expected = expected_minimum(spec)
    icon_name = expected_icon_name(spec)
    app = Path(args[0])
    problems = bundle_problems(app, expected, icon_name)
    if problems:
        print("Verify bundle metadata failed:")
        for problem in problems:
            print(problem)
        return 1
    print(
        f"LSMinimumSystemVersion is {expected!r} and "
        "Contents/Resources/LICENSES is present. "
        f"CFBundleIconFile is {icon_name!r}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
