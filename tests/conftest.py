"""Pytest defaults for Galley. Tests always run without a display."""

import os
from pathlib import Path

import pytest
from tests.perf import Budget, budget_for, current_mode

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# DejaVu Sans Mono 2.37, regular, from the official release (#145). Its
# license is LICENSE-DejaVu.txt in the same folder.
CODE_TEST_FONT = (
    Path(__file__).resolve().parent / "fixtures" / "fonts" / "DejaVuSansMono.ttf"
)
CODE_TEST_FAMILY = "DejaVu Sans Mono"


@pytest.fixture(scope="session")
def code_test_font(qapp) -> str:
    """Load DejaVu Sans Mono and make it the editors' code font (#145).

    Qt's offscreen platform on the Windows runners has no fonts and draws
    every glyph as the same box, so pixel tests broke there only. Loading a
    real font gives every platform the same glyphs. ``code_font`` asks for
    the generic ``monospace`` family, which Qt maps to this font here. App
    code is unchanged.
    """
    from PySide6.QtGui import QFont, QFontDatabase

    del qapp
    font_id = QFontDatabase.addApplicationFont(str(CODE_TEST_FONT))
    if font_id == -1:
        pytest.fail(f"Qt could not load the test font {CODE_TEST_FONT}", pytrace=False)
    families = QFontDatabase.applicationFontFamilies(font_id)
    if CODE_TEST_FAMILY not in families:
        pytest.fail(
            f"the test font reports {families}, not {CODE_TEST_FAMILY!r}",
            pytrace=False,
        )
    QFont.insertSubstitution("monospace", CODE_TEST_FAMILY)
    return CODE_TEST_FAMILY


@pytest.fixture(autouse=True)
def _qt_tests_use_the_code_test_font(request: pytest.FixtureRequest) -> None:
    """Load the test font before a Qt test makes its first widget.

    Only tests that use ``qapp`` or ``qtbot`` start Qt, so core tests stay
    without a QApplication.
    """
    if {"qapp", "qtbot"} & set(request.fixturenames):
        request.getfixturevalue("code_test_font")


@pytest.fixture
def perf_budget(request: pytest.FixtureRequest) -> Budget:
    """Time and check a wall-clock budget. Only for tests marked ``perf``."""
    return budget_for(request.node)


def pytest_configure(config: pytest.Config) -> None:
    """Stop at once on a GALLEY_PERF_MODE that is neither gate nor strict."""
    del config
    current_mode()
