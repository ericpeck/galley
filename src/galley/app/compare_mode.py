"""Compare mode: View › Compare, its changes, opening and Format (#65 3d, 3e).

Compare reuses the Input/Output splitter. The pane headers read Left and
Right. Left is the Input editor itself, read-only, so only a file opened
into Left and Format change Input's text, each as one undo step. Right is a
read-only editor of its own, shown in the Output pane in place of the
Output editor, which stays as it was. Right keeps its text for the session.

Paste and Open Right Document… fill Right. File › Open…, Open Recent and a
file dropped outside the panes ask which side; a file dropped on a side
loads that side. Each file is read once here and refused when it is XML or
HTML; Left then takes the text through the window's own Open.

The compare runs on the parse session's worker (#87), one job at a time,
with its Cancel. A side over 2 MB asks first. Format sends one Format job
per side, Left first, holds both results and changes neither side unless
both succeed and neither side changed meanwhile. The
Changes pane (#146) is a tab of the results panel while Compare is on.
Link Scrolling keeps the lines the report pairs level. ``ActionHold`` greys
out the commands that don't apply and gives each its own state back on
leaving.

The window wires this in one block and hands over what it needs as a
``CompareHost``.
"""

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QDropEvent, QKeyEvent, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.actions import (
    ActionHold,
    ShellActions,
    is_unbuilt,
    set_not_available,
)
from galley.app.changes_pane import (
    CHANGES_NOUNS,
    COMPARISON_CANCELED,
    COMPARISON_STOPPED,
    ChangesPane,
    ChangesState,
    difference_count_text,
)
from galley.app.editor import CHANGE_MARKERS, ChangeMarker, CodeEditor
from galley.app.file_drop import dropped_files
from galley.app.find_bar import FindBar
from galley.app.indent import indent_unit
from galley.app.limits import COMPARE_WARNING_BYTES
from galley.app.open_file import FileReader
from galley.app.open_text import open_error_text, opened_first_text, opened_text
from galley.app.pane import PaneWidget, style_empty_heading
from galley.app.parse_session import CompareFailure, ParseSession
from galley.app.problem_text import DisplayProblem, announcement_text
from galley.app.problems_pane import ProblemsPane
from galley.app.problems_settings import STATE_OPEN
from galley.app.regex_session import RegexSession
from galley.app.status_bar import Busy, StatusBar
from galley.app.style import primary_button_style
from galley.core.compare_report import ChangeRow, CompareReport, GroupRow, ReportRow
from galley.core.formats import format_named
from galley.core.load import LoadedDocument, LoadError
from galley.core.worker import CompareEvent, CompareSide, not_comparable

LEFT_TITLE = "Left"
RIGHT_TITLE = "Right"
COMPARING_MESSAGE = "Comparing…"
COMPARING_ACCESSIBLE_NAME = "Comparing"
# The counts slot for identical documents. The Changes pane says "No
# differences." as a sentence.
NO_DIFFERENCES_COUNT = "No differences"
SIZE_WARNING_TEXT = "Comparing this much data may take a while."
# The sheet's body, Bill's v1 wording, from the 5, 8 and 16 MB timings.
SIZE_WARNING_DETAIL = (
    "Large files can take several seconds, and the window may pause for a few "
    "seconds while the results appear. Press Cancel at any point while it's "
    "comparing to stop."
)
COMPARE_ANYWAY = "Compare anyway"
# "Comparing…" and Cancel wait as long as Format's spinner (ADR 0001).
COMPARING_REVEAL_MS = 500
# An empty Right (Bill's 3e criteria), with Open Right Document….
RIGHT_PLACEHOLDER = "Paste or open the JSON or YAML to compare with."
# File › Open…, Open Recent and a drop outside the panes ask which side,
# with Left, Right (the default) and Cancel (Eric).
OPEN_SIDE_TEXT = "Open “{name}” on the left or the right?"
# Format in Compare (Eric).
FORMATTED_BOTH = "Formatted both sides."
FORMATTED_LEFT = "Formatted Left side."
FIX_ERRORS_TO_FORMAT = "Fix errors to format."


def open_side_text(file_name: str) -> str:
    """The question the Left / Right / Cancel sheet asks."""
    return OPEN_SIDE_TEXT.format(name=file_name)


def compare_needs_size_warning(left_text: str, right_text: str) -> bool:
    """True when either side is over 2 MB in UTF-8, so Compare asks first."""
    return any(
        len(text.encode("utf-8")) > COMPARE_WARNING_BYTES
        for text in (left_text, right_text)
    )


