"""Rebuild galley.icns from galley.svg.

Run this by hand when the icon art changes. The Release macOS workflow does
not call this script and does not render the SVG. The disk image uses the
committed galley.icns.

Prerequisites, none of which are project or runtime dependencies:
- An SVG rasterizer on PATH: rsvg-convert (librsvg), Inkscape, or the
  cairosvg command.
- On a Mac, iconutil from the Xcode command line tools packs the iconset.
  Without iconutil, this script writes the .icns file itself: PNG images
  for 16, 32, 128, 256, and 512 points at 1x and 2x. Pillow is not used;
  its ICNS writer omits the 16- and 32-point @1x images.

    python packaging/icon/regenerate.py
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

# Iconset file, pixel size, and icns OSTypes. icp6 repeats the 64px image
# (32 pt @2x) for readers that look that type up. iconutil emits it too.
_ICONSET: tuple[tuple[str, int, tuple[bytes, ...]], ...] = (
    ("icon_16x16.png", 16, (b"icp4",)),
    ("icon_16x16@2x.png", 32, (b"ic11",)),
    ("icon_32x32.png", 32, (b"icp5",)),
    ("icon_32x32@2x.png", 64, (b"ic12", b"icp6")),
    ("icon_128x128.png", 128, (b"ic07",)),
    ("icon_128x128@2x.png", 256, (b"ic13",)),
    ("icon_256x256.png", 256, (b"ic08",)),
    ("icon_256x256@2x.png", 512, (b"ic14",)),
    ("icon_512x512.png", 512, (b"ic09",)),
    ("icon_512x512@2x.png", 1024, (b"ic10",)),
)

_PNG = b"\x89PNG\r\n\x1a\n"


def _png_size(data: bytes, label: str) -> tuple[int, int]:
    """Return the pixel size of a PNG, or exit if ``data`` is not one."""
    if len(data) < 24 or not data.startswith(_PNG) or data[12:16] != b"IHDR":
        raise SystemExit(f"{label} is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _rasterizer() -> str:
    """Return the first SVG rasterizer available on PATH."""
    for name in ("rsvg-convert", "inkscape", "cairosvg"):
        if shutil.which(name):
            return name
    raise SystemExit(
        "No SVG rasterizer found. Install rsvg-convert (librsvg), "
        "Inkscape, or cairosvg. See the prerequisites at the top of "
        "packaging/icon/regenerate.py."
    )


def _render_command(tool: str, svg: Path, size: int, dest: Path) -> list[str]:
    """Return the command that renders ``svg`` to a square PNG."""
    if tool == "rsvg-convert":
        return [
            "rsvg-convert",
            "--width",
            str(size),
            "--height",
            str(size),
            "--background-color",
            "none",
            "--output",
            str(dest),
            str(svg),
        ]
    if tool == "inkscape":
        return [
            "inkscape",
            str(svg),
            "--export-width",
            str(size),
            "--export-height",
            str(size),
            "--export-filename",
            str(dest),
        ]
    if tool == "cairosvg":
        return [
            "cairosvg",
            str(svg),
            "-o",
            str(dest),
            "-W",
            str(size),
            "-H",
            str(size),
        ]
    raise SystemExit(f"Unknown SVG rasterizer {tool}")


def _run(command: list[str]) -> None:
    """Run ``command``. Exit with its status when it fails."""
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"{command[0]} failed with status {completed.returncode}")


def _render_sizes(svg: Path) -> dict[int, bytes]:
    """Rasterize ``svg`` once per distinct icon pixel size."""
    tool = _rasterizer()
    rendered: dict[int, bytes] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for size in sorted({entry[1] for entry in _ICONSET}):
            dest = root / f"{size}.png"
            _run(_render_command(tool, svg, size, dest))
            png = dest.read_bytes()
            width, height = _png_size(png, f"{tool} output for {size}px")
            if (width, height) != (size, size):
                raise SystemExit(f"{tool} wrote {width}x{height}, expected {size}px")
            rendered[size] = png
    return rendered


def _elements(rendered: dict[int, bytes]) -> list[tuple[bytes, bytes]]:
    """Return (OSType, png) pairs in iconset order."""
    elements: list[tuple[bytes, bytes]] = []
    for _name, size, types in _ICONSET:
        png = rendered[size]
        elements.extend((ostype, png) for ostype in types)
    return elements


def _pack_icns(elements: list[tuple[bytes, bytes]]) -> bytes:
    """Pack PNG elements into an icns container with a table of contents."""
    chunks = [
        ostype + struct.pack(">I", 8 + len(png)) + png for ostype, png in elements
    ]
    toc_body = b"".join(
        ostype + struct.pack(">I", 8 + len(png)) for ostype, png in elements
    )
    toc = b"TOC " + struct.pack(">I", 8 + len(toc_body)) + toc_body
    body = toc + b"".join(chunks)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def _atomic_write(dest: Path, data: bytes) -> None:
    """Replace ``dest`` with ``data`` via a temporary file in the same directory."""
    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        path.replace(dest)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _write_with_iconutil(rendered: dict[int, bytes], dest: Path) -> None:
    """Pack an iconset with macOS iconutil."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "galley.iconset"
        iconset.mkdir()
        for name, size, _types in _ICONSET:
            (iconset / name).write_bytes(rendered[size])
        fd, raw = tempfile.mkstemp(
            dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp"
        )
        os.close(fd)
        staged = Path(raw)
        try:
            _run(["iconutil", "-c", "icns", "-o", str(staged), str(iconset)])
            staged.replace(dest)
        except Exception:
            staged.unlink(missing_ok=True)
            raise


