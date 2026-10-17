"""Input errors stay structured. Banner sentences live in galley.app."""

from pathlib import Path

from galley.core.errors import MAX_DEPTH

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "src" / "galley" / "core"

# Phrases from the approved banners. They must not move back into core.
_BANNER_PHRASES = (
    "isn't valid UTF-8",
    "first bad byte",
    "Save the file as UTF-8",
    "Galley can format safely",
    "invalid character escape",
)


def test_max_depth_is_the_single_named_limit() -> None:
    assert MAX_DEPTH == 512


def test_core_source_has_no_banner_sentences() -> None:
    offenders: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for phrase in _BANNER_PHRASES:
            if phrase in text:
                relative = path.relative_to(REPO_ROOT)
                offenders.append(f"{relative}: {phrase}")
    assert offenders == []
