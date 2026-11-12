"""Stale output (#91 D2, Bill's D3 and his D2 answers on #91).

Once Output holds a result, an Input edit or a change to a setting
Format reads marks it stale: the Output tab shows a `Stale` tag (clock
icon plus word), the footer says `Last good result`, and a success status
goes back to `Ready`. Output text keeps its full strength. A failed Format
adds the 6a banner. The next successful run clears all of it.
"""

import pytest
from PySide6.QtCore import QDate, QDateTime, QLocale, QTime
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import QComboBox, QLabel
from tests.app.compare_inline import inline_compare as inline_compare

from galley.app.main import create_main_window
from galley.app.parse_session import ParseSession
from galley.app.stale_cue import banner_text, stale_tooltip, stale_when
from galley.app.style import stale_style
from galley.app.theme import contrast_ratio, stale_colors

# Fake data only.
_JSON = '{"name": "Ada Example", "id": 7}'
_BROKEN = '{"name": "Ada Example",'
_PYTHON = "{'name': 'Ada Example', 'id': 7}"
_TIP = "Output doesn't match the current input and settings. Press {} to update it."


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1280, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _child(window, kind, name: str):
    found = window.findChild(kind, name)
    assert isinstance(found, kind), name
    return found


def _tag(window) -> QLabel:
    return _child(window, QLabel, "outputStaleTag")


def _banner(window) -> QLabel:
    return _child(window, QLabel, "outputStaleBanner")


def _footer(window) -> str:
    return _child(window, QLabel, "outputCount").text()


def _state(window) -> str:
    return window._status.state_text()


def _input(window):
    return window._input_pane.editor


def _output_text(window) -> str:
    return window._output_pane.own_editor.toPlainText()


def _run(qtbot, window, command: str) -> None:
    _action(window, command).trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=15000)


def _format_json(qtbot, window) -> str:
    _input(window).setPlainText(_JSON)
    _run(qtbot, window, "Format")
    assert _output_text(window) != ""
    assert not _tag(window).isVisibleTo(window)
    return _output_text(window)


def _assert_stale(window, action: str = "Format") -> None:
    tag = _tag(window)
    assert tag.isVisibleTo(window)
    assert tag.text() == "Stale"
    assert tag.accessibleName() == "Stale"
    assert tag.toolTip() == _TIP.format(action)
    assert tag.accessibleDescription() == tag.toolTip()
    assert _footer(window) == "Last good result"


def _assert_fresh(window) -> None:
    assert not _tag(window).isVisibleTo(window)
    assert not _banner(window).isVisibleTo(window)
    assert _footer(window) != "Last good result"


