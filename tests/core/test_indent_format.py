"""Format writes the indent the picker chose: 2 spaces, 4 spaces, or Tab.

Core never reads the setting. ``indent`` is passed to ``format_document``
and on to every writer. The 2-space goldens are the output on main at
8a2e07c, before ``indent`` existed, so the default stays byte for byte.
"""

import json
from io import StringIO
from queue import SimpleQueue

import pytest
import ruamel.yaml.main as ruamel_main
from ruamel.yaml import YAML
from tests.text_compare import assert_same_text

import galley.core.format as format_module
from galley.core.format import (
    DEFAULT_INDENT,
    _format_json_walk_first,
    format_document,
    format_json,
    format_yaml,
)
from galley.core.html_format import format_html
from galley.core.mask import MASK, MaskRules, mask_document
from galley.core.worker import FormatJob, SuccessEvent, _execute
from galley.core.xml_format import format_xml

_FOUR = "    "
_TAB = "\t"

_JSON = (
    '{"name": "Ann", "tags": ["a", {"k": [1, 2]}], "empty": {}, "none": [], "n": 1.5}'
)
_YAML = (
    "name: Ann\ntags:\n- a\n- k:\n  - 1\n  - 2\n  m: {x: 1}\n- [3, 4]\n"
    "note: |\n  first\n    kept\n  last\nempty: {}\n"
)
_JSON_FOR_YAML = '{"name": "Ann", "tags": ["a", {"k": [1, 2]}], "n": 1.5}'
_YAML_FOR_JSON = "name: Ann\ntags:\n- a\n- k: [1, 2]\n"
_XML = (
    '<?xml version="1.0"?>\n<root a="1"><item><name>  Ann  </name>'
    '<t xml:space="preserve">\n  x\n   y </t><!-- one\n     two --></item>'
    "<e/></root>"
)
_HTML = (
    "<!DOCTYPE html>\n<html><body><div><p>Hi <b>there</b></p><pre>  a\n    b</pre>"
    "<!-- c\n   d --><ul><li>one<li>two</ul></div></body></html>"
)

# Output on main at 8a2e07c, before ``indent`` existed.
_GOLDEN = {
    ("json", "json", _JSON): (
        '{\n  "name": "Ann",\n  "tags": [\n    "a",\n    {\n      "k": [\n'
        '        1,\n        2\n      ]\n    }\n  ],\n  "empty": {},\n'
        '  "none": [],\n  "n": 1.5\n}\n'
    ),
    ("yaml", "yaml", _YAML): (
        "name: Ann\ntags:\n- a\n- k:\n  - 1\n  - 2\n  m:\n    x: 1\n- - 3\n  - 4\n"
        'note: "first\\n  kept\\nlast\\n"\nempty: {}\n'
    ),
    ("json", "yaml", _JSON_FOR_YAML): (
        "name: Ann\ntags:\n- a\n- k:\n  - 1\n  - 2\nn: 1.5\n"
    ),
    ("yaml", "json", _YAML_FOR_JSON): (
        '{\n  "name": "Ann",\n  "tags": [\n    "a",\n    {\n      "k": [\n'
        "        1,\n        2\n      ]\n    }\n  ]\n}\n"
    ),
    ("xml", "xml", _XML): (
        '<?xml version="1.0"?>\n<root a="1">\n  <item>\n    <name>  Ann  </name>\n'
        '    <t xml:space="preserve">\n  x\n   y </t>\n    <!-- one\n     two -->\n'
        "  </item>\n  <e/>\n</root>\n"
    ),
    ("html", "html", _HTML): (
        "<!DOCTYPE html>\n<html>\n  <body>\n    <div>\n"
        "      <p>Hi <b>there</b></p>\n      <pre>  a\n    b</pre>\n"
        "      <!-- c\n   d -->\n      <ul>\n        <li>one\n        <li>two\n"
        "      </ul>\n    </div>\n  </body>\n</html>\n"
    ),
}

_ENGINES = ("c", "pure")


