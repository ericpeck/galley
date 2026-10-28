"""Syntax colors in the code editors (spec §3, §5.4)."""

from collections.abc import Callable, Iterator

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPalette,
    QTextCursor,
    QTextDocument,
)

from galley.app.editor import CodeEditor
from galley.app.highlighter import DARK_SYNTAX, LIGHT_SYNTAX, SyntaxHighlighter
from galley.app.theme import (
    ACCENT_INK,
    ACCENT_INK_DARK,
    COLORS,
    ERROR_TINT,
    STRING,
    TEXT_CODE_VALUE,
    TEXT_MUTED,
    light_palette,
)

KEY = (ACCENT_INK, False)
STR = (STRING, False)
VALUE = (TEXT_CODE_VALUE, True)
PUNCT = (TEXT_MUTED, False)

_JSON = '{\n  "name": "Ada",\n  "size": -1.5e3,\n  "ok": true,\n  "gone": null\n}'
_YAML = (
    "name: Ada  # who\n"
    "size: 42\n"
    "ok: true\n"
    "gone: null\n"
    "tags:\n"
    '  - "quoted"\n'
    "  - plain words\n"
    "flow: {a: 1, b: [x, false]}\n"
)

_MIN_CONTRAST = 4.5
# macOS native dark Base and Midlight (current-line band), read from the
# cocoa platform with the dark scheme.
_MAC_DARK_BASE = "#171717"
_MAC_DARK_BAND = "#343434"


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        if srgb <= 0.04045:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast(foreground: str, background: str) -> float:
    one = _luminance(QColor(foreground))
    two = _luminance(QColor(background))
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _dark_palette(palette: QPalette) -> QPalette:
    dark = QPalette(palette)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        dark.setColor(group, QPalette.ColorRole.Base, QColor(_MAC_DARK_BASE))
        dark.setColor(group, QPalette.ColorRole.Text, QColor("#FFFFFF"))
        dark.setColor(group, QPalette.ColorRole.Midlight, QColor(_MAC_DARK_BAND))
    return dark


@pytest.fixture
def color_scheme(qapp) -> Iterator[Callable[[bool], None]]:
    """Set the color scheme, then the palette that scheme would bring.

    Offscreen CI ignores the OS appearance and has no dark theme, so the
    scheme request alone leaves the palette light. The test supplies the
    macOS dark palette, or #57's light one, and restores both afterwards.
    """
    hints = QGuiApplication.styleHints()
    original = QPalette(qapp.palette())

    def apply(dark: bool) -> None:
        if dark:
            hints.setColorScheme(Qt.ColorScheme.Dark)
            qapp.setPalette(_dark_palette(original))
        else:
            hints.setColorScheme(Qt.ColorScheme.Light)
            qapp.setPalette(light_palette(original))

    yield apply
    hints.unsetColorScheme()
    qapp.setPalette(original)


def _editor_in(qtbot, apply: Callable[[bool], None], dark: bool) -> CodeEditor:
    """Editor with JSON text after the scheme switch, palette checked."""
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText(_JSON)
    apply(dark)
    _wait_for_palette(qtbot, editor, dark)
    return editor


def _base(editor: CodeEditor) -> QColor:
    return editor.palette().color(QPalette.ColorRole.Base)


def _wait_for_palette(qtbot, editor: CodeEditor, dark: bool) -> None:
    """Wait until the editor carries the application palette for ``dark``."""
    app_palette = QGuiApplication.palette()

    def settled() -> bool:
        return (_base(editor).lightness() < 128) == dark and all(
            editor.palette().color(role) == app_palette.color(role)
            for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Midlight)
        )

    qtbot.waitUntil(settled)


def _style(
    document: QTextDocument, line: int, needle: str, *, last: bool = False
) -> tuple[str, bool]:
    """Color and boldness covering every character of ``needle`` on ``line``."""
    block = document.findBlockByNumber(line - 1)
    text = block.text()
    start = text.rindex(needle) if last else text.index(needle)
    # Format ranges count UTF-16 units.
    start = len(text[:start].encode("utf-16-le")) // 2
    end = start + len(needle.encode("utf-16-le")) // 2
    for item in block.layout().formats():
        if item.start <= start and end <= item.start + item.length:
            fmt = item.format
            color = fmt.foreground().color().name().upper()
            return color, fmt.fontWeight() == QFont.Weight.Bold
    return "", False


