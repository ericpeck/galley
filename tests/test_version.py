"""The package, pyproject.toml and uv.lock name the same Galley version."""

import tomllib
from pathlib import Path

import galley

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject_and_uv_lock() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [entry for entry in lock["package"] if entry["name"] == "galley"]
    assert len(locked) == 1
    assert pyproject["project"]["version"] == galley.__version__
    assert locked[0]["version"] == galley.__version__
