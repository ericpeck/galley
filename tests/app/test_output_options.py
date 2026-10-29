"""The Output Options popover (release check, Chief's brief; ui-spec 6h).

The popover is a second view of the toolbar's indent size picker and Sort
keys toggle. Settings live in the throwaway store from ``isolated_settings``.
"""

import io
from contextlib import redirect_stdout

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAction, QColor, QHelpEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QToolTip,
    QWidget,
)

from galley.app.actions import main, not_yet_failure
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.parse_session import ParseSession
from galley.app.problems_settings import (
    load_indent_size,
    load_sort_keys,
    problems_settings,
)
from galley.app.style import options_link_style, segment_style
from galley.app.theme import composite, contrast_ratio, segment_colors

_WAIT_MS = 5000
_WIDE_PX = 1600
_JSON = '{"b": 1, "a": 2}'
_YAML_TAB_REASON = "YAML indents with spaces."
_SEGMENTS = (("2", "2 spaces"), ("4", "4 spaces"), ("Tab", "Tab"))


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _link(window) -> QPushButton:
    button = window.findChild(QPushButton, "outputOptionsButton")
    assert isinstance(button, QPushButton)
    return button


def _popover(window) -> QFrame:
    popover = window.findChild(QFrame, "outputOptions")
    assert isinstance(popover, QFrame)
    return popover


def _open(qtbot, window) -> QFrame:
    qtbot.mouseClick(_link(window), Qt.MouseButton.LeftButton)
    popover = _popover(window)
    qtbot.waitUntil(popover.isVisible, timeout=_WAIT_MS)
    return popover


def _segment(window, text: str) -> QToolButton:
    found = window.findChild(QToolButton, f"outputOptionsIndent{text}")
    assert isinstance(found, QToolButton), text
    return found


def _sort_box(window) -> QCheckBox:
    box = window.findChild(QCheckBox, "outputOptionsSortKeys")
    assert isinstance(box, QCheckBox)
    return box


def _reset(window) -> QPushButton:
    button = window.findChild(QPushButton, "outputOptionsReset")
    assert isinstance(button, QPushButton)
    return button


def _label(window, name: str) -> QLabel:
    label = window.findChild(QLabel, name)
    assert isinstance(label, QLabel), name
    return label


def _picker(window) -> QComboBox:
    picker = window.findChild(QComboBox, "indentSize")
    assert isinstance(picker, QComboBox)
    return picker


def _format_picker(window, pane: str) -> QComboBox:
    combo = window.findChild(QComboBox, f"{pane}Format")
    assert isinstance(combo, QComboBox)
    return combo


def _choose(combo: QComboBox, text: str) -> None:
    index = combo.findText(text)
    assert index >= 0, text
    combo.setCurrentIndex(index)


def _editors(window) -> tuple[CodeEditor, CodeEditor]:
    source = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(source, CodeEditor)
    assert isinstance(output, CodeEditor)
    return source, output


def _sort_button(qtbot, window) -> QToolButton:
    """The toolbar's Sort keys button, with its label shown.

    The Windows runner's font sheds labels at the default width.
    """
    window.resize(_WIDE_PX, window.height())
    for button in window.findChildren(QToolButton):
        if button.defaultAction() is window._actions.sort_keys:
            qtbot.waitUntil(button.isVisible, timeout=_WAIT_MS)
            return button
    raise AssertionError("no toolbar button for Sort keys")


def _focused() -> QWidget:
    widget = QApplication.focusWidget()
    assert isinstance(widget, QWidget)
    return widget


def _press(qtbot, key: Qt.Key) -> None:
    qtbot.keyClick(_focused(), key)


def _checked_segments(window) -> list[str]:
    return [text for text, _name in _SEGMENTS if _segment(window, text).isChecked()]


# --- Opening and closing ----------------------------------------------------------


def test_the_options_link_opens_the_popover_anchored_to_it(qtbot) -> None:
    window = _show(qtbot)
    link = _link(window)
    assert not link.isChecked()
    popover = _open(qtbot, window)
    assert popover.windowType() == Qt.WindowType.Popup
    assert link.isCheckable()
    assert link.isChecked()
    assert window.statusBar().currentMessage() == ""
    # Above the link, starting at its left edge as in 6h, unless that would
    # run off the screen's right edge.
    anchor = link.mapToGlobal(QPoint(0, 0))
    frame = popover.frameGeometry()
    screen = link.screen().availableGeometry()
    assert frame.bottom() < anchor.y()
    assert frame.left() == min(anchor.x(), screen.right() + 1 - frame.width())
    assert screen.left() <= frame.left()
    assert frame.right() <= screen.right()