def _formatted(document: QTextDocument) -> bool:
    block = document.firstBlock()
    while block.isValid():
        if block.layout().formats():
            return True
        block = block.next()
    return False


def _document(text: str, mode: str) -> tuple[QTextDocument, SyntaxHighlighter]:
    document = QTextDocument()
    # Without a layout the document emits no contentsChange to highlight on.
    document.documentLayout()
    highlighter = SyntaxHighlighter(document, mode)
    document.setPlainText(text)
    return document, highlighter


def test_json_colors_keys_strings_values_and_punctuation() -> None:
    document, _highlighter = _document(_JSON, "JSON")

    assert _style(document, 2, '"name"') == KEY
    assert _style(document, 2, '"Ada"') == STR
    assert _style(document, 3, "-1.5e3") == VALUE
    assert _style(document, 4, "true") == VALUE
    assert _style(document, 5, "null") == VALUE
    assert _style(document, 1, "{") == PUNCT
    assert _style(document, 2, ":") == PUNCT
    assert _style(document, 2, ",") == PUNCT
    assert _style(document, 6, "}") == PUNCT


def test_json_brackets_inside_strings_stay_string() -> None:
    document, _highlighter = _document('["a, [b]: c", 1]', "JSON")

    assert _style(document, 1, '"a, [b]: c"') == STR
    assert _style(document, 1, "1") == VALUE
    assert _style(document, 1, "[") == PUNCT
    assert _style(document, 1, ",", last=True) == PUNCT
    assert _style(document, 1, "]", last=True) == PUNCT


def test_yaml_colors_keys_scalars_and_markers() -> None:
    document, _highlighter = _document(_YAML, "YAML")

    assert _style(document, 1, "name") == KEY
    assert _style(document, 1, "Ada") == STR
    assert _style(document, 1, "# who") == PUNCT
    assert _style(document, 2, "42") == VALUE
    assert _style(document, 3, "true") == VALUE
    assert _style(document, 4, "null") == VALUE
    assert _style(document, 2, ":") == PUNCT
    assert _style(document, 6, "-") == PUNCT
    assert _style(document, 6, '"quoted"') == STR
    assert _style(document, 7, "plain words") == STR


def test_yaml_flow_collections_use_json_colors() -> None:
    document, _highlighter = _document(_YAML, "YAML")

    assert _style(document, 8, "flow") == KEY
    assert _style(document, 8, "{") == PUNCT
    assert _style(document, 8, "a") == KEY
    assert _style(document, 8, "1") == VALUE
    assert _style(document, 8, "[") == PUNCT
    assert _style(document, 8, "x") == STR
    assert _style(document, 8, "false") == VALUE


def test_yaml_block_scalar_is_string_until_dedent() -> None:
    text = "steps:\n  - run: |\n      echo: hi\n      # not a comment\n    name: 1\n"
    document, _highlighter = _document(text, "YAML")

    assert _style(document, 2, "|") == PUNCT
    assert _style(document, 3, "echo: hi") == STR
    assert _style(document, 4, "# not a comment") == STR
    assert _style(document, 5, "name") == KEY
    assert _style(document, 5, "1") == VALUE


def test_formats_land_after_characters_outside_the_bmp() -> None:
    document, _highlighter = _document('{"\U0001f600": 1, "k": null}', "JSON")

    assert _style(document, 1, '"\U0001f600"') == KEY
    assert _style(document, 1, '"k"') == KEY
    assert _style(document, 1, "null") == VALUE


def test_switching_mode_recolors_existing_text() -> None:
    document, highlighter = _document("name: Ada\n", "JSON")
    assert _style(document, 1, "name") == ("", False)

    highlighter.set_mode("YAML")

    assert highlighter.mode() == "YAML"
    assert _style(document, 1, "name") == KEY


def test_unknown_mode_is_rejected() -> None:
    document = QTextDocument()
    with pytest.raises(ValueError):
        SyntaxHighlighter(document, "TOML")  # type: ignore[arg-type]
    highlighter = SyntaxHighlighter(document)
    with pytest.raises(ValueError):
        highlighter.set_mode("TOML")  # type: ignore[arg-type]


def test_off_switch_clears_colors_and_on_restores_them(qtbot) -> None:
    document, highlighter = _document(_JSON, "JSON")
    assert _formatted(document)

    highlighter.set_enabled(False)

    assert not highlighter.is_enabled()
    assert not _formatted(document)
    document.setPlainText(_JSON + "\n")
    assert not _formatted(document)

    highlighter.set_enabled(True)

    assert highlighter.is_enabled()
    qtbot.waitUntil(lambda: _style(document, 2, '"name"') == KEY)


