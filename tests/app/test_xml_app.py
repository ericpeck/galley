"""XML in the app (#63 slice 3): pickers, greyed commands, Format, and Open.

Specs: Erik's slice 3 prompt and Bill's UI criteria on #63. Fake data only.
"""

import typing

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QToolButton,
    QWidget,
)

import galley.app.actions as actions_module
from galley.app.actions import COMING_SOON, ShellActions
from galley.app.copy_as import MASK_HELP
from galley.app.editor import CodeEditor
from galley.app.find_bar import KEYS_ONLY_TIP
from galley.app.limits import (
    JSON_BUSY_BYTES,
    JSON_PROGRESS_BYTES,
    JSON_WARNING_BYTES,
    XML_BUSY_BYTES,
    XML_PROGRESS_BYTES,
    XML_WARNING_BYTES,
    YAML_BUSY_BYTES,
    YAML_PROGRESS_BYTES,
    YAML_WARNING_BYTES,
)
from galley.app.main import create_main_window
from galley.app.main_window import _busy_progress_bytes, _warning_bytes
from galley.app.pane import EMPTY_HEADING, PaneRole, PaneWidget
from galley.app.problem_text import _xml_text, display_problem, message_for
from galley.app.problems_pane import ProblemsPane
from galley.app.recent_files import remember_recent_file
from galley.app.shortcuts import ESCAPE_AS_STRING, UNESCAPE_STRING
from galley.app.status_bar import INFO_RESERVE
from galley.app.status_message import StatusMessage
from galley.core.problems import (
    XML_PROBLEM_CODES,
    Problem,
    ProblemCode,
    is_xml_code,
)

NOT_AVAILABLE = "Not available for XML"
SORT_KEYS_REASON = "Sort keys doesn't apply to XML"
_XML = '<catalog><item id="1"><name>Widget</name></item></catalog>'
_XML_FORMATTED = (
    '<catalog>\n  <item id="1">\n    <name>Widget</name>\n  </item>\n</catalog>\n'
)
# A mismatched end tag on line 3. Fake data only.
_BROKEN = "<catalog>\n  <item>\n  </name>\n</catalog>\n"


def _show(qtbot) -> QMainWindow:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _pane(window: QMainWindow, name: str) -> PaneWidget:
    pane = window.findChild(PaneWidget, f"{name}Pane")
    assert isinstance(pane, PaneWidget)
    return pane


def _status(window: QMainWindow) -> StatusMessage:
    status = window.findChild(StatusMessage, "statusMessage")
    assert isinstance(status, StatusMessage)
    return status


def _choices(pane: PaneWidget) -> list[str]:
    combo = pane.format_combo
    return [combo.itemText(index) for index in range(combo.count())]


def _choose(pane: PaneWidget, label: str) -> None:
    combo = pane.format_combo
    index = combo.findText(label)
    assert index >= 0, label
    combo.setCurrentIndex(index)


def _greyed(window: QMainWindow) -> dict[str, QAction]:
    actions: ShellActions = window._actions
    return {
        "Convert from Python": actions.convert_python,
        "Escape as String": actions.escape_string,
        "Unescape String": actions.unescape_string,
        "Sort keys": actions.sort_keys,
        "Copy As › Original": actions.copy_as_original,
        "Copy As › Masked": actions.copy_as_masked,
        "Copy As › Random fake": actions.copy_as_fake,
        "Copy As": window._copy_as_menu.menuAction(),
    }


def _toolbar_button(window: QMainWindow, action: QAction) -> QToolButton:
    for button in window.findChildren(QToolButton):
        if button.defaultAction() is action:
            return button
    raise AssertionError(f"no toolbar button for {action.text()}")


# --- Pickers (B1) -----------------------------------------------------------


