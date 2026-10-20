"""View › Appearance: System, Light, Dark (issue #54).

Offscreen CI ignores the OS dark setting, and ``setColorScheme`` does not
change the offscreen palette. Each test picks the mode itself, through the
menu action, and checks that the palette really changed before it
measures anything.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
)

from galley.app.appearance import (
    MODE_DARK,
    MODE_LIGHT,
    MODE_SYSTEM,
    application_appearance,
    load_appearance_mode,
)
from galley.app.editor import CodeEditor
from galley.app.format_picker import closed_field_colors, pop_out_colors
from galley.app.main import create_main_window
from galley.app.problems_settings import problems_settings
from galley.app.theme import (
    ACCENT,
    ACCENT_TEXT_DARK,
    EDGE_MIN_CONTRAST,
    ERROR_DARK,
    ERROR_TINT,
    LINE_NUMBER_DARK,
    TEXT_MIN_CONTRAST,
    contrast_ratio,
    dark_palette,
    error_color,
    line_number_color,
    problem_line_tint,
)

_R = QPalette.ColorRole


@pytest.fixture(autouse=True)
def back_to_system(qapp):
    """Leave the next test on System, whatever this one picked."""
    yield
    appearance = application_appearance(qapp)
    if appearance is not None:
        appearance.set_mode(MODE_SYSTEM, save=False)
        QApplication.processEvents()


def _window(qtbot, *, show: bool = True):
    window = create_main_window()
    qtbot.addWidget(window)
    if show:
        window.show()
        qtbot.waitExposed(window)
    return window


def _menu(window, name: str) -> QMenu:
    menu = window.findChild(QMenu, name)
    assert isinstance(menu, QMenu), name
    return menu


def _appearance_actions(window) -> dict[str, QAction]:
    menu = _menu(window, "menuAppearance")
    return {action.text(): action for action in menu.actions()}


def _pick(qtbot, window, label: str) -> None:
    """Trigger View › Appearance › ``label`` and let deferred refreshes run."""
    _appearance_actions(window)[label].trigger()
    qtbot.wait(20)


def _palette() -> QPalette:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    return app.palette()


def _is_dark(color: QColor) -> bool:
    return color.lightness() < 128


def _painted(color: QColor, surface: QColor) -> QColor:
    """Composite an alpha role on its surface, as the screen shows it."""
    share = color.alphaF()
    return QColor(
        round(surface.red() * (1 - share) + color.red() * share),
        round(surface.green() * (1 - share) + color.green() * share),
        round(surface.blue() * (1 - share) + color.blue() * share),
    )


def _ratio(ink: QColor, surface: QColor) -> float:
    return contrast_ratio(_painted(ink, surface), surface)


def _editor(window, name: str = "inputEditor") -> CodeEditor:
    editor = window.findChild(CodeEditor, name)
    assert isinstance(editor, CodeEditor)
    return editor


def _pickers(window) -> list[QComboBox]:
    found = []
    for name in ("inputFormat", "outputFormat"):
        picker = window.findChild(QComboBox, name)
        assert isinstance(picker, QComboBox), name
        found.append(picker)
    return found


def _sampled_contrast(widget, *, width_fraction: float) -> float:
    """Strongest rendered pixel against the most common one."""
    image = widget.grab().toImage()
    assert image.width() > 8 and image.height() > 8
    counts: dict[int, int] = {}
    limit_x = max(4, int(image.width() * width_fraction))
    for y in range(2, image.height() - 2):
        for x in range(2, limit_x):
            pixel = image.pixel(x, y)
            counts[pixel] = counts.get(pixel, 0) + 1
    background = QColor(max(counts, key=counts.__getitem__))
    return max(contrast_ratio(QColor(pixel), background) for pixel in counts)


def test_appearance_menu_has_three_radio_items_and_defaults_to_system(
    qtbot,
) -> None:
    window = _window(qtbot)
    actions = _appearance_actions(window)
    assert list(actions) == ["System", "Light", "Dark"]
    groups = {action.actionGroup() for action in actions.values()}
    assert len(groups) == 1
    group = groups.pop()
    assert isinstance(group, QActionGroup)
    assert group.isExclusive()
    for action in actions.values():
        assert action.isCheckable()
        assert action.isEnabled()
        assert action.shortcut().isEmpty()
    assert actions["System"].isChecked()
    assert load_appearance_mode() == MODE_SYSTEM


def test_picking_dark_switches_now_and_saves_the_choice(qtbot) -> None:
    window = _window(qtbot)
    editor = _editor(window)
    light_base = QColor(editor.palette().color(_R.Base))
    assert not _is_dark(light_base)

    _pick(qtbot, window, "Dark")

    assert problems_settings().value("appearance/mode") == MODE_DARK
    assert _appearance_actions(window)["Dark"].isChecked()
    dark_base = editor.palette().color(_R.Base)
    assert dark_base.rgba() != light_base.rgba()
    assert _is_dark(dark_base)
    assert dark_base.rgba() == _palette().color(_R.Base).rgba()

    _pick(qtbot, window, "Light")
    assert problems_settings().value("appearance/mode") == MODE_LIGHT
    assert editor.palette().color(_R.Base).rgba() == light_base.rgba()


def test_light_to_dark_while_open_recolors_the_editor_gutter_and_tray(
    qtbot,
) -> None:
    window = _window(qtbot)
    editor = _editor(window)
    editor.setPlainText('{\n  "a": 1,\n  "b": tru\n}\n')
    editor.set_problem_lines({3})
    tray = window.findChild(QFrame, "undoRedoTray")
    assert isinstance(tray, QFrame)
    _pick(qtbot, window, "Light")
    light_gutter = QColor(editor.gutter_color())
    light_numbers = QColor(editor.line_number_color())

    _pick(qtbot, window, "Dark")

    base = editor.palette().color(_R.Base)
    assert _is_dark(base), "the editor kept the light Base after the switch"
    gutter = editor.gutter_color()
    assert gutter.rgba() != light_gutter.rgba()
    assert _is_dark(gutter), "the gutter stayed light on a dark editor"
    numbers = editor.line_number_color()
    assert numbers.rgba() != light_numbers.rgba()
    assert numbers.name() == QColor(LINE_NUMBER_DARK).name()
    assert _ratio(numbers, base) >= TEXT_MIN_CONTRAST
    assert _ratio(numbers, gutter) >= TEXT_MIN_CONTRAST
    assert _is_dark(tray.palette().color(_R.AlternateBase))
    tint = problem_line_tint(editor.palette())
    assert tint.name() != QColor(ERROR_TINT).name()
    assert _is_dark(tint), "the problem line kept the light pink band"
    bands = [
        selection.format.background().color().name()
        for selection in editor.extraSelections()
    ]
    assert tint.name() in bands
    assert QColor(ERROR_TINT).name() not in bands
    assert error_color(editor.palette()).name() == QColor(ERROR_DARK).name()

    _pick(qtbot, window, "Light")
    assert editor.gutter_color().rgba() == light_gutter.rgba()
    bands = [
        selection.format.background().color().name()
        for selection in editor.extraSelections()
    ]
    assert QColor(ERROR_TINT).name() in bands


def test_copied_inks_follow_a_switch(qtbot) -> None:
    """Pickers, the split rail, and toolbar glyphs copy palette colors."""
    window = _window(qtbot)
    pickers = _pickers(window)
    ratio = window.findChild(QLabel, "splitRatio")
    assert isinstance(ratio, QLabel)
    paste = next(
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is not None
        and button.defaultAction().text() == "Paste"
    )
    _pick(qtbot, window, "Light")
    light_glyph = paste.icon().pixmap(16, 16).toImage()

    _pick(qtbot, window, "Dark")

    window_ink = window.palette().color(_R.ButtonText)
    for picker in pickers:
        assert picker.palette().color(_R.ButtonText).name() == window_ink.name()
        ink, field = closed_field_colors(picker)
        assert _is_dark(field)
        assert not _is_dark(ink)
    rail_ink = ratio.palette().color(_R.WindowText)
    assert rail_ink.name() == _palette().color(_R.PlaceholderText).name()
    assert paste.icon().pixmap(16, 16).toImage() != light_glyph


def test_choice_persists_across_a_restart(qtbot) -> None:
    window = _window(qtbot)
    _pick(qtbot, window, "Dark")
    window.close()
    appearance = application_appearance()
    assert appearance is not None
    # A new process starts on System until the saved mode is restored.
    appearance.set_mode(MODE_SYSTEM, save=False)
    assert not _is_dark(_palette().color(_R.Window))
    assert load_appearance_mode() == MODE_DARK

    restarted = _window(qtbot, show=False)

    # Restored before the first show, so the window never flashes light.
    assert not restarted.isVisible()
    assert appearance.mode() == MODE_DARK
    assert _appearance_actions(restarted)["Dark"].isChecked()
    assert _is_dark(_palette().color(_R.Window))
    assert _is_dark(_editor(restarted).palette().color(_R.Base))


def test_an_unknown_saved_mode_falls_back_to_system(qtbot) -> None:
    settings = problems_settings()
    settings.setValue("appearance/mode", "sepia")
    settings.sync()
    window = _window(qtbot)
    assert load_appearance_mode() == MODE_SYSTEM
    assert _appearance_actions(window)["System"].isChecked()


def test_system_follows_a_scheme_change(qtbot) -> None:
    window = _window(qtbot)
    assert _appearance_actions(window)["System"].isChecked()
    hints = QGuiApplication.styleHints()
    editor = _editor(window)
    assert not _is_dark(editor.palette().color(_R.Base))

    hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    qtbot.waitUntil(lambda: _is_dark(editor.palette().color(_R.Base)))
    assert _is_dark(editor.gutter_color())

    hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
    qtbot.waitUntil(lambda: not _is_dark(editor.palette().color(_R.Base)))


def test_light_and_dark_ignore_a_scheme_change(qtbot) -> None:
    window = _window(qtbot)
    hints = QGuiApplication.styleHints()
    _pick(qtbot, window, "Light")
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    qtbot.wait(20)
    assert not _is_dark(_palette().color(_R.Base))
    _pick(qtbot, window, "Dark")
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
    qtbot.wait(20)
    assert _is_dark(_palette().color(_R.Base))


def _assert_contrast(window) -> None:
    palette = _palette()
    window_bg = palette.color(_R.Window)
    base = palette.color(_R.Base)
    gutter = palette.color(_R.AlternateBase)
    text_pairs = {
        "text on base": (palette.color(_R.Text), base),
        "window text": (palette.color(_R.WindowText), window_bg),
        "button text": (palette.color(_R.ButtonText), palette.color(_R.Button)),
        "muted on window": (palette.color(_R.PlaceholderText), window_bg),
        "muted on base": (palette.color(_R.PlaceholderText), base),
        "line numbers on gutter": (line_number_color(palette), gutter),
        "line numbers on base": (line_number_color(palette), base),
        "link on window": (palette.color(_R.Link), window_bg),
        "menu selection": (base, palette.color(_R.Link)),
        "Format label": (palette.color(_R.HighlightedText), QColor(ACCENT)),
        "error number on gutter": (error_color(palette), gutter),
        "text on problem line": (palette.color(_R.Text), problem_line_tint(palette)),
        "text on current line": (palette.color(_R.Text), palette.color(_R.Midlight)),
    }
    if _is_dark(window_bg):
        # Light mode keeps a white HighlightedText (the Format label) over
        # the #99E0FF selection, which #57 shipped. That pair is a known
        # light-mode follow-up, so only dark mode checks it here.
        text_pairs["selected text"] = (
            palette.color(_R.HighlightedText),
            palette.color(_R.Highlight),
        )
    for name, (ink, surface) in text_pairs.items():
        ratio = _ratio(ink, surface)
        assert ratio >= TEXT_MIN_CONTRAST, f"{name}: {ratio:.2f}"
    edge_pairs = {
        "pane edge on window": (palette.color(_R.Mid), window_bg),
        "pane edge on base": (palette.color(_R.Mid), base),
        "focus ring on window": (palette.color(_R.Accent), window_bg),
    }
    for name, (edge, surface) in edge_pairs.items():
        ratio = _ratio(edge, surface)
        assert ratio >= EDGE_MIN_CONTRAST, f"{name}: {ratio:.2f}"
    for picker in _pickers(window):
        ink, field = closed_field_colors(picker)
        assert _ratio(ink, field) >= TEXT_MIN_CONTRAST, picker.objectName()
        assert _sampled_contrast(picker, width_fraction=0.55) >= TEXT_MIN_CONTRAST
    pop_outs = [
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "Pop out"
    ]
    assert len(pop_outs) == 2
    for button in pop_outs:
        ink, surface = pop_out_colors(button)
        assert _ratio(ink, surface) >= TEXT_MIN_CONTRAST


def test_contrast_passes_in_light_and_dark(qtbot) -> None:
    window = _window(qtbot)

    _pick(qtbot, window, "Light")
    light_window = QColor(_palette().color(_R.Window))
    assert not _is_dark(light_window)
    _assert_contrast(window)

    _pick(qtbot, window, "Dark")
    dark_window = _palette().color(_R.Window)
    assert dark_window.rgba() != light_window.rgba(), "the palette did not change"
    assert _is_dark(dark_window)
    assert _palette().color(_R.Link).name() == QColor(ACCENT_TEXT_DARK).name()
    _assert_contrast(window)


def test_format_fill_stays_brand_blue_in_dark(qtbot) -> None:
    window = _window(qtbot)
    _pick(qtbot, window, "Dark")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(button, QPushButton)
    assert ACCENT in button.styleSheet()
    assert ACCENT_TEXT_DARK not in button.styleSheet()


def test_stylesheet_palette_colors_follow_a_switch(qtbot) -> None:
    """Stylesheets resolve palette(...) when set, so a switch re-applies them."""
    window = _window(qtbot)
    status = window.statusBar()
    _pick(qtbot, window, "Light")
    light = status.grab().toImage()
    light_fill = QColor(light.pixel(light.width() // 2, 2))
    assert not _is_dark(light_fill)

    _pick(qtbot, window, "Dark")

    dark = status.grab().toImage()
    dark_fill = QColor(dark.pixel(dark.width() // 2, 2))
    assert _is_dark(dark_fill), "the status bar kept its light stylesheet fill"
    assert dark_fill.name() == _palette().color(_R.Window).name()


def _tool_button(window, text: str) -> QToolButton:
    for button in window.findChildren(QToolButton):
        action = button.defaultAction()
        if action is not None and action.text() == text:
            return button
    raise AssertionError(text)


def _label_ink(button: QToolButton) -> tuple[QColor, QColor]:
    """Strongest opaque label pixel and the button's most common pixel.

    The label sits right of the icon, so the left 40% is skipped.
    """
    image = button.grab().toImage()
    counts: dict[int, int] = {}
    for y in range(2, image.height() - 2):
        for x in range(int(image.width() * 0.4), image.width() - 3):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 255:
                continue
            counts[pixel.rgb()] = counts.get(pixel.rgb(), 0) + 1
    background = QColor(max(counts, key=counts.__getitem__))
    ink = max(
        (QColor(pixel) for pixel in counts),
        key=lambda color: contrast_ratio(color, background),
    )
    return ink, background


def test_disabled_controls_look_disabled_in_light_and_dark(qtbot) -> None:
    """Carries #69's faded-text fix: Coming soon must not look like Paste."""
    window = _window(qtbot)
    paste = _tool_button(window, "Paste")
    assert paste.isEnabled()
    coming_soon = [
        _tool_button(window, name)
        for name in ("Sort keys", "Minify", "Validate", "Find…")
    ]
    for button in coming_soon:
        assert not button.isEnabled()

    for label in ("Light", "Dark"):
        _pick(qtbot, window, label)
        palette = _palette()
        assert _is_dark(palette.color(_R.Window)) == (label == "Dark")
        surface = palette.color(_R.Window)
        enabled = palette.color(QPalette.ColorGroup.Active, _R.ButtonText)
        for role in (_R.ButtonText, _R.Text, _R.WindowText):
            faded = palette.color(QPalette.ColorGroup.Disabled, role)
            assert faded.alpha() == 255, f"{label} {role.name} is translucent"
            assert faded.rgba() != enabled.rgba()
            ratio = _ratio(faded, surface)
            assert ratio >= 3.0, f"{label} disabled {role.name}: {ratio:.2f}"
            assert _ratio(enabled, surface) - ratio >= 2.0, (
                f"{label} disabled {role.name} is not clearly dimmer"
            )
        inactive = palette.color(QPalette.ColorGroup.Inactive, _R.ButtonText)
        assert _ratio(inactive, surface) >= TEXT_MIN_CONTRAST

        paste_ink, paste_bg = _label_ink(paste)
        paste_ratio = contrast_ratio(paste_ink, paste_bg)
        assert paste_ratio >= TEXT_MIN_CONTRAST, f"{label} Paste {paste_ratio:.2f}"
        for button in coming_soon:
            ink, background = _label_ink(button)
            assert ink.name() != paste_ink.name(), button.defaultAction().text()
            ratio = contrast_ratio(ink, background)
            assert 2.5 <= ratio < paste_ratio - 1.5, (
                f"{label} {button.defaultAction().text()}: {ratio:.2f}"
            )


def test_dark_palette_fixes_black_inactive_button_text() -> None:
    """macOS reports Inactive ButtonText as opaque black even in dark mode."""
    platform = QPalette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        platform.setColor(group, _R.Window, QColor(50, 50, 50))
        platform.setColor(group, _R.Base, QColor(23, 23, 23))
        platform.setColor(group, _R.Text, QColor(255, 255, 255))
        platform.setColor(group, _R.WindowText, QColor(255, 255, 255, 216))
    platform.setColor(
        QPalette.ColorGroup.Active, _R.ButtonText, QColor(255, 255, 255, 216)
    )
    platform.setColor(QPalette.ColorGroup.Inactive, _R.ButtonText, QColor(0, 0, 0))

    fixed = dark_palette(platform)

    active = fixed.color(QPalette.ColorGroup.Active, _R.ButtonText)
    assert (
        fixed.color(QPalette.ColorGroup.Inactive, _R.ButtonText).rgba() == active.rgba()
    )