def test_editor_guesses_mode_from_first_character() -> None:
    editor = CodeEditor()

    editor.setPlainText("\n  " + _JSON)
    assert editor.syntax_mode() == "JSON"
    assert _style(editor.document(), 3, '"name"') == KEY

    editor.setPlainText(_YAML)
    assert editor.syntax_mode() == "YAML"
    assert _style(editor.document(), 1, "name") == KEY


def test_editor_guess_recolors_after_a_partial_edit() -> None:
    editor = CodeEditor()
    editor.setPlainText('"a": 1\n')
    assert editor.syntax_mode() == "YAML"

    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.insertText("{")

    assert editor.syntax_mode() == "JSON"
    assert _style(editor.document(), 1, "1") == VALUE


def test_editor_mode_set_explicitly_is_kept() -> None:
    editor = CodeEditor()
    editor.set_syntax_mode("YAML")

    editor.setPlainText('{"a": [1, 2]}')

    assert editor.syntax_mode() == "YAML"
    assert _style(editor.document(), 1, '"a"') == KEY


def test_editor_off_switch() -> None:
    editor = CodeEditor()
    editor.setPlainText(_JSON)
    assert editor.syntax_colors_enabled()

    editor.set_syntax_colors_enabled(False)

    assert not editor.syntax_colors_enabled()
    assert not _formatted(editor.document())


def test_colors_leave_line_bands_and_text_alone(qtbot) -> None:
    editor = CodeEditor()
    editor.setPlainText(_JSON)
    editor.set_problem_lines({3})
    editor.set_syntax_mode("YAML")
    editor.set_syntax_colors_enabled(False)
    editor.set_syntax_colors_enabled(True)
    qtbot.waitUntil(lambda: _formatted(editor.document()))

    assert editor.toPlainText() == _JSON
    assert not editor.document().isModified()
    band = editor.palette().color(QPalette.ColorRole.Midlight).name().upper()
    bands = {
        selection.format.background().color().name().upper()
        for selection in editor.extraSelections()
    }
    assert bands == {band, ERROR_TINT}


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_syntax_colors_read_on_the_base_they_are_drawn_on(
    qtbot, color_scheme, dark: bool
) -> None:
    editor = _editor_in(qtbot, color_scheme, dark)
    base = _base(editor)
    band = editor.palette().color(QPalette.ColorRole.Midlight)
    assert base.name().upper() == (_MAC_DARK_BASE if dark else COLORS["editor.bg"])

    document = editor.document()
    drawn = {
        "key": _style(document, 2, '"name"'),
        "string": _style(document, 2, '"Ada"'),
        "value": _style(document, 3, "-1.5e3"),
        "literal": _style(document, 4, "true"),
        "punctuation": _style(document, 1, "{"),
    }
    expected = DARK_SYNTAX if dark else LIGHT_SYNTAX
    assert drawn["key"][0] == expected.key
    assert drawn["string"][0] == expected.string
    assert drawn["value"] == (expected.value, True)
    assert drawn["literal"] == (expected.value, True)
    assert drawn["punctuation"][0] == expected.punctuation
    for role, (color, _bold) in drawn.items():
        for surface in (base, band):
            ratio = _contrast(color, surface.name())
            assert ratio >= _MIN_CONTRAST, (role, color, surface.name(), ratio)


def test_highlighter_follows_base_lightness() -> None:
    document, highlighter = _document(_JSON, "JSON")

    highlighter.follow_base(QColor("#171717"))

    assert highlighter.colors() == DARK_SYNTAX
    assert _style(document, 2, '"name"') == (ACCENT_INK_DARK, False)

    highlighter.follow_base(QColor(COLORS["editor.bg"]))

    assert highlighter.colors() == LIGHT_SYNTAX
    assert _style(document, 2, '"name"') == KEY


def test_editor_recolors_when_the_scheme_changes(qtbot, color_scheme) -> None:
    editor = _editor_in(qtbot, color_scheme, dark=False)
    assert _style(editor.document(), 2, '"name"') == KEY

    color_scheme(True)
    _wait_for_palette(qtbot, editor, dark=True)

    assert _style(editor.document(), 2, '"name"') == (ACCENT_INK_DARK, False)

    color_scheme(False)
    _wait_for_palette(qtbot, editor, dark=False)

    assert _style(editor.document(), 2, '"name"') == KEY