def test_input_lists_json_yaml_xml_and_output_follows(qtbot) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    result = _pane(window, "output")
    assert _choices(source) == ["JSON", "YAML", "XML", "HTML"]
    assert _choices(result) == ["JSON", "YAML"]

    _choose(result, "YAML")
    _choose(source, "XML")

    assert _choices(result) == ["XML"]
    assert result.format_name() == "XML"
    assert window._status.info_text() == "XML → XML"

    _choose(source, "JSON")

    assert _choices(result) == ["JSON", "YAML"]
    assert result.format_name() == "YAML"
    assert window._status.info_text() == "JSON → YAML"


def test_the_pickers_read_the_same_to_screen_readers(qtbot) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    assert source.format_combo.accessibleName() == "Input as"
    _choose(source, "XML")
    result = _pane(window, "output")
    assert result.format_combo.accessibleName() == "Output as"
    assert result.format_combo.currentText() == "XML"


def test_the_status_reserve_fits_xml(qtbot) -> None:
    assert "XML → XML" in INFO_RESERVE


# --- Greyed-out commands (B2) ------------------------------------------------


def test_json_and_yaml_commands_grey_out_for_xml(qtbot) -> None:
    window = _show(qtbot)
    _pane(window, "input").editor.setPlainText(_XML)
    _pane(window, "output").editor.setPlainText(_XML_FORMATTED)
    _choose(_pane(window, "input"), "XML")

    for name, action in _greyed(window).items():
        reason = SORT_KEYS_REASON if name == "Sort keys" else NOT_AVAILABLE
        assert action.isVisible(), name
        assert not action.isEnabled(), name
        assert action.toolTip() == reason, name
        assert action.statusTip() == reason, name
    for action, reason in (
        (window._actions.convert_python, NOT_AVAILABLE),
        (window._actions.sort_keys, SORT_KEYS_REASON),
    ):
        button = _toolbar_button(window, action)
        assert not button.isEnabled()
        assert button.toolTip() == reason
        assert button.accessibleDescription() == reason


def test_commands_come_back_with_their_own_tips(qtbot) -> None:
    window = _show(qtbot)
    _pane(window, "input").editor.setPlainText('{"a": 1}')
    _pane(window, "output").editor.setPlainText('{\n  "a": 1\n}\n')
    source = _pane(window, "input")
    _choose(source, "XML")
    _choose(source, "YAML")

    actions = window._actions
    for name, action in _greyed(window).items():
        assert action.isEnabled(), name
        assert action.toolTip() not in (NOT_AVAILABLE, SORT_KEYS_REASON), name
    assert actions.escape_string.toolTip() == "Escape as String"
    assert actions.copy_as_masked.toolTip() == MASK_HELP
    assert actions.copy_as_fake.statusTip() == MASK_HELP
    assert actions.convert_python.statusTip() == ""
    button = _toolbar_button(window, actions.sort_keys)
    assert button.accessibleDescription() == ""


def test_coming_soon_and_fold_commands_keep_their_own_state(qtbot) -> None:
    window = _show(qtbot)
    _choose(_pane(window, "input"), "XML")
    actions = window._actions
    assert actions.indent.toolTip() == COMING_SOON
    assert not actions.indent.isEnabled()
    for action in (actions.expand_all, actions.collapse_all):
        assert not action.isEnabled()
        assert action.toolTip().endswith("nothing to fold")


def test_sort_keys_keeps_its_check_while_greyed_out(qtbot) -> None:
    window = _show(qtbot)
    sort_keys = window._actions.sort_keys
    sort_keys.setChecked(True)
    source = _pane(window, "input")
    _choose(source, "XML")
    assert sort_keys.isChecked()
    assert not sort_keys.isEnabled()
    _choose(source, "JSON")
    assert sort_keys.isChecked()
    assert sort_keys.isEnabled()


@pytest.mark.parametrize("keys", [ESCAPE_AS_STRING, UNESCAPE_STRING])
def test_a_greyed_shortcut_does_nothing(qtbot, keys: QKeySequence) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    _choose(source, "XML")
    source.editor.setPlainText(_XML)
    output = _pane(window, "output").editor
    output.setPlainText("<kept/>\n")
    status = _status(window).text()
    source.editor.setFocus()
    qtbot.waitUntil(source.editor.hasFocus)

    qtbot.keyClick(source.editor, keys[0].key(), keys[0].keyboardModifiers())

    assert output.toPlainText() == "<kept/>\n"
    assert source.editor.toPlainText() == _XML
    assert _status(window).text() == status
    problems = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(problems, ProblemsPane)
    assert problems.messages() == []