# --- What makes Output stale -------------------------------------------------


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_an_input_edit_after_format_marks_output_stale(
    qtbot, install_scheme, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    shown = _format_json(qtbot, window)
    lines = _footer(window)
    assert _state(window) == "Valid JSON"
    _input(window).insertPlainText(" ")
    _assert_stale(window)
    assert _state(window) == "Ready"
    # Full strength: Output keeps its text and its editor stays enabled.
    assert _output_text(window) == shown
    assert window._output_pane.own_editor.isEnabled()
    assert not _banner(window).isVisibleTo(window)
    _run(qtbot, window, "Format")
    _assert_fresh(window)
    assert _footer(window) == lines
    assert _state(window) == "Valid JSON"


@pytest.mark.parametrize("picker", ["inputFormat", "outputFormat"])
def test_a_picker_change_marks_output_stale(qtbot, picker: str) -> None:
    window = _show(qtbot)
    _format_json(qtbot, window)
    _child(window, QComboBox, picker).setCurrentText("YAML")
    _assert_stale(window)


def test_sort_keys_marks_output_stale(qtbot) -> None:
    window = _show(qtbot)
    _format_json(qtbot, window)
    _action(window, "Sort keys").trigger()
    _assert_stale(window)


def test_the_indent_choice_marks_output_stale(qtbot) -> None:
    window = _show(qtbot)
    _format_json(qtbot, window)
    indent = _child(window, QComboBox, "indentSize")
    indent.setCurrentIndex((indent.currentIndex() + 1) % indent.count())
    _assert_stale(window)


def test_output_stays_stale_after_undo_until_the_next_run(qtbot) -> None:
    """Bill's Q2: undo back to the same Input still shows Stale."""
    window = _show(qtbot)
    _format_json(qtbot, window)
    _input(window).insertPlainText(" ")
    _input(window).undo()
    assert _input(window).toPlainText() == _JSON
    _assert_stale(window)


def test_typing_in_output_does_not_mark_it_stale(qtbot) -> None:
    window = _show(qtbot)
    _format_json(qtbot, window)
    window._output_pane.own_editor.insertPlainText(" ")
    _assert_fresh(window)


def test_an_empty_output_has_nothing_stale(qtbot) -> None:
    window = _show(qtbot)
    _format_json(qtbot, window)
    _input(window).insertPlainText(" ")
    _assert_stale(window)
    window._output_pane.own_editor.clear()
    _assert_fresh(window)
    assert _footer(window) == ""


def test_an_input_edit_with_no_result_shows_nothing(qtbot) -> None:
    window = _show(qtbot)
    _input(window).setPlainText(_JSON)
    _assert_fresh(window)
    assert _state(window) == "Ready"


# --- Each command names itself ------------------------------------------------


@pytest.mark.parametrize(
    ("command", "text"),
    [
        ("Minify", _JSON),
        ("Convert from Python", _PYTHON),
        ("Escape as String", _JSON),
        ("Unescape String", '"{\\"id\\": 7}"\n'),
    ],
)
def test_the_tooltip_names_the_command_that_made_output(
    qtbot, command: str, text: str
) -> None:
    window = _show(qtbot)
    _input(window).setPlainText(text)
    _run(qtbot, window, command)
    assert _output_text(window) != ""
    _assert_fresh(window)
    status = _state(window)
    _input(window).insertPlainText(" ")
    _assert_stale(window, command)
    assert _state(window) == "Ready", status


def test_stale_tooltip_wording() -> None:
    assert stale_tooltip("Format") == _TIP.format("Format")
    assert stale_tooltip("Escape as String") == _TIP.format("Escape as String")


# --- Ready replaces only a success status ------------------------------------


def test_a_problem_count_is_kept_when_output_goes_stale(qtbot) -> None:
    window = _show(qtbot)
    _format_json(qtbot, window)
    _input(window).setPlainText(_BROKEN)
    _run(qtbot, window, "Format")
    count = _state(window)
    assert "problem" in count
    _input(window).insertPlainText(" ")
    _assert_stale(window)
    assert _state(window) == count


# --- The 6a banner ------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_failed_format_shows_the_banner_until_the_next_run(
    qtbot, install_scheme, scheme: str, monkeypatch
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    shown = _format_json(qtbot, window)
    heard: list[str] = []
    announce = window._status.announce
    monkeypatch.setattr(
        window._status, "announce", lambda text: (heard.append(text), announce(text))
    )
    _input(window).setPlainText(_BROKEN)
    # Typing shows the tag without a live announcement (Bill, Q4).
    assert not any("Stale" in text or "last good" in text for text in heard)
    _run(qtbot, window, "Format")
    banner = _banner(window)
    assert banner.isVisibleTo(window)
    assert banner.text().startswith("Showing the last good result, from ")
    assert banner.text().endswith(". Fix the input and press Format to update it.")
    assert banner.wordWrap()
    assert banner.accessibleName() == banner.text()
    assert [text for text in heard if text == banner.text()] == [banner.text()]
    _assert_stale(window)
    assert _output_text(window) == shown
    # It sits above the editor and is never a Tab stop (Bill, Q6).
    assert banner.y() < window._output_pane.own_editor.y()
    assert banner.focusPolicy() == banner.focusPolicy().NoFocus
    _input(window).setPlainText(_JSON)
    _run(qtbot, window, "Format")
    _assert_fresh(window)


def test_no_banner_without_an_earlier_result(qtbot) -> None:
    window = _show(qtbot)
    _input(window).setPlainText(_BROKEN)
    _run(qtbot, window, "Format")
    assert _output_text(window) == ""
    _assert_fresh(window)


def test_the_banner_names_the_time_or_the_day_and_time() -> None:
    locale = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
    made = QDateTime(QDate(2026, 11, 12), QTime(10, 39))
    later = QDateTime(QDate(2026, 11, 12), QTime(15, 2))
    next_day = QDateTime(QDate(2026, 11, 13), QTime(9, 0))
    same_day = locale.toString(made.time(), QLocale.FormatType.ShortFormat)
    other_day = locale.toString(made, QLocale.FormatType.ShortFormat)
    assert stale_when(made, later, locale) == same_day
    assert stale_when(made, next_day, locale) == other_day
    assert other_day != same_day
    assert banner_text(same_day) == (
        f"Showing the last good result, from {same_day}. "
        "Fix the input and press Format to update it."
    )


# --- Look ---------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_tag_and_banner_colors_meet_the_contrast_bars(
    qtbot, install_scheme, scheme: str
) -> None:
    window = _show(qtbot)
    palette = install_scheme(scheme)
    window_color = palette.color(QPalette.ColorRole.Window)
    assert (window_color.lightness() < 128) is (scheme == "dark")
    fill, text, edge = stale_colors(palette)
    assert contrast_ratio(text, fill) >= 4.5
    assert contrast_ratio(edge, fill) >= 3.0
    assert contrast_ratio(edge, window_color) >= 3.0
    _format_json(qtbot, window)
    _input(window).insertPlainText(" ")
    for label in (_tag(window), _banner(window)):
        assert label.styleSheet() == stale_style(label.objectName())
        assert fill.name() in label.styleSheet()
        assert text.name() in label.styleSheet()


