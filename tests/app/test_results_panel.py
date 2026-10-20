"""Results panel tabs: hidden while Problems is the only tab."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from galley.app.problem_text import display_problem
from galley.app.problems_pane import ProblemsPane
from galley.app.results_panel import ResultRow, ResultsList
from galley.core.problems import Problem


class _DummyRow(ResultRow):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        row = QHBoxLayout(self)
        row.addWidget(QLabel(text))


def _changes() -> ResultsList:
    changes = ResultsList("Changes", "changesList")
    changes.set_rows([(5, _DummyRow("Changed line 5")), (9, _DummyRow("Added line 9"))])
    return changes


def _accent(palette: QPalette):
    return palette.color(QPalette.ColorRole.Accent)


def _header(pane: ProblemsPane) -> tuple[QLabel, QLabel]:
    title = pane.findChild(QLabel, "problemsTitle")
    badge = pane.findChild(QLabel, "problemsBadge")
    assert isinstance(title, QLabel) and isinstance(badge, QLabel)
    return title, badge


def test_a_second_tab_shows_the_tab_bar_until_it_is_removed(qtbot) -> None:
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.resize(320, 300)
    pane.set_problems((display_problem(Problem("yaml_tab", 3)),))
    pane.show()
    qtbot.waitExposed(pane)
    tabs = pane.tab_bar()
    title, badge = _header(pane)
    problems = pane.current_page()
    assert problems is not None
    assert not tabs.isVisible()
    assert title.isVisible() and title.text() == "Problems"
    assert badge.isVisible() and badge.text() == "1"

    changes = _changes()
    pane.add_tab(changes, "Changes", ("change", "changes"), _accent)
    pane.set_count(changes, 2)
    assert tabs.isVisible()
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Problems 1",
        "Changes 2",
    ]
    assert not title.isVisible() and not badge.isVisible()
    assert pane.current_page() is problems
    assert pane.list_widget().isVisible() and not changes.isVisible()

    qtbot.mouseClick(tabs, Qt.MouseButton.LeftButton, pos=tabs.tabRect(1).center())
    assert pane.current_page() is changes
    assert changes.isVisible() and not pane.list_widget().isVisible()
    assert badge.accessibleName() == "2 changes"
    changes.setFocus()
    qtbot.waitUntil(changes.hasFocus)
    with qtbot.waitSignal(changes.activated) as activated:
        qtbot.keyClick(changes, Qt.Key.Key_Return)
    assert activated.args == [5]

    pane.set_current_page(problems)
    assert pane.list_widget().isVisible() and not changes.isVisible()
    pane.set_current_page(changes)

    pane.remove_tab(changes)
    assert not tabs.isVisible()
    assert tabs.count() == 1
    assert pane.current_page() is problems
    assert pane.list_widget().isVisible()
    assert title.isVisible() and title.text() == "Problems"
    assert badge.isVisible() and badge.text() == "1"
    assert badge.accessibleName() == "1 problem"


def test_clicking_a_row_selects_it_and_sends_its_data(qtbot) -> None:
    changes = _changes()
    qtbot.addWidget(changes)
    changes.resize(320, 200)
    changes.show()
    qtbot.waitExposed(changes)
    second = changes.itemWidget(changes.item(1))
    assert isinstance(second, QWidget)
    with qtbot.waitSignal(changes.activated) as activated:
        qtbot.mouseClick(second, Qt.MouseButton.LeftButton)
    assert activated.args == [9]
    assert changes.currentItem() is changes.item(1)