def test_the_release_check_is_unchanged(qtbot, capsys) -> None:
    window = _show(qtbot)
    _choose(_pane(window, "input"), "XML")
    blocks = [
        block
        for block in (
            actions_module.release_failure(),
            actions_module.not_yet_failure(),
        )
        if block is not None
    ]
    # "Not available for XML" is a valid state, not Coming soon or Not yet.
    assert actions_module.main() == (1 if blocks else 0)
    output = capsys.readouterr().out
    assert output == ("\n".join(blocks) + "\n" if blocks else "")
    assert NOT_AVAILABLE not in output


# --- Find bar -------------------------------------------------------------


def test_keys_only_stays_visible_but_greyed_out_for_xml(qtbot) -> None:
    window = _show(qtbot)
    result = _pane(window, "output")
    bar = result.find_bar
    box = bar.keys_only
    assert box is not None
    result.editor.setPlainText(_XML_FORMATTED)
    bar.open()
    box.setChecked(True)
    source = _pane(window, "input")

    _choose(source, "XML")

    assert box.isVisible()
    assert not box.isEnabled()
    assert box.isChecked()
    assert box.toolTip() == "Keys only doesn't apply to XML"
    assert box.accessibleDescription() == "Keys only doesn't apply to XML"
    assert not bar.keys_only_active()

    _choose(source, "JSON")

    assert box.isEnabled()
    assert box.toolTip() == KEYS_ONLY_TIP
    assert box.accessibleDescription() == ""
    assert bar.keys_only_active()


def test_find_matches_xml_text_while_keys_only_is_greyed_out(qtbot) -> None:
    window = _show(qtbot)
    result = _pane(window, "output")
    bar = result.find_bar
    assert bar.keys_only is not None
    bar.keys_only.setChecked(True)
    _choose(_pane(window, "input"), "XML")
    result.editor.setPlainText(_XML_FORMATTED)
    bar.open()
    bar.field.setText("Widget")
    bar.search()
    assert bar.match_count() == 1


# --- Format, problems, and states -------------------------------------------


def _format_xml(qtbot, window: QMainWindow, text: str, status: str) -> None:
    source = _pane(window, "input")
    _choose(source, "XML")
    source.editor.setPlainText(text)
    window._actions.format.trigger()
    qtbot.waitUntil(lambda: _status(window).text() == status, timeout=20000)


def test_format_xml_shows_valid_xml_and_its_line_count(qtbot) -> None:
    window = _show(qtbot)
    window._actions.sort_keys.setChecked(True)
    _format_xml(qtbot, window, _XML, "Valid XML")
    output = _pane(window, "output")
    assert output.editor.toPlainText() == _XML_FORMATTED
    # The count includes the empty line after the final newline, as for JSON.
    assert output.count_label.text() == "6 lines"
    assert window._status.info_text() == "XML → XML"
    assert output.editor.syntax_mode() == "XML"
    assert _pane(window, "input").editor.syntax_mode() == "XML"
    assert not output.editor.has_fold_ranges()


def test_an_xml_error_shows_in_the_gutter_and_problems(qtbot) -> None:
    window = _show(qtbot)
    _format_xml(qtbot, window, _XML, "Valid XML")
    _format_xml(qtbot, window, _BROKEN, "1 problem")
    problems = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(problems, ProblemsPane)
    qtbot.waitUntil(problems.isVisible)
    assert problems.lines() == [3]
    assert problems.messages() == ["Line 3 closes </name>, but <item> is still open."]
    assert _pane(window, "input").editor.problem_lines() == {3}
    # Output keeps its last good result.
    assert _pane(window, "output").editor.toPlainText() == _XML_FORMATTED


