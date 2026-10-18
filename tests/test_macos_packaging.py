"""Settings for the unsigned macOS disk image.

The spec is parsed with ast. PyInstaller defines names such as BUNDLE when it
runs the spec, so this test must not execute that file.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = "com.ericmercurypeck.galley"
WORKFLOW = ROOT / ".github" / "workflows" / "release-macos.yml"
FIRST_LAUNCH = (
    ROOT / "README.md",
    ROOT / "docs" / "releasing.md",
    WORKFLOW,
)


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _bundle_identifier(spec: Path) -> str:
    tree = ast.parse(spec.read_text(encoding="utf-8"), filename=str(spec))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "BUNDLE":
            continue
        for keyword in node.keywords:
            if keyword.arg != "bundle_identifier":
                continue
            value = keyword.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                raise AssertionError("bundle_identifier must be a string constant")
            found.append(value.value)
    if len(found) != 1:
        raise AssertionError(f"expected one bundle_identifier, found {found}")
    return found[0]


def test_spec_bundle_identifier() -> None:
    assert _bundle_identifier(ROOT / "packaging" / "galley.spec") == BUNDLE_ID


def test_workflow_checks_info_plist_identifier() -> None:
    expected = f'[[ "${{identifier}}" != "{BUNDLE_ID}" ]]'
    assert expected in _workflow()


def test_docs_name_the_bundle_identifier() -> None:
    text = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
    assert BUNDLE_ID in text


def test_old_bundle_identifier_is_not_used() -> None:
    old = "com." + "galley.app"
    paths = (
        ROOT / "packaging" / "galley.spec",
        ROOT / "docs" / "releasing.md",
        ROOT / "README.md",
        WORKFLOW,
    )
    for path in paths:
        assert old not in path.read_text(encoding="utf-8")


def test_first_launch_is_open_anyway_only() -> None:
    for path in FIRST_LAUNCH:
        text = path.read_text(encoding="utf-8").lower()
        assert "drag galley into applications before opening it" in text
        assert "privacy & security" in text
        assert "open anyway" in text
        assert "password" in text
        assert "xattr" not in text
        assert "right-click" not in text
        assert "right click" not in text


def test_pull_request_builds_an_artifact_and_does_not_publish() -> None:
    workflow = _workflow()
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "packaging/**" in workflow
    assert ".github/workflows/release-macos.yml" in workflow
    assert '- "v*"' in workflow
    assert "group: ${{ github.workflow }}-${{ github.ref }}" in workflow
    assert "actions: write" not in workflow
    assert "contents: read" in workflow
    assert "contents: write" in workflow
    assert "github.event_name != 'pull_request'" in workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in workflow
