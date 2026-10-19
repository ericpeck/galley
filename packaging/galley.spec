# -*- mode: python ; coding: utf-8 -*-
"""Unsigned arm64 Galley.app.

PyInstaller executes this file and defines SPEC, Analysis, PYZ, EXE, COLLECT,
and BUNDLE. Run it on macOS Apple Silicon from the repository root:

    uv sync --frozen --no-dev --no-editable
    uv run --frozen --no-dev --no-editable --with pyinstaller==6.22.3 pyinstaller --noconfirm --clean packaging/galley.spec

codesign_identity is None: no Developer ID is used. PyInstaller still ad-hoc
signs the bundle, which Apple Silicon requires in order to start the binary.
A downloaded copy is unsigned for Gatekeeper. See docs/releasing.md.
"""

import importlib.util
import os
from pathlib import Path

import PySide6

from galley import __version__

# Bundle id for Galley.app. Change this line to pick another identifier.
BUNDLE_IDENTIFIER = "com.ericmercurypeck.galley"

# Floor is the highest minos any bundled binary records (12.0 with
# PySide6 6.9.3) rounded up to the oldest macOS CI launches on (14.0).
# PySide6 6.10+ bindings record the build SDK as minos, so do not bump
# PySide6 without re-checking those binaries.
MINIMUM_MACOS = "14.0"

_ROOT = os.path.normpath(os.path.join(os.path.dirname(SPEC), ".."))
_ENTRY = os.path.join(_ROOT, "src", "galley", "__main__.py")


def _license_datas():
    """Ship third-party license texts inside the app bundle."""
    source = os.path.join(_ROOT, "LICENSES")
    if not os.path.isdir(source):
        raise SystemExit(
            "LICENSES/ is missing. The disk image must include third-party licenses."
        )
    return [(source, "LICENSES")]


def _clib_binaries():
    """Bundle ruamel.yaml.clib 0.2.15, which the project depends on."""
    found = importlib.util.find_spec("_ruamel_yaml")
    if found is None or not found.origin:
        raise SystemExit(
            "ruamel.yaml.clib 0.2.15 is required (_ruamel_yaml missing). "
            "The disk image build fails without the C extension."
        )
    return [(found.origin, ".")]


def _offscreen_plugin():
    """Keep Qt's offscreen platform plugin, used by the headless smoke test."""
    root = Path(PySide6.__file__).resolve().parent
    for directory in (
        root / "Qt" / "plugins" / "platforms",
        root / "Qt6" / "plugins" / "platforms",
    ):
        if not directory.is_dir():
            continue
        matches = sorted(path for path in directory.glob("*offscreen*") if path.is_file())
        if matches:
            return [(str(matches[0]), "PySide6/Qt/plugins/platforms")]
    raise SystemExit(
        "Qt offscreen platform plugin was not found next to PySide6. "
        "The headless smoke test needs it."
    )


_clib = _clib_binaries()

a = Analysis(
    [_ENTRY],
    pathex=[],
    binaries=_clib + _offscreen_plugin(),
    datas=_license_datas(),
    hiddenimports=["_ruamel_yaml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Galley",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Galley",
)
app = BUNDLE(
    coll,
    name="Galley.app",
    icon=os.path.join(os.path.dirname(SPEC), "icon", "galley.icns"),
    bundle_identifier=BUNDLE_IDENTIFIER,
    version=__version__,
    info_plist={"LSMinimumSystemVersion": MINIMUM_MACOS},
)