# --- XML (#63 slice 3): Bill's mapping onto the existing roles -------------

PLAIN = ("", False)
_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE catalog SYSTEM "catalog.dtd">\n'
    "<!-- fake catalog -->\n"
    '<catalog xmlns:geo="urn:fake" id="c1">\n'
    "  <item sku='A-1'>Widget &amp; co</item>\n"
    "  <geo:point/>\n"
    "  <note><![CDATA[a < b]]></note>\n"
    "  <?app run?>\n"
    "</catalog>\n"
)


def test_xml_colors_names_attributes_values_and_markup() -> None:
    document, _highlighter = _document(_XML, "XML")

    assert _style(document, 4, "catalog") == KEY
    assert _style(document, 5, "item") == KEY
    assert _style(document, 5, "item", last=True) == KEY
    assert _style(document, 6, "geo:point") == KEY
    assert _style(document, 9, "catalog") == KEY
    assert _style(document, 4, "xmlns:geo") == PUNCT
    assert _style(document, 4, "id") == PUNCT
    assert _style(document, 4, "=") == PUNCT
    assert _style(document, 5, "<") == PUNCT
    assert _style(document, 5, "</") == PUNCT
    assert _style(document, 6, "/>") == PUNCT
    assert _style(document, 4, '"c1"') == STR
    assert _style(document, 5, "'A-1'") == STR
    assert _style(document, 1, '<?xml version="1.0" encoding="UTF-8"?>') == PUNCT
    assert _style(document, 2, '<!DOCTYPE catalog SYSTEM "catalog.dtd">') == PUNCT
    assert _style(document, 3, "<!-- fake catalog -->") == PUNCT
    assert _style(document, 8, "<?app run?>") == PUNCT
    assert _style(document, 7, "<![CDATA[") == PUNCT
    assert _style(document, 7, "]]>") == PUNCT
    assert _style(document, 7, "a < b") == PLAIN
    assert _style(document, 5, "Widget") == PLAIN
    assert _style(document, 5, "&amp;") == PLAIN


def test_xml_carries_open_markup_across_lines() -> None:
    text = (
        "<r>\n"
        "  <!-- a\n"
        "  long note -->\n"
        '  <a b="one\n'
        "  two\" c='x\n"
        "  y'\n"
        '     d="z">text</a>\n'
        "  <![CDATA[ x\n"
        "  <not-a-tag> ]]>\n"
        "  <?pi first\n"
        "  second?>\n"
        "  <!DOCTYPE fake [\n"
        "    <!ELEMENT r ANY>\n"
        "  ]>\n"
        "  <after/>\n"
        "</r>\n"
    )
    document, _highlighter = _document(text, "XML")

    assert _style(document, 3, "long note -->") == PUNCT
    assert _style(document, 4, '"one') == STR
    assert _style(document, 5, 'two"') == STR
    assert _style(document, 5, "c") == PUNCT
    assert _style(document, 6, "y'") == STR
    assert _style(document, 7, "d") == PUNCT
    assert _style(document, 7, '"z"') == STR
    assert _style(document, 7, "text") == PLAIN
    assert _style(document, 9, "<not-a-tag>") == PLAIN
    assert _style(document, 9, "]]>") == PUNCT
    assert _style(document, 11, "second?>") == PUNCT
    assert _style(document, 13, "<!ELEMENT r ANY>") == PUNCT
    assert _style(document, 14, "]>") == PUNCT
    assert _style(document, 15, "after") == KEY
    assert _style(document, 16, "r") == KEY


def test_editing_a_line_recolors_the_lines_after_it() -> None:
    document, _highlighter = _document("<r>\n<a/>\n</r>\n", "XML")
    assert _style(document, 2, "a") == KEY

    cursor = QTextCursor(document.findBlockByNumber(0))
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertText("<!--")

    assert _style(document, 2, "<a/>") == PUNCT