def test_the_header_names_the_popover_and_the_output_format(qtbot) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    title = _label(window, "outputOptionsTitle")
    assert title.text() == "Output options"
    assert title.font().bold()
    assert _label(window, "outputOptionsScope").text() == "Output · JSON"


def test_the_header_follows_the_output_picker_while_open(qtbot) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    scope = _label(window, "outputOptionsScope")
    _choose(_format_picker(window, "output"), "YAML")
    assert scope.text() == "Output · YAML"
    _choose(_format_picker(window, "output"), "JSON")
    assert scope.text() == "Output · JSON"
    for label in ("XML", "HTML"):
        _choose(_format_picker(window, "input"), label)
        assert scope.text() == f"Output · {label}"


def test_esc_closes_and_returns_focus_to_the_options_link(qtbot) -> None:
    window = _show(qtbot)
    popover = _open(qtbot, window)
    qtbot.waitUntil(lambda: _focused().window() is popover, timeout=_WAIT_MS)
    _press(qtbot, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not popover.isVisible(), timeout=_WAIT_MS)
    assert not _link(window).isChecked()
    qtbot.waitUntil(lambda: _focused() is _link(window), timeout=_WAIT_MS)


def test_esc_in_the_popover_closes_it_and_leaves_a_running_format(
    qtbot, monkeypatch
) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_JSON)
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    window._actions.format.trigger()
    assert window._formatting
    popover = _open(qtbot, window)
    qtbot.waitUntil(lambda: _focused().window() is popover, timeout=_WAIT_MS)
    _press(qtbot, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not popover.isVisible(), timeout=_WAIT_MS)
    assert window._formatting


def test_a_click_outside_closes_and_returns_focus_to_the_options_link(
    qtbot,
) -> None:
    window = _show(qtbot)
    popover = _open(qtbot, window)
    qtbot.mouseClick(popover, Qt.MouseButton.LeftButton, pos=QPoint(-20, -20))
    qtbot.waitUntil(lambda: not popover.isVisible(), timeout=_WAIT_MS)
    assert not _link(window).isChecked()
    qtbot.waitUntil(lambda: _focused() is _link(window), timeout=_WAIT_MS)


def test_a_press_on_the_options_link_while_open_only_closes_it(qtbot) -> None:
    window = _show(qtbot)
    link = _link(window)
    popover = _open(qtbot, window)
    at_link = popover.mapFromGlobal(link.mapToGlobal(link.rect().center()))
    qtbot.mousePress(popover, Qt.MouseButton.LeftButton, pos=at_link)
    qtbot.waitUntil(lambda: not popover.isVisible(), timeout=_WAIT_MS)
    # The press is not replayed on the link, which would open it again.
    assert popover.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
    assert not link.isChecked()


def test_the_link_triggers_output_options_and_toggles_the_popover(qtbot) -> None:
    window = _show(qtbot)
    action = window._actions.output_options
    assert action.text() == "Output Options"
    assert action not in window._actions.stubs()
    popover = _popover(window)
    action.trigger()
    assert popover.isVisible()
    action.trigger()
    assert not popover.isVisible()
    assert not _link(window).isChecked()


# --- Rows -------------------------------------------------------------------------


def test_only_indent_and_sort_keys_rows_with_the_specs_labels(qtbot) -> None:
    window = _show(qtbot)
    popover = _open(qtbot, window)
    texts = {label.text() for label in popover.findChildren(QLabel)}
    assert {"Indent", "Sort keys", "Applies on the next Format"} <= texts
    assert [_segment(window, text).text() for text, _ in _SEGMENTS] == [
        "2",
        "4",
        "Tab",
    ]
    assert _sort_box(window).text() == "A–Z at every level"
    assert _reset(window).text() == "Reset to defaults"
    every = " ".join(
        [
            *texts,
            *(button.text() for button in popover.findChildren(QToolButton)),
            *(button.text() for button in popover.findChildren(QPushButton)),
            *(box.text() for box in popover.findChildren(QCheckBox)),
        ]
    )
    for left_out in (
        "Non-ASCII",
        "Escape as",
        "Line endings",
        "CRLF",
        "Quote style",
        "Collections",
        "Comments may move.",
    ):
        assert left_out not in every
    assert len(popover.findChildren(QCheckBox)) == 1
    assert len(popover.findChildren(QToolButton)) == len(_SEGMENTS)


