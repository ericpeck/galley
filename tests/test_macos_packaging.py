"""Settings for the unsigned macOS disk image.

The spec is parsed with ast. PyInstaller defines names such as BUNDLE when it
runs the spec, so this test must not execute that file.
"""

import ast
import struct
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
    assert "Check: ruamel.yaml.clib self-check" in workflow
    assert "What broke:" in workflow
    assert "Reproduce locally: ${bin} --self-check" in workflow
    assert "dist/Galley.app/Contents/MacOS/Galley --self-check" in docs
    assert "uv run galley --self-check" in docs


# OSType -> pixel size for the standard iconset: 16, 32, 128, 256, and 512
# points, each at 1x and 2x. 32 pt @2x is 64 px (ic12). 512 pt @2x is 1024 px.
ICON_PNG_SIZES = {
    b"icp4": 16,
    b"ic11": 32,
    b"icp5": 32,
    b"ic12": 64,
    b"ic07": 128,
    b"ic13": 256,
    b"ic08": 256,
    b"ic14": 512,
    b"ic09": 512,
    b"ic10": 1024,
}
_PNG = b"\x89PNG\r\n\x1a\n"


def _bundle_icon() -> ast.expr:
    icons: list[ast.expr] = []
    for node in ast.walk(_spec_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "BUNDLE":
            continue
        for keyword in node.keywords:
            if keyword.arg == "icon":
                icons.append(keyword.value)
    assert len(icons) == 1
    return icons[0]


def _icns_png_sizes(data: bytes) -> dict[bytes, tuple[int, int]]:
    assert data[:4] == b"icns"
    total = struct.unpack(">I", data[4:8])[0]
    assert total == len(data)
    sizes: dict[bytes, tuple[int, int]] = {}
    offset = 8
    while offset < len(data):
        kind = data[offset : offset + 4]
        size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        assert size >= 8
        payload = data[offset + 8 : offset + size]
        if payload.startswith(_PNG):
            assert payload[12:16] == b"IHDR"
            width, height = struct.unpack(">II", payload[16:24])
            sizes[kind] = (width, height)
        offset += size
    assert offset == len(data)
    return sizes


def test_spec_points_at_the_committed_icon() -> None:
    icon = _bundle_icon()
    assert isinstance(icon, ast.Call)
    func = icon.func
    assert isinstance(func, ast.Attribute)
    assert func.attr == "join"
    owner = func.value
    assert isinstance(owner, ast.Attribute)
    assert owner.attr == "path"
    assert isinstance(owner.value, ast.Name)
    assert owner.value.id == "os"
    assert len(icon.args) == 3
    dirname = icon.args[0]
    assert isinstance(dirname, ast.Call)
    dirname_func = dirname.func
    assert isinstance(dirname_func, ast.Attribute)
    assert dirname_func.attr == "dirname"
    dirname_owner = dirname_func.value
    assert isinstance(dirname_owner, ast.Attribute)
    assert dirname_owner.attr == "path"
    assert isinstance(dirname_owner.value, ast.Name)
    assert dirname_owner.value.id == "os"
    assert len(dirname.args) == 1
    spec_name = dirname.args[0]
    assert isinstance(spec_name, ast.Name)
    assert spec_name.id == "SPEC"
    directory = icon.args[1]
    filename = icon.args[2]
    assert isinstance(directory, ast.Constant)
    assert directory.value == "icon"
    assert isinstance(filename, ast.Constant)
    assert filename.value == "galley.icns"
    svg = (ROOT / "packaging" / "icon" / "galley.svg").read_text(encoding="utf-8")
    assert 'width="1024"' in svg
    assert 'height="1024"' in svg
    assert "#0080A6" in svg
    icns = ROOT / "packaging" / "icon" / "galley.icns"
    assert icns.is_file()
    found = _icns_png_sizes(icns.read_bytes())
    assert {key: found.get(key) for key in ICON_PNG_SIZES} == {
        key: (size, size) for key, size in ICON_PNG_SIZES.items()
    }


def test_release_workflow_does_not_render_the_icon() -> None:
    workflow = _workflow()
    assert "regenerate.py" not in workflow
    assert "galley.svg" not in workflow
    assert "iconutil" not in workflow
    assert "rsvg-convert" not in workflow
