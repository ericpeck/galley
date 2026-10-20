"""Problems pane: one row per located error, docked to the left of Input."""

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.problem_text import DisplayProblem
from galley.app.results_panel import (
    ROW_MARGINS,
    ResultRow,
    ResultsList,
    ResultsPanel,
    set_style,
)
from galley.app.style import muted_text_style
from galley.app.theme import error_color

# Off until Replace tabs with spaces is approved. The control stays hidden.
SHOW_REPLACE_TABS_BUTTON = False

# The row marker. Color is never the only cue: the row also says "Error".
_ERROR_ICON = "⊗"  # ⊗


class ProblemsPane(ResultsPanel):
    """List of problems, with minimize, close, and a flagged tab repair."""

    jump_requested = Signal(int)
    replace_tabs_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("problems", parent)
        self.setAccessibleName("Problems")
        self._problems: tuple[DisplayProblem, ...] = ()

        self._list = ResultsList("Problems", "problemsList")
        self._list.activated.connect(self._jump)

        self._replace = QPushButton("Replace tabs with spaces")
        self._replace.setObjectName("replaceTabsButton")
        self._replace.setAccessibleName("Replace tabs with spaces")
        self._replace.setToolTip("Replace tabs with spaces")
        self._replace.clicked.connect(self.replace_tabs_requested.emit)
        self._replace.hide()
        self._replace.setEnabled(False)

        self._page = QWidget()
        self._page.setObjectName("problemsPage")
        page = QVBoxLayout(self._page)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        page.addWidget(self._list, 1)
        page.addWidget(self._replace)
        self.add_tab(self._page, "Problems", ("problem", "problems"), error_color)

    def list_widget(self) -> ResultsList:
        """The problem list. Tab order uses this widget."""
        return self._list

    def count(self) -> int:
        return len(self._problems)

    def messages(self) -> list[str]:
        return [problem.message for problem in self._problems]

    def lines(self) -> list[int]:
        return [problem.lineno for problem in self._problems]

    def set_problems(self, problems: Sequence[DisplayProblem]) -> None:
        """Replace the rows. An empty sequence is not shown by the window."""
        self._problems = tuple(problems)
        self._list.set_rows(
            [(problem.lineno, _ProblemRow(problem)) for problem in self._problems]
        )
        self.set_count(self._page, len(self._problems))
        self._sync_replace()

    def _sync_replace(self) -> None:
        has_tabs = any(problem.code == "yaml_tab" for problem in self._problems)
        show = SHOW_REPLACE_TABS_BUTTON and has_tabs
        self._replace.setEnabled(show)
        self._replace.setVisible(show)

    def _jump(self, lineno: object) -> None:
        if isinstance(lineno, int):
            self.jump_requested.emit(lineno)


class _ProblemRow(ResultRow):
    """An ⊗ marker, "Error · Line n" in bold, and the message under it."""

    def __init__(self, problem: DisplayProblem, parent: QWidget | None = None) -> None:
        super().__init__(problem.accessible, parent)
        self.setObjectName("problemRow")

        marker = QLabel(_ERROR_ICON)
        marker.setObjectName("problemMarker")
        marker.setAccessibleName("Error")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(18, 18)
        self._marker = marker

        line = QLabel(f"Error · Line {problem.lineno}")
        line.setStyleSheet("font-weight: 700;")
        self._line = line

        message = QLabel(problem.message)
        message.setWordWrap(True)
        message.setStyleSheet(muted_text_style())
        self._message = message

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(0)
        text_column.addWidget(line)
        text_column.addWidget(message)
        text_column.addStretch(1)

        row = QHBoxLayout(self)
        row.setContentsMargins(*ROW_MARGINS)
        row.setSpacing(8)
        row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text_column, 1)
        self.sync_text_colors()

    def sync_text_colors(self) -> None:
        # A selected row paints its text in an opaque color that reads on
        # the fill.
        set_style(
            self._marker,
            f"color: {error_color(self.palette()).name()}; "
            "font-size: 16px; font-weight: 700;",
        )
        if not self.is_selected():
            self._line.setStyleSheet("font-weight: 700;")
            self._message.setStyleSheet(muted_text_style())
            return
        line = self.readable_on_fill(QPalette.ColorRole.WindowText)
        message = self.readable_on_fill(QPalette.ColorRole.PlaceholderText)
        self._line.setStyleSheet(f"font-weight: 700; color: {line.name()};")
        self._message.setStyleSheet(f"color: {message.name()};")