@pytest.fixture(params=_ENGINES)
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """With ruamel.yaml.clib, then forced to pure Python, as in the parity tests."""
    if request.param == "c":
        if not format_module._clib_available():
            pytest.skip("ruamel.yaml.clib is not installed")
        return "c"
    monkeypatch.setattr(ruamel_main, "CParser", None)
    monkeypatch.setattr(ruamel_main, "CEmitter", None)
    monkeypatch.setattr(format_module, "_CLIB_READY", False)
    return "pure"


def _deeper(text: str, unit: str) -> str:
    """``text`` with each leading 2-space step replaced by ``unit``."""
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        steps = (len(line) - len(stripped)) // 2
        lines.append(unit * steps + stripped)
    return "".join(lines)


# --- Default output is unchanged ---------------------------------------------


def test_the_default_indent_is_two_spaces() -> None:
    assert DEFAULT_INDENT == "  "
    assert FormatJob(job_id=1, text="", source="JSON", target="JSON").indent == "  "


@pytest.mark.parametrize(("source", "target", "text"), list(_GOLDEN))
def test_two_spaces_writes_the_output_on_main(
    engine: str, source: str, target: str, text: str
) -> None:
    del engine
    golden = _GOLDEN[(source, target, text)]
    assert format_document(text, source, target) == golden
    assert format_document(text, source, target, indent="  ") == golden


# --- JSON ----------------------------------------------------------------------


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_json_writes_each_level_with_the_unit(unit: str) -> None:
    expected = _deeper(_GOLDEN[("json", "json", _JSON)], unit)
    assert format_document(_JSON, "json", "json", indent=unit) == expected
    assert format_json(_JSON, indent=unit) == expected


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_the_character_walk_path_writes_the_unit(unit: str) -> None:
    expected = _deeper(_GOLDEN[("json", "json", _JSON)], unit)
    assert _format_json_walk_first(_JSON, indent=unit) == expected


def test_json_that_takes_the_walk_writes_the_unit(monkeypatch) -> None:
    monkeypatch.setattr(
        format_module, "_json_fast_value", lambda _s: format_module._CHARACTER_WALK
    )
    expected = _deeper(_GOLDEN[("json", "json", _JSON)], _TAB)
    assert format_json(_JSON, indent=_TAB) == expected


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_sort_keys_writes_the_unit(unit: str) -> None:
    text = '{"b": 1, "a": {"d": 2, "c": [3]}}'
    expected = _deeper(format_json(text, sort_keys=True), unit)
    assert format_document(text, "json", "json", sort_keys=True, indent=unit) == (
        expected
    )
    assert expected.index('"a"') < expected.index('"b"')


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_yaml_to_json_writes_the_unit(unit: str) -> None:
    expected = _deeper(_GOLDEN[("yaml", "json", _YAML_FOR_JSON)], unit)
    assert format_document(_YAML_FOR_JSON, "yaml", "json", indent=unit) == expected


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_minify_ignores_the_unit(unit: str) -> None:
    compact = format_document(_JSON, "json", "json", minify=True)
    assert format_document(_JSON, "json", "json", minify=True, indent=unit) == compact
    assert "\n" not in compact


# --- YAML ----------------------------------------------------------------------

# Eric: at four spaces each dash sits two in and its item starts at four.
_YAML_FOUR = (
    "name: Ann\ntags:\n  - a\n  - k:\n      - 1\n      - 2\n    m:\n        x: 1\n"
    '  -   - 3\n      - 4\nnote: "first\\n  kept\\nlast\\n"\nempty: {}\n'
)


def test_yaml_at_four_spaces_puts_each_dash_two_in(engine: str) -> None:
    """Mapping 4, sequence 4, offset 2: "a:\\n  - 1\\n  - b: 2"."""
    del engine
    assert format_document(_YAML, "yaml", "yaml", indent=_FOUR) == _YAML_FOUR
    assert format_yaml(_YAML, indent=_FOUR) == _YAML_FOUR