def test_every_control_has_an_accessible_name(qtbot) -> None:
    window = _show(qtbot)
    popover = _open(qtbot, window)
    for text, name in _SEGMENTS:
        assert _segment(window, text).accessibleName() == name
    indent = window.findChild(QWidget, "outputOptionsIndent")
    assert isinstance(indent, QWidget)
    assert indent.accessibleName() == "Indent"
    assert _sort_box(window).accessibleName() == "Sort keys"
    assert _sort_box(window).accessibleDescription() == "A–Z at every level"
    assert _reset(window).accessibleName() == "Reset to defaults"
    assert popover.accessibleName() == "Output options"


# --- One source of truth ----------------------------------------------------------


def test_indent_shows_the_toolbar_pickers_choice(qtbot) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    assert _checked_segments(window) == ["2"]
    _choose(_picker(window), "Tab")
    assert _checked_segments(window) == ["Tab"]
    submenu = window.findChild(QMenu, "indentSizeMenu")
    assert isinstance(submenu, QMenu)
    four = next(item for item in submenu.actions() if item.text() == "4 spaces")
    four.trigger()
    assert _checked_segments(window) == ["4"]


@pytest.mark.parametrize(("text", "choice"), [("4", "4 spaces"), ("Tab", "Tab")])
def test_choosing_indent_in_the_popover_moves_the_toolbar_picker(
    qtbot, text, choice
) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    qtbot.mouseClick(_segment(window, text), Qt.MouseButton.LeftButton)
    assert _picker(window).currentText() == choice
    assert load_indent_size() == choice
    assert problems_settings().value("format/indentSize") == choice
    assert _checked_segments(window) == [text]


def test_the_saved_indent_shows_in_the_popover_on_the_next_launch(qtbot) -> None:
    first = _show(qtbot)
    _open(qtbot, first)
    qtbot.mouseClick(_segment(first, "4"), Qt.MouseButton.LeftButton)
    first.close()
    second = _show(qtbot)
    _open(qtbot, second)
    assert _checked_segments(second) == ["4"]
    assert _picker(second).currentText() == "4 spaces"


def test_sort_keys_in_the_popover_is_the_toolbar_toggle(qtbot) -> None:
    window = _show(qtbot)
    action: QAction = window._actions.sort_keys
    button = _sort_button(qtbot, window)
    _open(qtbot, window)
    box = _sort_box(window)
    assert not box.isChecked()
    box.click()
    assert action.isChecked()
    assert button.isChecked()
    assert load_sort_keys() is True
    action.trigger()
    assert not action.isChecked()
    assert not box.isChecked()
    assert load_sort_keys() is False


def test_the_saved_sort_keys_shows_in_the_popover_on_the_next_launch(qtbot) -> None:
    first = _show(qtbot)
    first._actions.sort_keys.trigger()
    first.close()
    second = _show(qtbot)
    _open(qtbot, second)
    assert _sort_box(second).isChecked()


# --- Where a choice doesn't apply -------------------------------------------------


def test_tab_is_greyed_out_while_output_is_yaml(qtbot) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    tab = _segment(window, "Tab")
    assert tab.isEnabled()
    _choose(_format_picker(window, "output"), "YAML")
    assert not tab.isEnabled()
    assert tab.toolTip() == _YAML_TAB_REASON
    assert tab.accessibleDescription() == _YAML_TAB_REASON
    assert _segment(window, "2").isEnabled()
    assert _segment(window, "4").isEnabled()
    _choose(_format_picker(window, "output"), "JSON")
    assert tab.isEnabled()
    assert tab.accessibleDescription() == ""


def test_a_chosen_tab_keeps_showing_while_output_is_yaml(qtbot) -> None:
    window = _show(qtbot)
    _choose(_picker(window), "Tab")
    _choose(_format_picker(window, "output"), "YAML")
    _open(qtbot, window)
    tab = _segment(window, "Tab")
    assert tab.isChecked()
    assert not tab.isEnabled()
    assert load_indent_size() == "Tab"


def test_hovering_the_greyed_out_tab_shows_why(qtbot) -> None:
    window = _show(qtbot)
    _choose(_format_picker(window, "output"), "YAML")
    _open(qtbot, window)
    tab = _segment(window, "Tab")
    host = tab.parentWidget()
    assert isinstance(host, QWidget)
    pos = tab.geometry().center()
    QApplication.sendEvent(
        host, QHelpEvent(QEvent.Type.ToolTip, pos, host.mapToGlobal(pos))
    )
    qtbot.waitUntil(QToolTip.isVisible, timeout=_WAIT_MS)
    assert QToolTip.text() == _YAML_TAB_REASON
    QToolTip.hideText()