@pytest.mark.parametrize(
    ("text", "line", "place", "message"),
    [
        # The text ends inside a tag: its line, and the column of its <.
        ('<r>\n  <item id="1', 2, "Line 2, Col 3", "The tag on line 2 isn't finished."),
        # Found only at the end of the text: the line alone (#131).
        ("<!-- c -->\n", 2, "Line 2", "There's no root element."),
    ],
)
def test_end_of_text_problems_show_their_place(
    qtbot, text: str, line: int, place: str, message: str
) -> None:
    window = _show(qtbot)
    _format_xml(qtbot, window, text, "1 problem")
    problems = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(problems, ProblemsPane)
    qtbot.waitUntil(problems.isVisible)
    assert problems.lines() == [line]
    assert problems.messages() == [message]
    row = problems.list_widget().itemWidget(problems.list_widget().item(0))
    assert row is not None
    texts = [label.text() for label in row.findChildren(QLabel)]
    assert f"Error · {place}" in texts


def test_xml_size_bands_use_the_yaml_placeholders() -> None:
    assert (XML_BUSY_BYTES, XML_PROGRESS_BYTES, XML_WARNING_BYTES) == (
        YAML_BUSY_BYTES,
        YAML_PROGRESS_BYTES,
        YAML_WARNING_BYTES,
    )
    for source in ("XML", "xml"):
        assert _busy_progress_bytes(source, "<a/>") == (
            XML_BUSY_BYTES,
            XML_PROGRESS_BYTES,
        )
        assert _warning_bytes(source) == XML_WARNING_BYTES
    assert _busy_progress_bytes("JSON", "{}") == (JSON_BUSY_BYTES, JSON_PROGRESS_BYTES)
    assert _warning_bytes("JSON") == JSON_WARNING_BYTES