def test_yaml_at_four_spaces_is_ruamels_mapping_4_sequence_4_offset_2() -> None:
    """The bytes are ruamel's pure emitter with those three settings."""
    yaml = format_module._block_dumper(YAML(typ="safe", pure=True))
    yaml.indent(mapping=4, sequence=4, offset=2)
    buffer = StringIO()
    yaml.dump(YAML(typ="safe", pure=True).load(_YAML), buffer)
    assert buffer.getvalue() == _YAML_FOUR


def test_eric_s_four_space_sample(engine: str) -> None:
    del engine
    assert format_document("a: [1, {b: 2}]\n", "yaml", "yaml", indent=_FOUR) == (
        "a:\n  - 1\n  - b: 2\n"
    )


def test_a_tab_writes_yaml_at_two_spaces(engine: str) -> None:
    del engine
    golden = _GOLDEN[("yaml", "yaml", _YAML)]
    assert format_document(_YAML, "yaml", "yaml", indent=_TAB) == golden
    assert "\t" not in golden
    to_yaml = _GOLDEN[("json", "yaml", _JSON_FOR_YAML)]
    assert format_document(_JSON_FOR_YAML, "json", "yaml", indent=_TAB) == to_yaml


def test_json_to_yaml_at_four_spaces(engine: str) -> None:
    del engine
    assert format_document(_JSON_FOR_YAML, "json", "yaml", indent=_FOUR) == (
        "name: Ann\ntags:\n  - a\n  - k:\n      - 1\n      - 2\nn: 1.5\n"
    )


_NESTED = (
    "jobs:\n- name: build\n  steps:\n  - run: make\n    env:\n      A: '1'\n"
    "  - [x, y]\n- name: test\n  matrix:\n    os:\n    - mac\n    - win\n"
    "script: |\n  echo one\n    echo two\n\n  echo three\n"
    "folded: >\n  a\n  b\n"
)


@pytest.mark.parametrize("unit", ["  ", _FOUR])
def test_yaml_is_the_same_with_and_without_clib(
    monkeypatch: pytest.MonkeyPatch, unit: str
) -> None:
    """Nested lists, maps inside lists, and block scalars, on both engines."""
    if not format_module._clib_available():
        pytest.skip("ruamel.yaml.clib is not installed")
    with_clib = format_document(_NESTED, "yaml", "yaml", indent=unit)
    from_json = format_document(
        json.dumps(YAML(typ="safe").load(_NESTED)), "json", "yaml", indent=unit
    )
    monkeypatch.setattr(ruamel_main, "CParser", None)
    monkeypatch.setattr(ruamel_main, "CEmitter", None)
    monkeypatch.setattr(format_module, "_CLIB_READY", False)
    assert format_document(_NESTED, "yaml", "yaml", indent=unit) == with_clib
    assert (
        format_document(
            json.dumps(YAML(typ="safe").load(_NESTED)), "json", "yaml", indent=unit
        )
        == from_json
    )


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_a_block_scalar_keeps_its_content(engine: str, unit: str) -> None:
    """Only the indent may change: every value loads back the same."""
    del engine
    written = format_document(_NESTED, "yaml", "yaml", indent=unit)
    loaded = YAML(typ="safe", pure=True).load(written)
    assert loaded == YAML(typ="safe", pure=True).load(_NESTED)
    assert loaded["script"] == "echo one\n  echo two\n\necho three\n"
    assert loaded["folded"] == "a b\n"


def test_yaml_too_deep_for_ruamel_writes_the_unit(engine: str) -> None:
    """The explicit-stack dump for deep YAML steps by the unit too."""
    del engine
    depth = format_module._RUAMEL_SAFE_DEPTH + 10
    text = "".join(f"{'  ' * level}k{level}:\n" for level in range(depth))
    text += "  " * depth + "leaf: 1\n"
    two = format_document(text, "yaml", "yaml")
    four = format_document(text, "yaml", "yaml", indent=_FOUR)
    assert_same_text(four, _deeper(two, _FOUR))
    assert_same_text(format_document(text, "yaml", "yaml", indent=_TAB), two)