@pytest.mark.parametrize("label", ["XML", "HTML"])
def test_sort_keys_is_greyed_out_for_xml_and_html_with_the_toolbars_reason(
    qtbot, label
) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    _choose(_format_picker(window, "input"), label)
    box = _sort_box(window)
    reason = f"Sort keys doesn't apply to {label}"
    assert not window._actions.sort_keys.isEnabled()
    assert not box.isEnabled()
    assert box.toolTip() == reason
    assert box.accessibleDescription() == reason
    _choose(_format_picker(window, "input"), "JSON")
    assert box.isEnabled()
    assert box.accessibleDescription() == "A–Z at every level"


# --- Reset and the note -----------------------------------------------------------


def test_reset_to_defaults_sets_two_spaces_and_sort_keys_off(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    output.setPlainText("kept as it is")
    _choose(_picker(window), "Tab")
    window._actions.sort_keys.trigger()
    _open(qtbot, window)
    qtbot.mouseClick(_reset(window), Qt.MouseButton.LeftButton)
    assert _picker(window).currentText() == "2 spaces"
    assert not window._actions.sort_keys.isChecked()
    assert _checked_segments(window) == ["2"]
    assert not _sort_box(window).isChecked()
    assert load_indent_size() == "2 spaces"
    assert load_sort_keys() is False
    # The note is static: nothing reformats Output.
    assert _label(window, "outputOptionsNote").text() == "Applies on the next Format"
    assert output.toPlainText() == "kept as it is"
    assert not window._formatting


def test_changing_an_option_does_not_reformat_output(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    window._actions.format.trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=15_000)
    before = output.toPlainText()
    _open(qtbot, window)
    qtbot.mouseClick(_segment(window, "4"), Qt.MouseButton.LeftButton)
    _sort_box(window).click()
    assert output.toPlainText() == before
    assert not window._formatting


# --- Keyboard ---------------------------------------------------------------------


def test_focus_starts_on_the_chosen_indent(qtbot) -> None:
    window = _show(qtbot)
    _choose(_picker(window), "4 spaces")
    _open(qtbot, window)
    qtbot.waitUntil(lambda: _focused() is _segment(window, "4"), timeout=_WAIT_MS)


def test_arrow_keys_move_within_indent_and_choose(qtbot) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    qtbot.waitUntil(lambda: _focused() is _segment(window, "2"), timeout=_WAIT_MS)
    _press(qtbot, Qt.Key.Key_Right)
    assert _focused() is _segment(window, "4")
    assert _picker(window).currentText() == "4 spaces"
    _press(qtbot, Qt.Key.Key_Right)
    assert _focused() is _segment(window, "Tab")
    assert _picker(window).currentText() == "Tab"
    _press(qtbot, Qt.Key.Key_Right)
    assert _focused() is _segment(window, "Tab")
    _press(qtbot, Qt.Key.Key_Left)
    _press(qtbot, Qt.Key.Key_Up)
    assert _focused() is _segment(window, "2")
    assert _picker(window).currentText() == "2 spaces"
    _press(qtbot, Qt.Key.Key_Down)
    assert _picker(window).currentText() == "4 spaces"


def test_arrow_keys_skip_the_greyed_out_tab(qtbot) -> None:
    window = _show(qtbot)
    _choose(_format_picker(window, "output"), "YAML")
    _open(qtbot, window)
    qtbot.waitUntil(lambda: _focused() is _segment(window, "2"), timeout=_WAIT_MS)
    _press(qtbot, Qt.Key.Key_Right)
    _press(qtbot, Qt.Key.Key_Right)
    assert _focused() is _segment(window, "4")
    assert _picker(window).currentText() == "4 spaces"


def test_tab_moves_through_the_controls_and_space_toggles(qtbot) -> None:
    window = _show(qtbot)
    popover = _open(qtbot, window)
    qtbot.waitUntil(lambda: _focused() is _segment(window, "2"), timeout=_WAIT_MS)
    _press(qtbot, Qt.Key.Key_Tab)
    assert _focused() is _sort_box(window)
    _press(qtbot, Qt.Key.Key_Space)
    assert _sort_box(window).isChecked()
    assert window._actions.sort_keys.isChecked()
    _press(qtbot, Qt.Key.Key_Tab)
    assert _focused() is _reset(window)
    _press(qtbot, Qt.Key.Key_Tab)
    assert _focused() is _segment(window, "2")
    _press(qtbot, Qt.Key.Key_Backtab)
    assert _focused() is _reset(window)
    assert popover.isVisible()


def test_the_unchosen_segments_are_not_tab_stops(qtbot) -> None:
    window = _show(qtbot)
    _open(qtbot, window)
    policy = Qt.FocusPolicy.TabFocus
    assert _segment(window, "2").focusPolicy() & policy
    assert not _segment(window, "4").focusPolicy() & policy
    assert not _segment(window, "Tab").focusPolicy() & policy


# --- Colors -----------------------------------------------------------------------


def _surface(palette: QPalette) -> QColor:
    return palette.color(QPalette.ColorRole.Window)


def _on_window(palette: QPalette, role: QPalette.ColorRole) -> float:
    window = _surface(palette)
    return contrast_ratio(composite(palette.color(role), window), window)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_reads_at_4_5_to_1_and_edges_at_3_to_1(
    qtbot, install_scheme, scheme
) -> None:
    window = _show(qtbot)
    # After the window: creating it applies the saved appearance.
    palette = install_scheme(scheme)
    popover = _open(qtbot, window)
    assert popover.palette().color(QPalette.ColorRole.Window) == _surface(palette)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.PlaceholderText,
        QPalette.ColorRole.Link,
    ):
        assert _on_window(palette, role) >= 4.5, role
    colors = segment_colors(palette)
    surface = _surface(palette)
    assert contrast_ratio(colors.ink, colors.fill) >= 4.5
    assert contrast_ratio(colors.chosen_ink, colors.chosen_fill) >= 4.5
    assert contrast_ratio(colors.edge, colors.fill) >= 3.0
    assert contrast_ratio(colors.edge, surface) >= 3.0
    assert contrast_ratio(colors.focus, surface) >= 3.0
    assert contrast_ratio(colors.focus, colors.chosen_fill) >= 3.0
    # Greyed-out labels: 3:1, per Bill's rule for disabled text.
    assert contrast_ratio(colors.unavailable_ink, colors.fill) >= 3.0
    assert contrast_ratio(colors.edge, colors.pressed_fill) >= 3.0
    link_ink = composite(palette.color(QPalette.ColorRole.Link), surface)
    assert contrast_ratio(link_ink, colors.pressed_fill) >= 4.5
    # The stylesheets carry this scheme's colors, not the last scheme's.
    assert _segment(window, "2").parentWidget().styleSheet() == segment_style()
    assert _link(window).styleSheet() == options_link_style()
    for color in (colors.chosen_fill, colors.edge, colors.unavailable_ink):
        assert color.name() in segment_style()


