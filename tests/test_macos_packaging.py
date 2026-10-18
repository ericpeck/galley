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


FIRST_LAUNCH_STEPS = (
    "1. Drag Galley to Applications.",
    "2. Open it once and dismiss the warning.",
    "3. Go to System Settings > Privacy & Security and click Open Anyway "
    "(asks for your password).",
    "4. Note: on macOS 14 and earlier, right-click > Open still works.",
)
SPEC = ROOT / "packaging" / "galley.spec"


def _spec_tree() -> ast.AST:
    return ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))


def test_bundle_identifier_is_one_constant() -> None:
    assignments: list[str] = []
    uses: list[ast.expr] = []
    for node in ast.walk(_spec_tree()):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "BUNDLE_IDENTIFIER"
        ):
            value = node.value
            assert isinstance(value, ast.Constant)
            assert isinstance(value.value, str)
            assignments.append(value.value)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "BUNDLE":
            continue
        for keyword in node.keywords:
            if keyword.arg == "bundle_identifier":
                uses.append(keyword.value)
    assert assignments == [BUNDLE_ID]
    assert len(uses) == 1
    use = uses[0]
    assert isinstance(use, ast.Name)
    assert use.id == "BUNDLE_IDENTIFIER"
    assert SPEC.read_text(encoding="utf-8").count(BUNDLE_ID) == 1


def test_workflow_reads_bundle_identifier_from_the_spec() -> None:
    workflow = _workflow()
    assert "BUNDLE_IDENTIFIER" in workflow
    assert BUNDLE_ID not in workflow


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


def test_first_launch_steps() -> None:
    for path in FIRST_LAUNCH:
        text = path.read_text(encoding="utf-8")
        cursor = 0
        for step in FIRST_LAUNCH_STEPS:
            found = text.find(step, cursor)
            assert found != -1, f"{path.name} missing {step!r}"
            cursor = found + len(step)
        assert "xattr" not in text.lower()


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
    assert 'staging="build/dmg-staging"' in workflow
    assert "ln -s /Applications Applications" in workflow
    assert '-srcfolder "${staging}"' in workflow
    assert "hdiutil attach" in workflow
    assert '[[ ! -d "${mount}/Galley.app" ]]' in workflow
    assert '[[ ! -L "${mount}/Applications" ]]' in workflow
    assert '[[ "${target}" != "/Applications" ]]' in workflow