def _icns_png_sizes(data: bytes) -> dict[bytes, tuple[int, int]]:
    """Return OSType -> pixel size for every PNG element in an icns file."""
    if len(data) < 8 or data[:4] != b"icns":
        raise SystemExit("output is not an icns file")
    total = struct.unpack(">I", data[4:8])[0]
    if total != len(data):
        raise SystemExit(f"icns length is {total}, file is {len(data)} bytes")
    sizes: dict[bytes, tuple[int, int]] = {}
    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            raise SystemExit("truncated icns element")
        kind = data[offset : offset + 4]
        size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        if size < 8 or offset + size > len(data):
            raise SystemExit(f"bad icns element {kind!r}")
        payload = data[offset + 8 : offset + size]
        if payload.startswith(_PNG):
            sizes[kind] = _png_size(payload, kind.decode("ascii", "replace"))
        offset += size
    return sizes


def _check_icns(data: bytes) -> list[str]:
    """Return problems when a standard iconset size is missing or wrong."""
    found = _icns_png_sizes(data)
    problems: list[str] = []
    expected: dict[bytes, int] = {}
    for _name, size, types in _ICONSET:
        for ostype in types:
            expected[ostype] = size
    for ostype, size in expected.items():
        actual = found.get(ostype)
        if actual != (size, size):
            label = ostype.decode("ascii")
            problems.append(f"{label} is {actual}, expected {size}x{size} PNG")
    return problems


def main() -> int:
    """Rebuild the galley.icns that sits next to this script."""
    root = Path(__file__).resolve().parent
    svg = root / "galley.svg"
    dest = root / "galley.icns"
    if not svg.is_file():
        print(f"Icon master is missing: {svg}", file=sys.stderr)
        return 1
    rendered = _render_sizes(svg)
    if shutil.which("iconutil"):
        _write_with_iconutil(rendered, dest)
    else:
        _atomic_write(dest, _pack_icns(_elements(rendered)))
    problems = _check_icns(dest.read_bytes())
    if problems:
        print("regenerate.py wrote an incomplete icns:", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"Wrote {dest}")
    for ostype, (width, height) in _icns_png_sizes(dest.read_bytes()).items():
        print(f"  {ostype.decode('ascii')} {width}x{height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
