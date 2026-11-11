"""The direction label shows the result on screen, not the pickers (#91 D4).

While Output is empty it follows the pickers. Once a Format or Minify
writes Output, it names that result's direction until Output changes
result or empties. A picker change after a result leaves it alone; the
Stale cue for that is D2. Convert from Python names Python as its
source. Escape as String and Unescape String name the formats they read
and wrote (Bill, #91 and #207).
"""

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel

from galley.app.main import create_main_window

# Fake data only.
_JSON = '{"name": "Ada Example", "id": 7}'
_BROKEN = '{"name": "Ada Example",'
_PYTHON = "{'name': 'Ada Example', 'id': 7}"


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _direction(window) -> str:
    label = window.findChild(QLabel, "directionLabel")
    assert label is not None
    return label.text()


def _picker(window, name: str) -> QComboBox:
    combo = window.findChild(QComboBox, name)
    assert isinstance(combo, QComboBox), name
    return combo


def _run(qtbot, window, command: str) -> None:
    _action(window, command).trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=15000)


def _format_to_yaml(qtbot, window) -> None:
    window._input_pane.editor.setPlainText(_JSON)
    _picker(window, "outputFormat").setCurrentText("YAML")
    _run(qtbot, window, "Format")
    assert window._output_pane.own_editor.toPlainText() != ""
    assert _direction(window) == "JSON → YAML"


def test_a_picker_change_after_a_result_keeps_the_result_direction(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    # Output still holds YAML.
    assert _direction(window) == "JSON → YAML"


def test_an_empty_output_follows_the_pickers_again(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    window._output_pane.own_editor.clear()
    assert _direction(window) == "JSON → JSON"
    _picker(window, "outputFormat").setCurrentText("YAML")
    assert _direction(window) == "JSON → YAML"


def test_a_new_format_names_its_own_direction(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    _run(qtbot, window, "Format")
    assert window._output_pane.own_editor.toPlainText().startswith("{")
    assert _direction(window) == "JSON → JSON"


def test_minify_names_json_as_its_result(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _run(qtbot, window, "Minify")
    assert window._output_pane.own_editor.toPlainText().startswith("{")
    assert _direction(window) == "JSON → JSON"


def test_a_failed_format_keeps_the_result_on_screen(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    yaml_output = window._output_pane.own_editor.toPlainText()
    window._input_pane.editor.setPlainText(_BROKEN)
    _picker(window, "outputFormat").setCurrentText("JSON")
    _run(qtbot, window, "Format")
    assert window._output_pane.own_editor.toPlainText() == yaml_output
    assert _direction(window) == "JSON → YAML"


def test_validate_leaves_output_and_its_direction(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    _run(qtbot, window, "Validate")
    assert _direction(window) == "JSON → YAML"


def _label(window) -> QLabel:
    label = window.findChild(QLabel, "directionLabel")
    assert label is not None
    return label


def _assert_label(window, text: str) -> None:
    """The label and its accessible name read the same text (Bill, #91)."""
    label = _label(window)
    assert label.text() == text
    assert label.accessibleName() == text


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("target", ["JSON", "YAML"])
def test_convert_from_python_names_python_as_its_source(
    qtbot, install_scheme, scheme: str, target: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText(target)
    window._input_pane.editor.setPlainText(_PYTHON)
    _action(window, "Convert from Python").trigger()
    assert window._output_pane.own_editor.toPlainText() != ""
    _assert_label(window, f"Python → {target}")
    window._output_pane.own_editor.clear()
    _assert_label(window, f"JSON → {target}")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_escape_as_string_names_the_input_format(
    qtbot, install_scheme, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _picker(window, "inputFormat").setCurrentText("YAML")
    _picker(window, "outputFormat").setCurrentText("YAML")
    window._input_pane.editor.setPlainText("name: Ada Example\nid: 7\n")
    _action(window, "Escape as String").trigger()
    assert window._output_pane.own_editor.toPlainText().startswith('"')
    # Escape parses Input as YAML and writes one JSON string (Bill, #207).
    _assert_label(window, "YAML → JSON")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_unescape_string_names_the_output_format(
    qtbot, install_scheme, scheme: str
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    _picker(window, "outputFormat").setCurrentText("YAML")
    window._input_pane.editor.setPlainText('"{\\"name\\":\\"Ada Example\\"}"\n')
    _action(window, "Unescape String").trigger()
    assert window._output_pane.own_editor.toPlainText() == "name: Ada Example\n"
    _assert_label(window, "JSON → YAML")


def test_a_failed_convert_or_unescape_keeps_the_label(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    window._input_pane.editor.setPlainText("{'name': 'Ada Example'")
    _action(window, "Convert from Python").trigger()
    _assert_label(window, "JSON → YAML")
    window._input_pane.editor.setPlainText('{"not": "a string"}')
    _action(window, "Unescape String").trigger()
    _assert_label(window, "JSON → YAML")


def test_a_format_direction_reads_the_same_to_screen_readers(qtbot) -> None:
    window = _show(qtbot)
    _assert_label(window, "JSON → JSON")
    _format_to_yaml(qtbot, window)
    _assert_label(window, "JSON → YAML")