# --- Compare ------------------------------------------------------------------


def test_compare_hides_the_cue_and_brings_it_back(qtbot, inline_compare) -> None:
    del inline_compare
    window = _show(qtbot)
    _format_json(qtbot, window)
    _input(window).insertPlainText(" ")
    _assert_stale(window)
    compare = _action(window, "Compare")
    compare.trigger()
    assert compare.isChecked()
    assert not _tag(window).isVisibleTo(window)
    assert not _banner(window).isVisibleTo(window)
    compare.trigger()
    assert not compare.isChecked()
    _assert_stale(window)


def test_entering_and_leaving_compare_leaves_a_fresh_result_fresh(
    qtbot, inline_compare
) -> None:
    """Compare swaps Output's picker for Right's and puts it back: no change."""
    del inline_compare
    window = _show(qtbot)
    _format_json(qtbot, window)
    compare = _action(window, "Compare")
    compare.trigger()
    _child(window, QComboBox, "outputFormat").setCurrentText("YAML")
    compare.trigger()
    assert not compare.isChecked()
    _assert_fresh(window)


# --- Settings are the job's, not the controls' at the end (Erik, #208) ------

_FORMATTED = '{\n  "name": "Ada Example",\n  "id": 7\n}\n'


def _held_format(qtbot, window, monkeypatch) -> None:
    """Start a Format whose job finishes only when ``_finish_held`` says so."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    _input(window).setPlainText(_JSON)
    _action(window, "Format").trigger()
    assert window._formatting


def _finish_held(qtbot, window) -> None:
    window._session.finished_job_id = 1
    window._session.succeeded.emit(_FORMATTED)
    qtbot.waitUntil(lambda: not window._formatting)
    assert _output_text(window) == _FORMATTED


def _change_output_picker(window) -> None:
    _child(window, QComboBox, "outputFormat").setCurrentText("YAML")


def _change_input_picker(window) -> None:
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")


def _toggle_sort_keys(window) -> None:
    _action(window, "Sort keys").trigger()


def _change_indent(window) -> None:
    indent = _child(window, QComboBox, "indentSize")
    indent.setCurrentIndex((indent.currentIndex() + 1) % indent.count())


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize(
    "change",
    [_change_output_picker, _change_input_picker, _toggle_sort_keys, _change_indent],
    ids=["outputFormat", "inputFormat", "sortKeys", "indent"],
)
def test_a_setting_changed_during_format_leaves_output_stale(
    qtbot, install_scheme, monkeypatch, scheme: str, change
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _held_format(qtbot, window, monkeypatch)
    change(window)
    _finish_held(qtbot, window)
    _assert_stale(window)
    assert _state(window) == "Ready"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_setting_changed_and_back_during_format_leaves_output_fresh(
    qtbot, install_scheme, monkeypatch, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _held_format(qtbot, window, monkeypatch)
    _toggle_sort_keys(window)
    _toggle_sort_keys(window)
    _finish_held(qtbot, window)
    _assert_fresh(window)
    assert _state(window) == "Valid JSON"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_an_input_edit_after_convert_notices_shows_ready(
    qtbot, install_scheme, scheme: str
) -> None:
    """Notices count as a success status, so they give way to Ready (Bill)."""
    window = _show(qtbot)
    install_scheme(scheme)
    _input(window).setPlainText("{'nums': (1, 2), 3: 'x'}")
    _run(qtbot, window, "Convert from Python")
    notices = _state(window)
    assert "tuple" in notices
    _assert_fresh(window)
    _input(window).insertPlainText(" ")
    _assert_stale(window, "Convert from Python")
    assert _state(window) == "Ready"