def test_editor_guesses_xml_from_a_leading_angle_bracket() -> None:
    editor = CodeEditor()

    editor.setPlainText("\n  " + _XML)
    assert editor.syntax_mode() == "XML"
    assert _style(editor.document(), 5, "catalog") == KEY

    editor.setPlainText(_JSON)
    assert editor.syntax_mode() == "JSON"


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_xml_colors_pass_contrast_on_their_base(
    qtbot, color_scheme, dark: bool
) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText(_XML)
    color_scheme(dark)
    _wait_for_palette(qtbot, editor, dark)
    base = _base(editor)
    band = editor.palette().color(QPalette.ColorRole.Midlight)
    expected = DARK_SYNTAX if dark else LIGHT_SYNTAX
    document = editor.document()
    drawn = {
        "element": _style(document, 4, "catalog")[0],
        "attribute": _style(document, 4, "id")[0],
        "value": _style(document, 4, '"c1"')[0],
        "comment": _style(document, 3, "<!-- fake catalog -->")[0],
    }
    assert drawn == {
        "element": expected.key,
        "attribute": expected.punctuation,
        "value": expected.string,
        "comment": expected.punctuation,
    }
    assert _style(document, 5, "Widget") == PLAIN
    for role, color in drawn.items():
        for surface in (base, band):
            ratio = _contrast(color, surface.name())
            assert ratio >= _MIN_CONTRAST, (role, color, surface.name(), ratio)


# --- HTML (#63 slice 5): the XML mapping, with raw bodies --------------------

_HTML = (
    "<!DOCTYPE html>\n"
    "<html lang=en>\n"
    "<head><title>Fake page</title>\n"
    '<SCRIPT type="text/javascript">\n'
    '  if (a < b && c > d) { x = "<p>"; }\n'
    "</Script>\n"
    "<style>p > b { color: red }</style>\n"
    "</head>\n"
    "<body><input disabled value=on>\n"
    "<pre class=x>  <b>kept</b> <!-- not a comment here -->\n"
    "  </PRE>\n"
    "<p>a <b>b</b> &amp; c<!-- note --></p></body></html>\n"
)


def test_html_colors_names_attributes_values_and_markup() -> None:
    document, _highlighter = _document(_HTML, "HTML")

    assert _style(document, 1, "<!DOCTYPE html>") == PUNCT
    assert _style(document, 2, "html") == KEY
    assert _style(document, 2, "lang") == PUNCT
    assert _style(document, 2, "en") == STR
    assert _style(document, 4, "SCRIPT") == KEY
    assert _style(document, 4, '"text/javascript"') == STR
    assert _style(document, 9, "input") == KEY
    assert _style(document, 9, "disabled") == PUNCT
    assert _style(document, 9, "on") == STR
    assert _style(document, 12, "<!-- note -->") == PUNCT
    assert _style(document, 12, "&amp;") == PLAIN
    assert _style(document, 12, "a") == PLAIN


def test_html_script_style_and_pre_bodies_stay_plain_across_lines() -> None:
    document, _highlighter = _document(_HTML, "HTML")

    assert _style(document, 5, 'if (a < b && c > d) { x = "<p>"; }') == PLAIN
    assert _style(document, 6, "Script") == KEY
    assert _style(document, 7, "p > b { color: red }") == PLAIN
    assert _style(document, 7, "style", last=True) == KEY
    assert _style(document, 10, "<b>kept</b> <!-- not a comment here -->") == PLAIN
    assert _style(document, 11, "PRE") == KEY
    assert _style(document, 12, "b") == KEY


def test_xml_keeps_script_markup_colored() -> None:
    # Raw bodies are HTML's. In XML a script element is markup like any other.
    document, _highlighter = _document('<script>\n  <b x="1"/>\n</script>\n', "XML")
    assert _style(document, 2, "b") == KEY
    assert _style(document, 2, '"1"') == STR


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_html_colors_pass_contrast_on_their_base(
    qtbot, color_scheme, dark: bool
) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText(_HTML)
    assert editor.syntax_mode() == "HTML"
    color_scheme(dark)
    _wait_for_palette(qtbot, editor, dark)
    base = _base(editor)
    band = editor.palette().color(QPalette.ColorRole.Midlight)
    expected = DARK_SYNTAX if dark else LIGHT_SYNTAX
    document = editor.document()
    drawn = {
        "element": _style(document, 2, "html")[0],
        "attribute": _style(document, 2, "lang")[0],
        "value": _style(document, 2, "en")[0],
        "doctype": _style(document, 1, "<!DOCTYPE html>")[0],
    }
    assert drawn == {
        "element": expected.key,
        "attribute": expected.punctuation,
        "value": expected.string,
        "doctype": expected.punctuation,
    }
    assert _style(document, 5, "if (a < b") == PLAIN
    for role, color in drawn.items():
        for surface in (base, band):
            ratio = _contrast(color, surface.name())
            assert ratio >= _MIN_CONTRAST, (role, color, surface.name(), ratio)
