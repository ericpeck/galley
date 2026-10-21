"""Input errors stay structured. Banner sentences live in galley.app."""

import pickle
from pathlib import Path

from galley.core.errors import MAX_DEPTH, InputError

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "src" / "galley" / "core"

# Phrases from the approved banners. They must not move back into core.
_BANNER_PHRASES = (
    "isn't valid UTF-8",
    "first bad byte",
    "Save the file as UTF-8",
    "Galley can format safely",
    "invalid character escape",
    "The formatter stopped unexpectedly",
    "Your text and file weren't changed",
    "Press Format to try again",
    "Missing comma",
    "Unclosed quote",
    "Trailing comma",
    "Put double quotes around",
    "instead of single quotes",
    "opened on line",
    "Couldn't read this JSON",
    "Couldn't read this YAML",
    "YAML needs spaces",
    "before the JSON document finishes",
    "before the YAML document finishes",
    "A key is repeated",
    "Duplicate key",
    "Missing colon",
)


def test_max_depth_is_the_single_named_limit() -> None:
    assert MAX_DEPTH == 512


def test_input_error_round_trips_through_pickle() -> None:
    error = InputError(
        "bad_escape",
        lineno=4,
        colno=11,
        details={"escape": "\\uD800"},
    )
    restored = pickle.loads(pickle.dumps(error))
    assert isinstance(restored, InputError)
    assert restored.kind == error.kind
    assert restored.lineno == error.lineno
    assert restored.colno == error.colno
    assert restored.details == error.details


def test_core_source_has_no_banner_sentences() -> None:
    offenders: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for phrase in _BANNER_PHRASES:
            if phrase in text:
                relative = path.relative_to(REPO_ROOT)
                offenders.append(f"{relative}: {phrase}")
    assert offenders == []