# --- XML and HTML --------------------------------------------------------------


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_xml_adds_the_unit_per_level(unit: str) -> None:
    written = format_document(_XML, "xml", "xml", indent=unit)
    assert written == format_xml(_XML, indent=unit)
    assert written == (
        f'<?xml version="1.0"?>\n<root a="1">\n{unit}<item>\n'
        f"{unit * 2}<name>  Ann  </name>\n"
        f'{unit * 2}<t xml:space="preserve">\n  x\n   y </t>\n'
        f"{unit * 2}<!-- one\n     two -->\n{unit}</item>\n{unit}<e/>\n</root>\n"
    )


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_html_adds_the_unit_per_level(unit: str) -> None:
    written = format_document(_HTML, "html", "html", indent=unit)
    assert written == format_html(_HTML, indent=unit)
    assert written == _deeper(_GOLDEN[("html", "html", _HTML)], unit).replace(
        # The second line of pre and of the comment is content, not indent.
        f"{unit * 2}b</pre>",
        "    b</pre>",
    ).replace(f"{unit}d -->", "   d -->")


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_markup_whitespace_inside_content_is_unchanged(unit: str) -> None:
    """Text, pre, xml:space="preserve" and comments keep every byte."""
    xml = format_document(_XML, "xml", "xml", indent=unit)
    for kept in (
        "<name>  Ann  </name>",
        '<t xml:space="preserve">\n  x\n   y </t>',
        "<!-- one\n     two -->",
    ):
        assert kept in xml
    html = format_document(_HTML, "html", "html", indent=unit)
    for kept in ("<pre>  a\n    b</pre>", "<!-- c\n   d -->", "<p>Hi <b>there</b></p>"):
        assert kept in html
    tabbed = format_document(
        '<a>\n\t<b xml:space="preserve">\t1\n\t\t2</b></a>', "xml", "xml", indent=unit
    )
    assert '<b xml:space="preserve">\t1\n\t\t2</b>' in tabbed


# --- Mask and the worker -------------------------------------------------------


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_masking_writes_the_unit(unit: str) -> None:
    text = '{"user": {"email": "ann@example.com", "id": 7}}'
    plain = mask_document(text, "json", "json", MaskRules())
    masked = mask_document(text, "json", "json", MaskRules(), indent=unit)
    assert masked.text == _deeper(plain.text, unit)
    assert MASK in masked.text
    assert masked.masked_count == plain.masked_count


def test_masked_yaml_at_four_spaces() -> None:
    text = "user:\n  emails:\n  - ann@example.com\n"
    masked = mask_document(text, "yaml", "yaml", MaskRules(), indent=_FOUR)
    assert masked.text == f"user:\n    emails:\n      - {MASK}\n"


@pytest.mark.parametrize("unit", [_FOUR, _TAB])
def test_the_worker_formats_with_the_jobs_indent(unit: str) -> None:
    events: SimpleQueue = SimpleQueue()
    job = FormatJob(job_id=3, text=_JSON, source="JSON", target="JSON", indent=unit)
    _execute(job, events)
    events.get_nowait()
    done = events.get_nowait()
    assert isinstance(done, SuccessEvent)
    assert done.text == _deeper(_GOLDEN[("json", "json", _JSON)], unit)
    assert done.folds != b""


def test_a_masked_worker_job_formats_with_the_jobs_indent() -> None:
    events: SimpleQueue = SimpleQueue()
    job = FormatJob(
        job_id=4,
        text='{"a": {"email": "ann@example.com"}}',
        source="JSON",
        target="JSON",
        mask=MaskRules(),
        indent=_TAB,
    )
    _execute(job, events)
    events.get_nowait()
    done = events.get_nowait()
    assert isinstance(done, SuccessEvent)
    assert done.text == f'{{\n\t"a": {{\n\t\t"email": "{MASK}"\n\t}}\n}}\n'
