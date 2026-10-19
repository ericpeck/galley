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
    "3. Go to System Settings > Privacy & Security, scroll down to Security, "
    "and click Open Anyway next to the message about Galley "
    "(asks for your password).",
    "4. Note: on macOS 14 and earlier, right-click > Open still works.",
    "After that, Galley opens with a normal double-click.",
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


def test_minimum_macos_is_written_into_the_bundle() -> None:
    assignments: list[str] = []
    plist_values: list[ast.expr] = []
    for node in ast.walk(_spec_tree()):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "MINIMUM_MACOS"
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
            if keyword.arg == "info_plist":
                plist_values.append(keyword.value)
    assert assignments == ["14.4"]
    assert len(plist_values) == 1
    plist = plist_values[0]
    assert isinstance(plist, ast.Dict)
    assert len(plist.keys) == 1
    key = plist.keys[0]
    assert isinstance(key, ast.Constant)
    assert key.value == "LSMinimumSystemVersion"
    mapped = plist.values[0]
    assert isinstance(mapped, ast.Name)
    assert mapped.id == "MINIMUM_MACOS"


def test_spec_ships_the_licenses_directory() -> None:
    tree = _spec_tree()
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_license_datas"
    ]
    assert len(functions) == 1
    returns = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Return)]
    assert len(returns) == 1
    value = returns[0].value
    assert isinstance(value, ast.List)
    assert len(value.elts) == 1
    pair = value.elts[0]
    assert isinstance(pair, ast.Tuple)
    assert len(pair.elts) == 2
    dest = pair.elts[1]
    assert isinstance(dest, ast.Constant)
    assert dest.value == "LICENSES"
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    ]
    assert len(calls) == 1
    datas = [keyword.value for keyword in calls[0].keywords if keyword.arg == "datas"]
    assert len(datas) == 1
    datas_call = datas[0]
    assert isinstance(datas_call, ast.Call)
    assert isinstance(datas_call.func, ast.Name)
    assert datas_call.func.id == "_license_datas"


def test_verify_bundle_metadata_is_its_own_step() -> None:
    workflow = _workflow()
    self_check = workflow.find(
        "- name: Check that the built app loads ruamel.yaml.clib"
    )
    verify = workflow.find("- name: Verify bundle metadata")
    smoke = workflow.find("- name: Smoke-launch Galley headless")
    assert self_check != -1
    assert self_check < verify < smoke
    block = workflow[verify:smoke]
    command = (
        "uv run --frozen --no-dev --no-editable "
        "python packaging/verify_bundle_metadata.py dist/Galley.app"
    )
    assert command in block
    assert "--self-check" not in block
    assert "LSMinimumSystemVersion" not in block
    assert "PlistBuddy" not in block
    assert '"LICENSES/**"' in workflow


def test_releasing_docs_name_the_minimum_and_licenses() -> None:
    text = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
    assert "LSMinimumSystemVersion" in text
    assert "`14.4`" in text
    assert "Contents/Resources/LICENSES" in text
    clib = text.find("## ruamel.yaml.clib")
    minimum = text.find("## Minimum macOS and licenses")
    assert minimum != -1
    assert clib != -1
    assert minimum < clib


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


def test_built_app_runs_the_clib_self_check() -> None:
    workflow = _workflow()
    docs = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
    assert '"${bin}" --self-check' in workflow
    assert "LICENSES self-check: ok" in workflow
    assert "Check: ruamel.yaml.clib self-check" in workflow
    assert "What broke:" in workflow
    assert "Reproduce locally: ${bin} --self-check" in workflow
    assert "dist/Galley.app/Contents/MacOS/Galley --self-check" in docs
    assert "uv run galley --self-check" in docs
