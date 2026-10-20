"""Syntax colors in the code editors (spec §3, §5.4)."""

import pytest
from PySide6.QtGui import QFont, QTextDocument

from galley.app.editor import CodeEditor
from galley.app.highlighter import SyntaxHighlighter
from galley.app.theme import (
    ACCENT_INK,
    ERROR_TINT,
    LINE_CURRENT,
    STRING,
    TEXT_CODE_VALUE,
    TEXT_MUTED,
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
    bands = {
        selection.format.background().color().name().upper()
        for selection in editor.extraSelections()
    }
    assert bands == {LINE_CURRENT, ERROR_TINT}