def test_a_scheme_switch_restyles_the_popover(qtbot, install_scheme) -> None:
    window = _show(qtbot)
    install_scheme("light")
    _open(qtbot, window)
    light = _segment(window, "2").parentWidget().styleSheet()
    palette = install_scheme("dark")
    dark = _segment(window, "2").parentWidget().styleSheet()
    assert dark != light
    assert segment_colors(palette).chosen_fill.name() in dark


def test_the_pressed_link_shows_an_edge_as_well_as_a_fill(qtbot) -> None:
    """Color is never the only cue: the pressed link gains a border."""
    window = _show(qtbot)
    sheet = options_link_style()
    assert ":checked" in sheet
    checked = sheet[sheet.index(":checked") :]
    assert "border" in checked
    assert "background" in checked
    assert _link(window).styleSheet() == sheet


# --- Release check ----------------------------------------------------------------


def test_output_options_is_off_the_not_yet_list(qtbot) -> None:
    window = _show(qtbot)
    actions = window._actions
    assert actions.output_options not in actions._not_yet_actions()
    assert actions.output_options not in actions.stubs()
    message = not_yet_failure()
    assert message is not None
    assert "Output Options" not in message
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert main() == 1
    assert printed.getvalue() == (
        'Release blocked: these actions still show "Coming soon": '
        "Open Right Document…, Compare, Next Change, Previous Change, "
        "Link Scrolling.\n"
        "Build them or remove them from UNBUILT_ACTIONS in actions.py "
        "before tagging.\n"
        'Release blocked: these actions still show "Not yet": '
        "Find and Replace….\n"
        "Build them or remove them from _not_yet_actions() in actions.py "
        "before tagging.\n"
    )
