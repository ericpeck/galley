"""HTML in the app (#63 slice 5): pickers, greyed commands, Format, and Open.

Also the shared 2 MB Format limit for XML and HTML (Eric, #63). Specs:
Erik's slice 5 prompt, Bill's slice 5 UI criteria, and the slice 5
checklist on #63. Fake data only.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QAction, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QToolButton

from galley.app.actions import ShellActions
from galley.app.editor import CodeEditor
from galley.app.limits import (
    HTML_BUSY_BYTES,
    HTML_PROGRESS_BYTES,
    HTML_WARNING_BYTES,
    MARKUP_FORMAT_LIMIT_BYTES,
    YAML_BUSY_BYTES,
    YAML_PROGRESS_BYTES,
    YAML_WARNING_BYTES,
)
from galley.app.main import create_main_window
from galley.app.main_window import (
    _busy_progress_bytes,
    _format_limit_bytes,
    _warning_bytes,
)
from galley.app.pane import PaneWidget
from galley.app.problem_text import _html_text, display_problem, message_for
from galley.app.problems_pane import ProblemsPane
from galley.app.recent_files import remember_recent_file
from galley.app.shortcuts import ESCAPE_AS_STRING, UNESCAPE_STRING
from galley.app.status_bar import INFO_RESERVE
from galley.app.status_message import StatusMessage
from galley.core.problems import HTML_PROBLEM_CODES, Problem, is_html_code

NOT_AVAILABLE = "Not available for HTML"
SORT_KEYS_REASON = "Sort keys doesn't apply to HTML"
_HTML = "<!DOCTYPE html><ul><li>one<li>two</ul><p>Fake <b>text</b></p>"
_HTML_FORMATTED = (
    "<!DOCTYPE html>\n<ul>\n  <li>one\n  <li>two\n</ul>\n<p>Fake <b>text</b></p>\n"
)
# A span never closed on line 2 and a stray </p> on line 4. Fake data only.
_BROKEN = "<div>\n  <span>\n</div>\n</p>\n"


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


def _problems(window: QMainWindow) -> ProblemsPane:
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    return pane


# --- Pickers (B1) -----------------------------------------------------------


def test_input_lists_html_fourth_and_output_follows(qtbot) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    result = _pane(window, "output")
    assert _choices(source) == ["JSON", "YAML", "XML", "HTML"]
    assert _choices(result) == ["JSON", "YAML"]

    _choose(result, "YAML")
    _choose(source, "HTML")

    assert _choices(result) == ["HTML"]
    assert result.format_name() == "HTML"
    assert window._status.info_text() == "HTML → HTML"

    _choose(source, "XML")
    assert _choices(result) == ["XML"]

    _choose(source, "JSON")

    assert _choices(result) == ["JSON", "YAML"]
    assert result.format_name() == "YAML"


def test_the_status_reserve_fits_html() -> None:
    assert "HTML → HTML" in INFO_RESERVE


# --- Greyed-out commands (B2) ------------------------------------------------


def test_json_and_yaml_commands_grey_out_for_html(qtbot) -> None:
    window = _show(qtbot)
    _pane(window, "input").editor.setPlainText(_HTML)
    _pane(window, "output").editor.setPlainText(_HTML_FORMATTED)
    _choose(_pane(window, "input"), "HTML")

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


def test_commands_come_back_after_html(qtbot) -> None:
    window = _show(qtbot)
    _pane(window, "input").editor.setPlainText('{"a": 1}')
    _pane(window, "output").editor.setPlainText('{\n  "a": 1\n}\n')
    source = _pane(window, "input")
    _choose(source, "HTML")
    _choose(source, "JSON")
    for name, action in _greyed(window).items():
        assert action.isEnabled(), name
        assert action.toolTip() not in (NOT_AVAILABLE, SORT_KEYS_REASON), name


@pytest.mark.parametrize("keys", [ESCAPE_AS_STRING, UNESCAPE_STRING])
def test_a_greyed_shortcut_does_nothing(qtbot, keys) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    _choose(source, "HTML")
    source.editor.setPlainText(_HTML)
    output = _pane(window, "output").editor
    output.setPlainText("<p>kept</p>\n")
    status = _status(window).text()
    source.editor.setFocus()
    qtbot.waitUntil(source.editor.hasFocus)

    qtbot.keyClick(source.editor, keys[0].key(), keys[0].keyboardModifiers())

    assert output.toPlainText() == "<p>kept</p>\n"
    assert source.editor.toPlainText() == _HTML
    assert _status(window).text() == status
    assert _problems(window).messages() == []


def test_keys_only_is_greyed_out_for_html(qtbot) -> None:
    window = _show(qtbot)
    bar = _pane(window, "output").find_bar
    box = bar.keys_only
    assert box is not None
    box.setChecked(True)
    _choose(_pane(window, "input"), "HTML")
    assert not box.isEnabled()
    assert box.isChecked()
    assert box.toolTip() == "Keys only doesn't apply to HTML"
    assert box.accessibleDescription() == "Keys only doesn't apply to HTML"
    assert not bar.keys_only_active()


# --- Format, problems, and states -------------------------------------------


def _format_html(qtbot, window: QMainWindow, text: str, status: str) -> None:
    source = _pane(window, "input")
    _choose(source, "HTML")
    source.editor.setPlainText(text)
    window._actions.format.trigger()
    qtbot.waitUntil(lambda: _status(window).text() == status, timeout=20000)


def test_format_html_says_formatted_not_valid(qtbot) -> None:
    window = _show(qtbot)
    window._actions.sort_keys.setChecked(True)
    _format_html(qtbot, window, _HTML, "Formatted HTML")
    output = _pane(window, "output")
    assert output.editor.toPlainText() == _HTML_FORMATTED
    assert window._status.info_text() == "HTML → HTML"
    assert output.editor.syntax_mode() == "HTML"
    assert _pane(window, "input").editor.syntax_mode() == "HTML"
    assert not output.editor.has_fold_ranges()


def test_every_html_problem_shows_in_the_gutter_and_problems(qtbot) -> None:
    window = _show(qtbot)
    _format_html(qtbot, window, _HTML, "Formatted HTML")
    _format_html(qtbot, window, _BROKEN, "2 problems")
    problems = _problems(window)
    qtbot.waitUntil(problems.isVisible)
    assert problems.lines() == [2, 4]
    assert problems.messages() == [
        "<span> opened on line 2 is never closed.",
        "Line 4 closes </p>, but nothing open matches it.",
    ]
    assert _pane(window, "input").editor.problem_lines() == {2, 4}
    # Output keeps its last good result.
    assert _pane(window, "output").editor.toPlainText() == _HTML_FORMATTED


def test_html_size_bands_use_the_yaml_placeholders() -> None:
    assert (HTML_BUSY_BYTES, HTML_PROGRESS_BYTES, HTML_WARNING_BYTES) == (
        YAML_BUSY_BYTES,
        YAML_PROGRESS_BYTES,
        YAML_WARNING_BYTES,
    )
    for source in ("HTML", "html"):
        assert _busy_progress_bytes(source, "<p>") == (
            HTML_BUSY_BYTES,
            HTML_PROGRESS_BYTES,
        )
        assert _warning_bytes(source) == HTML_WARNING_BYTES


def test_html_colors_turn_off_past_the_html_busy_size(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("<!DOCTYPE html>\n" + "<p>x</p>\n" * (HTML_BUSY_BYTES // 8))
    assert editor.syntax_mode() == "HTML"
    assert not editor.syntax_colors_enabled()


# --- The shared 2 MB Format limit (XML and HTML) --------------------------------


def _over_the_limit(fmt: str) -> str:
    """Just over 2 MB of fake markup in ``fmt``."""
    if fmt == "XML":
        item = "<item><name>Fake</name></item>\n"
        head, tail = "<catalog>\n", "</catalog>\n"
    else:
        item = "<p>Fake <b>text</b></p>\n"
        head, tail = "<!DOCTYPE html>\n<body>\n", "</body>\n"
    count = MARKUP_FORMAT_LIMIT_BYTES // len(item) + 1
    return head + item * count + tail


def test_xml_and_html_share_one_two_megabyte_limit() -> None:
    assert MARKUP_FORMAT_LIMIT_BYTES == 2_000_000
    assert _format_limit_bytes("XML") == MARKUP_FORMAT_LIMIT_BYTES
    assert _format_limit_bytes("html") == MARKUP_FORMAT_LIMIT_BYTES
    assert _format_limit_bytes("JSON") is None
    assert _format_limit_bytes("YAML") is None


@pytest.mark.parametrize("fmt", ["XML", "HTML"])
def test_format_refuses_markup_over_two_megabytes(qtbot, fmt: str) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    _choose(source, fmt)
    text = _over_the_limit(fmt)
    size = len(text.encode("utf-8"))
    assert MARKUP_FORMAT_LIMIT_BYTES < size < 2_100_000
    source.editor.setPlainText(text)
    output = _pane(window, "output").editor
    output.setPlainText("kept\n")

    window._actions.format.trigger()

    assert _status(window).text() == (
        f"This document is too large to format ({size / 1_000_000:.1f} MB). "
        "The limit is 2 MB."
    )
    assert not window._formatting
    assert window.findChild(QMessageBox, "sizeWarning") is None
    assert output.toPlainText() == "kept\n"
    assert source.editor.toPlainText() == text


def _at_the_limit() -> str:
    """Fake HTML of exactly 2 MB, in short lines so the editor stays fast."""
    head = "<!DOCTYPE html>\n"
    line = "<p>Fake text</p>\n"
    count = (MARKUP_FORMAT_LIMIT_BYTES - len(head)) // len(line) - 1
    used = len(head) + count * len(line)
    pad = MARKUP_FORMAT_LIMIT_BYTES - used - len("<p></p>\n")
    return head + line * count + "<p>" + "x" * pad + "</p>\n"


def test_markup_at_the_limit_still_formats_after_the_size_prompt(qtbot) -> None:
    window = _show(qtbot)
    source = _pane(window, "input")
    _choose(source, "HTML")
    text = _at_the_limit()
    assert len(text.encode("utf-8")) == MARKUP_FORMAT_LIMIT_BYTES
    source.editor.setPlainText(text)
    window._actions.format.trigger()
    # At the limit, the usual size prompt asks, as for any large document.
    qtbot.waitUntil(lambda: window.findChild(QMessageBox, "sizeWarning") is not None)
    assert "too large" not in _status(window).text()


# --- Problem sentences (Bill's slice 5 wording) -------------------------------


@pytest.mark.parametrize(
    ("problem", "sentence"),
    [
        (
            Problem("html_unexpected_close", 7, "div", 1),
            "Line 7 closes </div>, but nothing open matches it.",
        ),
        (
            Problem("html_unclosed", 2, "section", 3),
            "<section> opened on line 2 is never closed.",
        ),
        # The tag name is shown as written.
        (
            Problem("html_unclosed", 5, "DIV", 1),
            "<DIV> opened on line 5 is never closed.",
        ),
    ],
)
def test_html_problem_sentences(problem: Problem, sentence: str) -> None:
    assert message_for(problem) == sentence
    assert display_problem(problem).accessible == f"Line {problem.lineno}: {sentence}"


def test_every_html_code_has_a_sentence_and_an_unknown_one_raises() -> None:
    assert HTML_PROBLEM_CODES == ("html_unexpected_close", "html_unclosed")
    for code in HTML_PROBLEM_CODES:
        assert is_html_code(code)
        assert _html_text(Problem(code, 3, "p"), code).endswith(".")
    assert not is_html_code("html_bogus")
    with pytest.raises(AssertionError):
        _html_text(Problem("html_bogus", 1), "html_bogus")  # type: ignore[arg-type]


# --- Detection, Open, Open Recent, and drag and drop ---------------------------


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("<!DOCTYPE html>\n<p>a</p>", "HTML"),
        ("  <html lang=en>", "HTML"),
        ("<!-- generated -->\n<head><title>t</title></head>", "HTML"),
        ("<body>\n<p>a", "HTML"),
        ('<?xml version="1.0"?>\n<feed/>', "XML"),
        ("<!-- generated -->\n<feed/>", "XML"),
        ("<p>a fragment</p>", "XML"),
    ],
)
def test_the_editor_tells_html_from_xml(text: str, mode: str) -> None:
    editor = CodeEditor()
    editor.setPlainText(text)
    assert editor.syntax_mode() == mode


def test_editing_the_doctype_switches_the_colors() -> None:
    editor = CodeEditor()
    editor.setPlainText("<feed/>\n")
    assert editor.syntax_mode() == "XML"
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.insertText("<!DOCTYPE html>\n")
    assert editor.syntax_mode() == "HTML"


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("page.html", _HTML),
        ("page.HTM", _HTML),
        # No mapped extension: the opening decides, past a leading comment.
        ("page.txt", "<!-- fake -->\n<html><body><p>a</p></body></html>"),
    ],
)
def test_open_selects_html(qtbot, tmp_path: Path, name: str, text: str) -> None:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    window = _show(qtbot)
    window.open_path(path)
    _wait_opened(qtbot, window, text)
    assert _pane(window, "input").format_name() == "HTML"
    assert _choices(_pane(window, "output")) == ["HTML"]


def test_open_recent_selects_html(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "recent.html"
    path.write_bytes(_HTML.encode("utf-8"))
    remember_recent_file(path)
    window = _show(qtbot)
    entries = [
        action for action in window._recent_menu.actions() if action.data() is not None
    ]
    entries[0].trigger()
    _wait_opened(qtbot, window, _HTML)
    assert _pane(window, "input").format_name() == "HTML"


def test_a_dropped_html_file_selects_html(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "dropped.htm"
    path.write_bytes(_HTML.encode("utf-8"))
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
    _wait_opened(qtbot, window, _HTML)
    assert _pane(window, "input").format_name() == "HTML"


def _wait_opened(qtbot, window: QMainWindow, text: str) -> None:
    qtbot.waitUntil(
        lambda: (
            window._loading_path is None
            and _pane(window, "input").editor.toPlainText() == text
        )
    )