def test_xml_colors_turn_off_past_the_xml_busy_size(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("<a>\n" + "x\n" * (XML_BUSY_BYTES // 2) + "</a>\n")
    assert editor.syntax_mode() == "XML"
    assert not editor.syntax_colors_enabled()
    editor.setPlainText('{"a":\n' + " 1\n" * (XML_BUSY_BYTES // 2) + "}\n")
    assert editor.syntax_mode() == "JSON"
    assert editor.syntax_colors_enabled()


def test_the_empty_state_names_every_format(qtbot) -> None:
    assert EMPTY_HEADING == "Paste JSON, YAML, XML, or HTML, or drop a file here."
    window = _show(qtbot)
    texts = [
        label.text()
        for label in _pane(window, "input").findChildren(QWidget)
        if hasattr(label, "text") and callable(label.text)
    ]
    assert any(str(text).startswith(EMPTY_HEADING) for text in texts)


# --- Problem sentences (B4) -------------------------------------------------


@pytest.mark.parametrize(
    ("problem", "sentence"),
    [
        (
            Problem("xml_mismatched_tag", 3, "b", 3, found="c"),
            "Line 3 closes </c>, but <b> is still open.",
        ),
        (Problem("xml_unclosed", 2, "b"), "<b> opened on line 2 is never closed."),
        (Problem("xml_unfinished_tag", 3), "The tag on line 3 isn't finished."),
        (
            Problem("xml_entity_declared", 1),
            "Galley doesn't load DTD entities. Remove the <!ENTITY> on line 1.",
        ),
        (
            Problem("xml_undefined_entity", 4, "nbsp"),
            "Unknown entity &nbsp; on line 4.",
        ),
        (
            Problem("xml_duplicate_attribute", 5, "id"),
            "Attribute id appears twice in this tag.",
        ),
        (
            Problem("xml_unbound_prefix", 6, "x"),
            "Prefix x isn't declared. Add an xmlns:x attribute.",
        ),
        (
            Problem("xml_after_root", 9),
            "Text after the root element on line 9. XML allows one root element.",
        ),
        (Problem("xml_no_root", 1), "There's no root element."),
        (
            Problem("xml_bad_token", 7),
            "Unexpected character on line 7. Write < as &lt; and & as &amp; in text.",
        ),
        (Problem("xml_other", 8), "Couldn't read this XML on line 8."),
    ],
)
def test_xml_problem_sentences(problem: Problem, sentence: str) -> None:
    assert message_for(problem) == sentence
    row = display_problem(problem)
    assert row.accessible == f"Line {problem.lineno}: {sentence}"


def test_every_xml_code_has_its_own_sentence() -> None:
    every_code = typing.get_args(ProblemCode)
    assert set(XML_PROBLEM_CODES) == {c for c in every_code if c.startswith("xml_")}
    sentences = {}
    for code in XML_PROBLEM_CODES:
        problem = Problem(code, 4, "b", 2, found="c")
        assert is_xml_code(code)
        sentences[code] = _xml_text(problem, code)
        assert message_for(problem) == sentences[code]
    assert all(sentence.endswith(".") for sentence in sentences.values())
    assert len(set(sentences.values())) == len(XML_PROBLEM_CODES)


def test_an_unknown_xml_code_raises() -> None:
    problem = Problem("xml_bogus", 1)  # type: ignore[arg-type]
    assert not is_xml_code("xml_bogus")
    with pytest.raises(AssertionError):
        _xml_text(problem, "xml_bogus")  # type: ignore[arg-type]
    with pytest.raises(AssertionError):
        message_for(problem)


def test_long_xml_names_are_cut_like_keys() -> None:
    name = "n" * 60
    sentence = message_for(Problem("xml_unclosed", 2, name))
    assert sentence == f"<{'n' * 40}…> opened on line 2 is never closed."


# --- Open, Open Recent, and drag and drop -------------------------------------


@pytest.mark.parametrize("suffix", [".xml", ".xsd", ".xsl", ".xslt", ".svg", ".xhtml"])
def test_open_selects_xml_for_its_extensions(qtbot, tmp_path, suffix: str) -> None:
    path = tmp_path / f"feed{suffix}"
    path.write_bytes(_XML.encode("utf-8"))
    window = _show(qtbot)
    window.open_path(path)
    _wait_opened(qtbot, window)
    assert _pane(window, "input").format_name() == "XML"
    assert _choices(_pane(window, "output")) == ["XML"]


def test_open_recent_selects_xml(qtbot, tmp_path) -> None:
    path = tmp_path / "recent.xml"
    path.write_bytes(_XML.encode("utf-8"))
    remember_recent_file(path)
    window = _show(qtbot)
    entries = [
        action for action in window._recent_menu.actions() if action.data() is not None
    ]
    entries[0].trigger()
    _wait_opened(qtbot, window)
    assert _pane(window, "input").format_name() == "XML"


def test_a_dropped_xml_file_selects_xml(qtbot, tmp_path) -> None:
    path = tmp_path / "dropped.xml"
    path.write_bytes(_XML.encode("utf-8"))
    window = _show(qtbot)
    viewport = _pane(window, "input").editor.viewport()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    point = viewport.rect().center()
    copy = Qt.DropAction.CopyAction
    left = Qt.MouseButton.LeftButton
    none = Qt.KeyboardModifier.NoModifier
    for event in (
        QDragEnterEvent(point, copy, mime, left, none),
        QDragMoveEvent(point, copy, mime, left, none),
        QDropEvent(QPoint(point).toPointF(), copy, mime, left, none),
    ):
        QApplication.sendEvent(viewport, event)
        assert event.isAccepted()
    _wait_opened(qtbot, window)
    assert _pane(window, "input").format_name() == "XML"


def _wait_opened(qtbot, window: QMainWindow) -> None:
    qtbot.waitUntil(
        lambda: (
            window._loading_path is None
            and _pane(window, "input").editor.toPlainText() == _XML
        )
    )


def test_a_standalone_output_pane_offers_the_value_formats(qtbot) -> None:
    pane = PaneWidget(PaneRole.OUTPUT)
    qtbot.addWidget(pane)
    assert _choices(pane) == ["JSON", "YAML"]
    pane.set_format_choices(("XML",), "XML")
    assert _choices(pane) == ["XML"]
    assert pane.format_name() == "XML"