@dataclass(frozen=True)
class CompareHost:
    """What Compare mode uses from the window.

    ``busy`` is True while a Format, Copy As, file read or sheet holds the
    window. ``format_reason`` is why JSON and YAML commands are greyed out
    for the Input format, or "". ``follow_editors`` gives the panes' editors the
    View settings, ``bind_menu`` gives a pane's editor the right-click menu,
    ``toolbar_sync`` redraws the toolbar's tooltips and ``resync`` brings
    every window control up to date again after Compare.

    ``choose_path`` asks for a file with the Open dialog, or None.
    ``open_left`` opens a file already read into Input through the window's
    own Open, replace prompt included. ``remember_recent`` puts a file in
    Open Recent and ``size_text`` writes a size for the Opened status.
    ``check_format_size`` applies Format's own size rules to (text, format)
    pairs and calls back to go ahead, after its sheet when it asks.
    ``indent_choice`` is the indent size picker's choice. ``formatting_text``
    and ``format_canceled_text`` are Format's busy and canceled words.
    """

    window: QWidget
    actions: ShellActions
    left: PaneWidget
    right: PaneWidget
    results: ProblemsPane
    status: StatusBar
    session: ParseSession
    regex_session: RegexSession
    held_actions: tuple[QAction, ...]
    held_widgets: tuple[QWidget, ...]
    busy: Callable[[], bool]
    format_reason: Callable[[], str]
    apply_problems: Callable[[tuple[DisplayProblem, ...]], None]
    clear_problems: Callable[[], None]
    problems_state: Callable[[], str]
    set_problems_state: Callable[[str], None]
    layout_problems: Callable[[], None]
    follow_editors: Callable[[], None]
    bind_menu: Callable[[PaneWidget], None]
    toolbar_sync: Callable[[], None]
    resync: Callable[[], None]
    choose_path: Callable[[], Path | None]
    open_left: Callable[[Path, LoadedDocument, int], None]
    check_format_size: Callable[[list[tuple[str, str]], Callable[[], None]], None]
    remember_recent: Callable[[Path], None]
    size_text: Callable[[int], str]
    indent_choice: Callable[[], str]
    formatting_text: str
    format_canceled_text: str


@dataclass
class _FormatRun:
    """Format in Compare: the sides still to format, then what each became.

    Each side is one Format job, Left first. ``versions`` is each side's
    document version when Format was asked for. Nothing is applied until
    every side has succeeded, and nothing at all if a version moved.
    """

    sides: list[tuple[CompareSide, str, str]]
    versions: dict[CompareSide, int]
    side: CompareSide | None = None
    done: dict[CompareSide, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Saved:
    """What entering Compare set aside, for leaving to put back."""

    output_editor: CodeEditor
    output_find_bar: FindBar
    output_format: str
    left_read_only: bool
    left_flags: Qt.TextInteractionFlag
    left_position: int
    left_anchor: int
    left_scroll: int
    problems: tuple[DisplayProblem, ...]
    problems_state: str
    state_text: str
    counts_text: str


class CompareMode(QObject):
    """Turns Compare on and off and runs each compare."""

    def __init__(self, host: CompareHost) -> None:
        super().__init__(host.window)
        self._host = host
        actions = host.actions
        self._hold = ActionHold(host.held_actions, host.held_widgets)
        self._changes = ChangesPane()
        self._changes.activated.connect(self._show_row)
        self._changes.escape_pressed.connect(self._focus_side)
        self._changes.compare_again_requested.connect(self.run)
        self._right = self._build_right_editor()
        self._right_bar = FindBar(
            self._right, "right", regex_session=host.regex_session
        )
        # The window owns them from the start, so Qt deletes them with it
        # even when Compare never turns on. Without a parent they were left
        # to Python's gc, which ran their Qt destructors mid-collection.
        # Hidden until Compare places them, so they never show in the
        # window's corner or take Tab. They join the focus chain in the
        # order Compare's first placing used to add them.
        for widget in (self._right_bar, self._right, self._changes):
            widget.setParent(host.window)
            widget.hide()
        self._share_toggles(host.left.find_bar, self._right_bar)
        self._placeholder = self._build_placeholder()
        self._right.textChanged.connect(self._sync_right_footer)
        self._right_format = ""
        self._saved: _Saved | None = None
        self._job_id: int | None = None
        # The texts and formats of the running compare.
        self._submitted: tuple[str, str, str, str] | None = None
        self._format: _FormatRun | None = None
        self._size_box: QMessageBox | None = None
        self._side_box: QMessageBox | None = None
        self._menu_bound = False
        self._restoring = False
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._show_comparing)
        # A file read for one side: the read, then where it goes.
        self._reader = FileReader(self)
        self._reader.finished.connect(self._on_file_read)
        self._open_job = 0
        self._reading: tuple[Path, CompareSide, int] | None = None
        # An Opened or Formatted status that a finished compare leaves.
        self._kept_status = ""
        # Each side's document version, for Format's stale-result rule.
        self._versions: dict[CompareSide, int] = {"left": 0, "right": 0}
        # Link Scrolling: each side's line of every pair, in step.
        self._anchor_left: list[int] = []
        self._anchor_right: list[int] = []
        self._linking = False

        actions.compare.toggled.connect(self._on_toggled)
        actions.next_change.triggered.connect(self._next_change)
        actions.previous_change.triggered.connect(self._previous_change)
        actions.paste.triggered.connect(self._paste)
        actions.open_right_document.triggered.connect(self.open_right_document)
        actions.link_scrolling.toggled.connect(self._on_link_toggled)
        host.status.cancel_clicked.connect(self.cancel)
        host.results.jump_requested.connect(self._jump_in_right)
        host.left.format_changed.connect(self._on_format_changed)
        host.right.format_changed.connect(self._on_right_format_changed)
        left = host.left.editor
        # Bound methods, not lambdas: a lambda on Right's signal would keep
        # this object, Right and the window's wrapper alive after it closes.
        left.textChanged.connect(self._bump_left)
        self._right.textChanged.connect(self._bump_right)
        left.verticalScrollBar().valueChanged.connect(self._follow_left)
        left.horizontalScrollBar().valueChanged.connect(self._follow_left_across)
        self._right.verticalScrollBar().valueChanged.connect(self._follow_right)
        self._right.horizontalScrollBar().valueChanged.connect(
            self._follow_right_across
        )
        session = host.session
        session.compare_finished.connect(self._on_finished)
        session.compare_failed.connect(self._on_failed)
        session.compare_stopped.connect(self._on_stopped)
        session.succeeded.connect(self._on_format_succeeded)
        session.problems_found.connect(self._on_format_problems)
        session.failed.connect(self._on_format_failed)
        # A busy window or an XML or HTML Input greys Compare out. Each of
        # these changes when the window's own sync runs.
        for watched in (actions.format, actions.open_file, actions.copy_as_original):
            watched.changed.connect(self.sync_action)
        host.left.format_changed.connect(self.sync_action)
        self.sync_action()

    # --- State ------------------------------------------------------------

    def is_on(self) -> bool:
        return self._saved is not None

    def is_running(self) -> bool:
        return self._job_id is not None

    def changes_pane(self) -> ChangesPane:
        return self._changes

    def right_editor(self) -> CodeEditor:
        return self._right

    def sync_action(self) -> None:
        """Grey Compare out for XML or HTML Input, or while the window is busy.

        It stays on while Compare is on, so Compare can always be left.
        Open Right Document… turns Compare on, so it follows Compare.
        """
        actions = self._host.actions
        reason = "" if self.is_on() else self._host.format_reason()
        available = self.is_on() or (not reason and not self._host.busy())
        for action in (actions.compare, actions.open_right_document):
            if is_unbuilt(action):
                continue
            if action.isEnabled() != available:
                action.setEnabled(available)
            set_not_available(action, reason)

    # --- Entering and leaving ---------------------------------------------

    def _on_toggled(self, checked: bool) -> None:
        if checked == self.is_on():
            return
        if checked:
            if self._host.busy() or self._host.format_reason():
                self._set_checked(False)
                return
            self._enter()
        else:
            self._leave()

    def _set_checked(self, checked: bool) -> None:
        action = self._host.actions.compare
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def _enter(self) -> None:
        host = self._host
        left = host.left.editor
        cursor = left.textCursor()
        self._saved = _Saved(
            output_editor=host.right.editor,
            output_find_bar=host.right.find_bar,
            output_format=host.right.format_name(),
            left_read_only=left.isReadOnly(),
            left_flags=left.textInteractionFlags(),
            left_position=cursor.position(),
            left_anchor=cursor.anchor(),
            left_scroll=left.verticalScrollBar().value(),
            problems=host.results.given(),
            problems_state=host.problems_state(),
            state_text=host.status.state_text(),
            counts_text=host.status.counts_text(),
        )
        for pane in (host.left, host.right):
            pane.find_bar.close_bar()
        self._hold.set_held(True)
        host.toolbar_sync()
        _view_only(left)
        host.left.set_empty_visible(False)
        host.right.show_editor(self._right, self._right_bar)
        if not self._menu_bound:
            self._menu_bound = True
            host.bind_menu(host.right)
        host.left.set_title(LEFT_TITLE)
        host.right.set_title(RIGHT_TITLE)
        host.follow_editors()
        self._choose_right_format(self._right_format or self._saved.output_format)
        self._sync_right_footer()
        host.results.add_tab(
            self._changes, "Changes", CHANGES_NOUNS, _accent_fill, keep_open=True
        )
        host.results.set_current_page(self._changes)
        if host.problems_state() != STATE_OPEN:
            host.set_problems_state(STATE_OPEN)
        host.layout_problems()
        QWidget.setTabOrder(left, self._right)
        QWidget.setTabOrder(self._right, self._changes.list_view())
        for action in (host.actions.next_change, host.actions.previous_change):
            action.setEnabled(True)
        QApplication.instance().installEventFilter(self)
        self.sync_action()
        left.setFocus(Qt.FocusReason.OtherFocusReason)
        self.run()

    def _leave(self) -> None:
        saved = self._saved
        if saved is None:
            return
        host = self._host
        QApplication.instance().removeEventFilter(self)
        self._close_size_box()
        self._close_side_box()
        self._reading = None
        self._clear_drop_outline()
        self._cancel_job()
        self._clear_marks()
        self._right.set_problem_lines(set())
        self._right_bar.close_bar()
        host.left.find_bar.close_bar()
        self._restoring = True
        try:
            host.right.show_editor(saved.output_editor, saved.output_find_bar)
            host.left.set_title(None)
            host.right.set_title(None)
            _choose_format(host.right, saved.output_format)
        finally:
            self._restoring = False
        left = host.left.editor
        left.setReadOnly(saved.left_read_only)
        left.setTextInteractionFlags(saved.left_flags)
        # Opening into Left or Format may have made Left shorter.
        end = max(0, left.document().characterCount() - 1)
        cursor = left.textCursor()
        cursor.setPosition(min(saved.left_anchor, end))
        cursor.setPosition(min(saved.left_position, end), cursor.MoveMode.KeepAnchor)
        left.setTextCursor(cursor)
        left.verticalScrollBar().setValue(saved.left_scroll)
        host.results.remove_tab(self._changes)
        self._saved = None
        for action in (host.actions.next_change, host.actions.previous_change):
            action.setEnabled(False)
        self._hold.set_held(False)
        host.toolbar_sync()
        if saved.problems:
            host.apply_problems(saved.problems)
        else:
            host.clear_problems()
            host.status.set_state(saved.state_text)
        host.status.set_counts(saved.counts_text)
        if host.problems_state() != saved.problems_state:
            host.set_problems_state(saved.problems_state)
        host.follow_editors()
        host.resync()
        self.sync_action()
        left.setFocus(Qt.FocusReason.OtherFocusReason)

    # --- Running a compare ------------------------------------------------

    def run(self) -> None:
        """Compare Left with Right now. A running compare is dropped first."""
        if not self.is_on():
            return
        host = self._host
        left_text = host.left.editor.toPlainText()
        right_text = self._right.toPlainText()
        asked = (
            left_text,
            host.left.format_name(),
            right_text,
            host.right.format_name(),
        )
        if self._format is None and self.is_running() and asked == self._submitted:
            # An open that changes the picker and the text asks twice.
            return
        self._close_size_box()
        self._cancel_job()
        self._clear_marks()
        self._set_anchors([])
        if _is_blank(left_text) or _is_blank(right_text):
            self._show_empty()
            return
        if compare_needs_size_warning(left_text, right_text):
            self._ask_size(left_text, right_text)
            return
        self._submit(left_text, right_text)

    def cancel(self) -> bool:
        """Stop the running compare or Format, as Cancel and Esc do.

        A canceled Format changes neither side and leaves the last result.
        False when idle.
        """
        if not self.is_running():
            return False
        formatting = self._format is not None
        self._cancel_job()
        if formatting:
            if self._changes.state() is ChangesState.BUSY:
                self._show_canceled()
            self._host.status.set_state(self._host.format_canceled_text)
            return True
        self._clear_marks()
        self._show_canceled()
        self._host.status.set_state(COMPARISON_CANCELED)
        return True

    def _submit(self, left_text: str, right_text: str) -> None:
        host = self._host
        job_id = host.session.submit_compare(
            left_text,
            host.left.format_name(),
            right_text,
            host.right.format_name(),
        )
        if job_id is None:
            self._show_stopped()
            return
        self._job_id = job_id
        self._submitted = (
            left_text,
            host.left.format_name(),
            right_text,
            host.right.format_name(),
        )
        # The last result's problems go with it.
        self._clear_problems()
        self._changes.show_busy()
        self._set_count(0)
        host.status.set_counts("")
        self._reveal_timer.start(COMPARING_REVEAL_MS)

    def _show_comparing(self) -> None:
        if not self.is_running():
            return
        status = self._host.status
        if self._format is not None:
            status.set_busy(Busy.SPINNER, cancel=True)
            status.set_state(self._host.formatting_text)
            return
        status.set_busy(Busy.SPINNER, COMPARING_ACCESSIBLE_NAME, cancel=True)
        status.set_state(COMPARING_MESSAGE)

    def _cancel_job(self) -> None:
        """Drop the running compare or Format quietly. The caller says what
        happened."""
        self._format = None
        if self._job_id is None:
            return
        self._end_job()
        self._host.session.cancel()

    def _end_job(self) -> None:
        self._job_id = None
        self._submitted = None
        self._reveal_timer.stop()
        self._host.status.set_busy(Busy.IDLE)

    def _ask_size(self, left_text: str, right_text: str) -> None:
        """Ask before a compare with a side over 2 MB. One answer, one compare."""
        box = QMessageBox(self._host.window)
        box.setObjectName("compareSizeWarning")
        box.setWindowTitle("Galley")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setText(SIZE_WARNING_TEXT)
        box.setInformativeText(SIZE_WARNING_DETAIL)
        anyway = box.addButton(COMPARE_ANYWAY, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(anyway)
        box.setEscapeButton(cancel)
        self._size_box = box

        def _choose() -> None:
            if self._size_box is not box:
                return
            accepted = box.clickedButton() is anyway
            self._size_box = None
            box.deleteLater()
            if not self.is_on():
                return
            if accepted:
                self._submit(left_text, right_text)
            else:
                self._show_canceled()

        box.finished.connect(_choose)
        box.open()

    def _close_size_box(self) -> None:
        box = self._size_box
        if box is None:
            return
        self._size_box = None
        box.reject()
        box.deleteLater()

    # --- Results ----------------------------------------------------------

    def _on_finished(self, job_id: int, event: object) -> None:
        if job_id != self._job_id or not isinstance(event, CompareEvent):
            return
        self._end_job()
        host = self._host
        self._clear_problems()
        report = event.report
        self._changes.set_report(report)
        self._set_count(report.count)
        counts = (
            difference_count_text(report.count)
            if report.count
            else NO_DIFFERENCES_COUNT
        )
        host.status.set_counts(counts)
        # A file just opened, or Format, keeps its status.
        if not self._kept_status or host.status.state_text() != self._kept_status:
            host.status.set_state("Ready")
        self._kept_status = ""
        host.status.announce(counts)
        self._mark(report)
        self._set_anchors(report.anchor_pairs())
        first = self._changes.model().row_at(self._changes.list_view().current_row())
        if first is not None:
            self._show_row(first)
        else:
            self.link_scroll(host.left.editor)

    def _on_failed(self, job_id: int, failure: object) -> None:
        if job_id != self._job_id or not isinstance(failure, CompareFailure):
            return
        self._end_job()
        host = self._host
        self._changes.show_errors()
        self._set_count(0)
        host.status.set_counts("")
        self._clear_problems()
        if not failure.problems:
            host.status.set_state(failure.message)
            return
        if failure.side == "right":
            self._show_right_problems(failure.problems)
        else:
            host.apply_problems(failure.problems)
        host.results.tab_bar().setCurrentIndex(0)

    def _clear_problems(self) -> None:
        """Clear the Problems rows and both sides' problem marks."""
        self._host.clear_problems()
        self._right.set_problem_lines(set())

    def _show_right_problems(self, rows: tuple[DisplayProblem, ...]) -> None:
        """List Right's problems and mark Right's gutter, as Input's are."""
        results = self._host.results
        results.set_problems(rows)
        shown = results.shown()
        messages: dict[int, list[str]] = {}
        for row in shown:
            messages.setdefault(row.lineno, []).append(row.full_message())
        self._right.set_problem_messages(
            {lineno: "\n".join(texts) for lineno, texts in messages.items()}
        )
        self._host.layout_problems()
        status = self._host.status
        status.set_problem_count(results.count())
        if shown:
            status.announce(announcement_text(results.count(), shown[0]))

    def _jump_in_right(self, lineno: int, colno: int) -> None:
        """A Problems row jumps in the side its problem is in."""
        if self._right.problem_lines():
            self._right.reveal_line(lineno, colno)

    def _on_stopped(self, job_id: int) -> None:
        if job_id != self._job_id:
            return
        self._end_job()
        self._show_stopped()

    def _show_empty(self) -> None:
        self._changes.show_empty_right()
        self._set_count(0)
        self._host.status.set_counts("")

    def _show_canceled(self) -> None:
        self._changes.show_canceled()
        self._set_count(0)
        self._host.status.set_counts("")

    def _show_stopped(self) -> None:
        self._changes.show_stopped()
        self._set_count(0)
        self._host.status.set_counts("")
        self._host.status.set_state(COMPARISON_STOPPED)

    def _set_count(self, count: int) -> None:
        if self.is_on():
            self._host.results.set_count(self._changes, count)

    # --- Markers and jumps ------------------------------------------------

    def _mark(self, report: CompareReport) -> None:
        names = _first_names(report.rows)
        for side, editor, marks in (
            ("left", self._host.left.editor, report.left),
            ("right", self._right, report.right),
        ):
            spoken = names[side]
            editor.set_line_markers(
                CHANGE_MARKERS,
                {
                    line: ChangeMarker(kind, spoken.get(line, ""))
                    for line, kind in marks.markers.items()
                },
            )
            editor.set_change_tints(side, marks.tints)

    def _clear_marks(self) -> None:
        sides: tuple[tuple[CompareSide, CodeEditor], ...] = (
            ("left", self._host.left.editor),
            ("right", self._right),
        )
        for side, editor in sides:
            editor.clear_line_markers(CHANGE_MARKERS)
            editor.set_change_tints(side, ())

    def _show_row(self, row: object) -> None:
        """Select a change's spans: Left, Right, or both. Focus stays put.

        Each side centers its own span, so Link Scrolling waits: a Moved
        change can be far apart on the two sides.
        """
        change = _first_change(row)
        if change is None:
            return
        self._linking = True
        try:
            if change.left_span is not None:
                self._host.left.editor.select_span(change.left_span)
            if change.right_span is not None:
                self._right.select_span(change.right_span)
        finally:
            self._linking = False

    def _next_change(self) -> None:
        if self.is_on() and not self.is_running():
            self._changes.next_change()

    def _previous_change(self) -> None:
        if self.is_on() and not self.is_running():
            self._changes.previous_change()

    # --- Format -----------------------------------------------------------

    def format(self) -> None:
        """Format both sides, each as its own Format job, Left first.

        Each side keeps its own format. Sort keys is ignored, and the indent
        is the indent size picker's, in spaces for YAML. Format's own size
        rules apply. Both results are held, then applied in one step: Left
        as one undo step on Input, Right replaced. If either side has
        errors, or changed meanwhile, neither changes. An empty Right is
        skipped. The compare then runs once more.
        """
        if not self.is_on() or self._format is not None or self._size_box is not None:
            return
        host = self._host
        sides: list[tuple[CompareSide, str, str]] = [
            ("left", host.left.editor.toPlainText(), host.left.format_name()),
            ("right", self._right.toPlainText(), host.right.format_name()),
        ]
        sides = [side for side in sides if not _is_blank(side[1])]
        if not sides:
            return
        versions = dict(self._versions)
        host.check_format_size(
            [(text, source) for _side, text, source in sides],
            lambda: self._start_format(sides, versions),
        )

    def _start_format(
        self,
        sides: list[tuple[CompareSide, str, str]],
        versions: dict[CompareSide, int],
    ) -> None:
        if not self.is_on() or self._format is not None:
            return
        self._close_size_box()
        self._cancel_job()
        if self._host.session.active_job_id is not None:
            return
        self._format = _FormatRun(list(sides), versions)
        self._reveal_timer.start(COMPARING_REVEAL_MS)
        self._submit_format()

    def _submit_format(self) -> None:
        run = self._format
        assert run is not None
        side, text, source = run.sides.pop(0)
        run.side = side
        session = self._host.session
        session.submit(
            text,
            source,
            source,
            sort_keys=False,
            indent=indent_unit(self._host.indent_choice(), source),
        )
        job_id = session.active_job_id
        if job_id is None:
            self._format = None
            self._end_job()
            self._show_stopped()
            return
        self._job_id = job_id

    def _on_format_succeeded(self, text: str) -> None:
        run = self._format
        if run is None or run.side is None:
            return
        if self._host.session.finished_job_id != self._job_id:
            return
        run.done[run.side] = text
        if run.sides:
            self._job_id = None
            self._submit_format()
            return
        self._format = None
        self._end_job()
        self._apply_formatted(run)

    def _apply_formatted(self, run: _FormatRun) -> None:
        """Both sides in one step, or neither when a side changed meanwhile."""
        host = self._host
        if any(self._versions[side] != run.versions[side] for side in run.versions):
            # The same stale-result rule as #170: drop both.
            host.status.set_state("Ready")
            return
        left = host.left.editor
        changed: list[CodeEditor] = []
        for side, editor in (("left", left), ("right", self._right)):
            text = run.done.get(side)
            if text is None or text == editor.toPlainText():
                continue
            blocked = editor.blockSignals(True)
            try:
                if editor is left:
                    _replace_as_one_undo(editor, text)
                else:
                    editor.setPlainText(text)
            finally:
                editor.blockSignals(blocked)
            changed.append(editor)
        for editor in changed:
            editor.textChanged.emit()
        status = FORMATTED_BOTH if "right" in run.done else FORMATTED_LEFT
        host.status.set_state(status)
        self._kept_status = status
        self.run()

    def _on_format_problems(self, problems: object) -> None:
        run = self._format
        if run is None or not isinstance(problems, tuple):
            return
        rows = tuple(item for item in problems if isinstance(item, DisplayProblem))
        if not rows:
            return
        side = run.side
        self._format = None
        self._end_job()
        host = self._host
        self._clear_problems()
        if side == "right":
            self._show_right_problems(rows)
        else:
            host.apply_problems(rows)
        host.results.tab_bar().setCurrentIndex(0)
        host.status.set_state(FIX_ERRORS_TO_FORMAT)

    def _on_format_failed(self, message: str) -> None:
        if self._format is None:
            return
        if self._host.session.has_pending_job:
            # The job waits out a reaped worker and still runs.
            self._host.status.set_state(message)
            return
        self._format = None
        self._end_job()
        self._host.status.set_state(message)

    def _bump_left(self) -> None:
        self._versions["left"] += 1

    def _bump_right(self) -> None:
        self._versions["right"] += 1

    # --- Opening ----------------------------------------------------------

    def open_right_document(self) -> None:
        """Choose a file for Right. Outside Compare, Compare turns on first."""
        host = self._host
        if not self._can_open():
            return
        if not self.is_on() and host.format_reason():
            return
        path = host.choose_path()
        if path is None:
            return
        if not self.is_on():
            host.actions.compare.setChecked(True)
            if not self.is_on():
                return
        self._read(path, "right")

    def open_file(self) -> None:
        """File › Open… while comparing: choose a file, then a side."""
        if not self.is_on() or not self._can_open():
            return
        path = self._host.choose_path()
        if path is not None:
            self._ask_side(path)

    def open_recent(self, path: Path) -> None:
        """An Open Recent file while comparing asks for a side."""
        if self.is_on() and self._can_open():
            self._ask_side(path)

    def left_opened(self) -> None:
        """Input took a file opened into Left: compare it."""
        if not self.is_on():
            return
        self._kept_status = self._host.status.state_text()
        self.run()

    def _can_open(self) -> bool:
        return not (self._host.busy() or self._side_box is not None or self._reading)

    def _ask_side(self, path: Path, of_files: int = 1) -> None:
        """Left, Right (the default) or Cancel (Esc)."""
        box = QMessageBox(self._host.window)
        box.setObjectName("compareOpenSide")
        QWidget.setWindowTitle(box, "Galley")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(open_side_text(path.name))
        left = box.addButton(LEFT_TITLE, QMessageBox.ButtonRole.AcceptRole)
        right = box.addButton(RIGHT_TITLE, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(right)
        box.setEscapeButton(cancel)
        self._side_box = box

        def _choose() -> None:
            if self._side_box is not box:
                return
            clicked = box.clickedButton()
            self._side_box = None
            box.deleteLater()
            if not self.is_on():
                return
            if clicked is left:
                self._read(path, "left", of_files)
            elif clicked is right:
                self._read(path, "right", of_files)

        box.finished.connect(_choose)
        box.open()

    def _close_side_box(self) -> None:
        box = self._side_box
        if box is None:
            return
        self._side_box = None
        box.reject()
        box.deleteLater()

    def _read(self, path: Path, side: CompareSide, of_files: int = 1) -> None:
        """Read ``path`` off the UI thread, then check it can be compared."""
        self._open_job += 1
        self._reading = (path, side, of_files)
        self._reader.read(self._open_job, path)

    def _on_file_read(self, job: int, result: object) -> None:
        reading = self._reading
        if job != self._open_job or reading is None:
            return
        self._reading = None
        path, side, of_files = reading
        if not self.is_on():
            return
        status = self._host.status
        if isinstance(result, LoadError):
            status.set_state(open_error_text(path.name, result), str(path))
            return
        if not isinstance(result, LoadedDocument):
            return
        refused = None if result.fmt is None else not_comparable(result.fmt)
        if refused is not None:
            status.set_state(refused, str(path))
            return
        if side == "left":
            # The window's Open with the text read here: its replace prompt,
            # one undo step, title and Recent. No second read.
            self._host.open_left(path, result, of_files)
            return
        self._load_right(path, result, of_files)

    def _load_right(self, path: Path, loaded: LoadedDocument, of_files: int) -> None:
        host = self._host
        self._right.setPlainText(loaded.text)
        self._choose_right_format(loaded.fmt or _format_guess(loaded.text))
        host.remember_recent(path)
        status = opened_text(path.name, host.size_text(loaded.size))
        if of_files > 1:
            status = opened_first_text(of_files)
        host.status.set_state(status, str(path))
        self._kept_status = status
        self.run()

    # --- Link Scrolling ---------------------------------------------------

    def link_scroll(self, source: CodeEditor) -> None:
        """Scroll the other side so its matching line is level with
        ``source``'s top line.

        The report's line pairs match key paths. Between two pairs, and
        before any result, lines are matched in proportion.
        """
        if (
            self._linking
            or not self.is_on()
            or not self._host.actions.link_scrolling.isChecked()
        ):
            return
        left = self._host.left.editor
        if source is left:
            target, mine, theirs = self._right, self._anchor_left, self._anchor_right
        else:
            target, mine, theirs = left, self._anchor_right, self._anchor_left
        line = _map_line(
            _top_line(source), mine, theirs, source.blockCount(), target.blockCount()
        )
        self._linking = True
        try:
            target.verticalScrollBar().setValue(_scroll_value(target, line))
        finally:
            self._linking = False

    def _follow_left(self, _value: int) -> None:
        self.link_scroll(self._host.left.editor)

    def _follow_right(self, _value: int) -> None:
        self.link_scroll(self._right)

    def _follow_left_across(self, value: int) -> None:
        self._link_across(self._right, value)

    def _follow_right_across(self, value: int) -> None:
        self._link_across(self._host.left.editor, value)

    def _link_across(self, target: CodeEditor, value: int) -> None:
        if (
            self._linking
            or not self.is_on()
            or not self._host.actions.link_scrolling.isChecked()
        ):
            return
        self._linking = True
        try:
            target.horizontalScrollBar().setValue(value)
        finally:
            self._linking = False

    def _on_link_toggled(self, checked: bool) -> None:
        if checked:
            self.link_scroll(self._host.left.editor)

    def _set_anchors(self, pairs: list[tuple[int, int]]) -> None:
        self._anchor_left = [left for left, _right in pairs]
        self._anchor_right = [right for _left, right in pairs]

    # --- Right ------------------------------------------------------------

    def _build_right_editor(self) -> CodeEditor:
        editor = CodeEditor()
        editor.setObjectName("rightEditor")
        editor.setAccessibleName(f"{RIGHT_TITLE} editor")
        _view_only(editor)
        editor.hide()
        return editor

    def _paste(self) -> None:
        """Paste replaces all of Right, wherever focus is (3d)."""
        if not self.is_on():
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        self._right.setPlainText(text)
        self._choose_right_format(_format_guess(text))
        self.run()

    def _choose_right_format(self, label: str) -> None:
        if label not in self._host.right.format_choices():
            self._right_format = self._host.right.format_name()
            return
        self._right_format = label
        self._restoring = True
        try:
            _choose_format(self._host.right, label)
        finally:
            self._restoring = False

    def _on_right_format_changed(self, label: str) -> None:
        if not self.is_on() or self._restoring:
            return
        self._right_format = label
        self.run()

    def _on_format_changed(self, _label: str) -> None:
        if self.is_on() and not self._restoring:
            self.run()

    def _build_placeholder(self) -> QWidget:
        """Right's empty state: Bill's line and Open Right Document…."""
        overlay = QWidget()
        overlay.setObjectName("emptyRight")
        overlay.setStyleSheet("background: transparent;")
        heading = QLabel(RIGHT_PLACEHOLDER)
        heading.setObjectName("rightEmptyTitle")
        heading.setWordWrap(True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        style_empty_heading(heading)
        open_right = self._host.actions.open_right_document
        button = QPushButton(open_right.text())
        button.setObjectName("openRightButton")
        button.setStyleSheet(primary_button_style())
        button.clicked.connect(open_right.trigger)
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._right.attach_overlay(overlay)
        overlay.hide()
        return overlay

    def _sync_right_footer(self) -> None:
        if not self.is_on():
            return
        pane = self._host.right
        text = self._right.toPlainText()
        pane.count_label.setText(_line_label(self._right.blockCount()) if text else "")
        if pane.copy_button is not None:
            pane.copy_button.setEnabled(bool(text))
        empty = _is_blank(text)
        self._placeholder.setVisible(empty)
        if empty:
            self._right.place_overlay()

    def _share_toggles(self, first: FindBar, second: FindBar) -> None:
        """Right's Regex and Whole word follow the other bars', as theirs do."""
        for name in ("regex_button", "whole_word_button"):
            one, two = (getattr(bar, name) for bar in (first, second))
            one.toggled.connect(two.setChecked)
            two.toggled.connect(one.setChecked)

    def _focus_side(self) -> None:
        """Esc in Changes goes back to the editor. Compare stays on."""
        self._host.left.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    # --- Esc and drops ----------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """While comparing, Esc closes a find bar, then cancels the compare.

        Idle, Esc goes on to the widget, so it never leaves Compare. A file
        dropped on Left or Right loads that side; anywhere else in the
        window it asks which side. Other drags are refused.
        """
        if not isinstance(watched, QWidget) or not _is_inside(
            watched, self._host.window
        ):
            return False
        kind = event.type()
        if kind == QEvent.Type.DragLeave:
            self._clear_drop_outline()
            return False
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            assert isinstance(event, QDropEvent)
            self._take_drop(watched, event, dropped=kind == QEvent.Type.Drop)
            return True
        if (
            kind != QEvent.Type.KeyPress
            or not isinstance(event, QKeyEvent)
            or event.key() != Qt.Key.Key_Escape
            or QApplication.activeModalWidget() is not None
        ):
            return False
        for bar in (self._host.left.find_bar, self._right_bar):
            if bar.is_open():
                if _is_inside(watched, bar):
                    return False
                bar.close_bar()
                return True
        return self.cancel()

    def _take_drop(self, watched: QWidget, event: QDropEvent, *, dropped: bool) -> None:
        """Follow a file drag, outlining the side it would load, or open it."""
        files = dropped_files(event.mimeData())
        if not files or not self._can_open():
            self._clear_drop_outline()
            event.ignore()
            return
        event.acceptProposedAction()
        side = self._side_under(watched)
        if not dropped:
            for pane, name in ((self._host.left, "left"), (self._host.right, "right")):
                pane.set_drop_highlight(side == name)
            return
        self._clear_drop_outline()
        if side is None:
            self._ask_side(files[0], len(files))
        else:
            self._read(files[0], side, len(files))

    def _side_under(self, widget: QWidget) -> CompareSide | None:
        if _is_inside(widget, self._host.left):
            return "left"
        if _is_inside(widget, self._host.right):
            return "right"
        return None

    def _clear_drop_outline(self) -> None:
        for pane in (self._host.left, self._host.right):
            pane.set_drop_highlight(False)


def _replace_as_one_undo(editor: CodeEditor, text: str) -> None:
    """Replace the whole document as a single undo step, as Open does."""
    cursor = editor.textCursor()
    cursor.beginEditBlock()
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.insertText(text)
    cursor.endEditBlock()
    editor.setTextCursor(cursor)


def _top_line(editor: CodeEditor) -> float:
    """The 1-based line at the top of ``editor``, with a fraction for a
    wrapped line part of the way down."""
    value = editor.verticalScrollBar().value()
    block = editor.document().findBlockByLineNumber(value)
    if not block.isValid():
        return float(editor.blockCount())
    part = (value - block.firstLineNumber()) / max(1, block.lineCount())
    return block.blockNumber() + 1 + min(max(part, 0.0), 1.0)


def _scroll_value(editor: CodeEditor, line: float) -> int:
    """The scroll value that puts 1-based ``line`` at the top of ``editor``."""
    number = min(max(int(line), 1), editor.blockCount())
    block = editor.document().findBlockByNumber(number - 1)
    part = min(max(line - number, 0.0), 1.0)
    return block.firstLineNumber() + int(part * max(1, block.lineCount()))


def _map_line(
    line: float, mine: list[int], theirs: list[int], my_count: int, their_count: int
) -> float:
    """The other side's line for ``line``, between the pairs around it.

    The first lines and the last lines pair up too. With no pairs, lines
    match by number.
    """
    if not mine:
        return line
    index = bisect_right(mine, line)
    if index:
        start = (float(mine[index - 1]), float(theirs[index - 1]))
    else:
        start = (1.0, 1.0)
    if index < len(mine):
        end = (float(mine[index]), float(theirs[index]))
    else:
        end = (float(max(my_count, mine[-1])), float(max(their_count, theirs[-1])))
    if end[0] <= start[0]:
        return start[1] + (line - start[0])
    share = (line - start[0]) / (end[0] - start[0])
    return start[1] + share * (end[1] - start[1])


def _view_only(editor: CodeEditor) -> None:
    """Read-only, with the caret and selection still on the keyboard."""
    editor.setReadOnly(True)
    editor.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )


def _first_change(row: object) -> ChangeRow | None:
    if isinstance(row, ChangeRow):
        return row
    if isinstance(row, GroupRow):
        return next(
            (child for child in row.children if isinstance(child, ChangeRow)), None
        )
    return None


def _first_names(rows: tuple[ReportRow, ...]) -> dict[str, dict[int, str]]:
    """Each side's marked line and the spoken name of its first change."""
    names: dict[str, dict[int, str]] = {"left": {}, "right": {}}
    for change in _changes_in(rows):
        for side, line in (("left", change.left_line), ("right", change.right_line)):
            if line is not None:
                names[side].setdefault(line, change.accessible_name)
    return names


def _changes_in(rows: tuple[ReportRow, ...]) -> list[ChangeRow]:
    found: list[ChangeRow] = []
    for row in rows:
        if isinstance(row, ChangeRow):
            found.append(row)
        elif isinstance(row, GroupRow):
            found.extend(
                child for child in row.children if isinstance(child, ChangeRow)
            )
    return found


def _format_guess(text: str) -> str:
    """JSON when the text opens with ``{`` or ``[``, else YAML.

    The editor guesses its colors the same way.
    """
    stripped = text.lstrip()
    label = "JSON" if stripped[:1] in ("{", "[") else "YAML"
    return format_named(label).label


def _choose_format(pane: PaneWidget, label: str) -> None:
    combo = pane.format_combo
    index = combo.findText(label)
    if index >= 0:
        combo.setCurrentIndex(index)


def _is_blank(text: str) -> bool:
    return text.strip() == ""


def _line_label(count: int) -> str:
    return f"{count} line" if count == 1 else f"{count} lines"


def _is_inside(widget: QWidget | None, ancestor: QWidget) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.parentWidget()
    return False


def _accent_fill(palette: QPalette) -> QColor:
    """The Changes badge: the accent, beside Problems' error fill."""
    return palette.color(QPalette.ColorRole.Accent)
