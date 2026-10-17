"""Galley main window: menus, toolbar, panes, and status bar."""

import time

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QKeySequence,
    QPalette,
    QResizeEvent,
    QShowEvent,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.actions import ShellActions
from galley.app.editor import CodeEditor
from galley.app.fonts import CODE_POINT_SIZE, ui_font
from galley.app.pane import PaneRole, PaneWidget
from galley.app.parse_session import ParseSession
from galley.app.splitter import PaneSplitter
from galley.app.style import (
    menu_style,
    muted_text_style,
    primary_button_style,
    progress_style,
    status_bar_style,
)
from galley.app.theme import (
    EDITOR_BG,
    GUTTER_BG,
    SELECTION,
    TEXT,
    TEXT_ON_ACCENT,
    WINDOW_BG,
)

NOT_YET_MESSAGE = "Not yet available."
CANCELLED_MESSAGE = "Cancelled. Showing the previous result."
FORMATTING_MESSAGE = "Formatting…"
WRITING_MESSAGE = "Writing output…"
_NOT_YET_MS = 4000

# Placeholders until Isaac's benchmark (ADR 0001, UI spec §10). Not tuned.
# The spinner and Cancel wait this long. The write bar waits longer.
SPINNER_REVEAL_MS = 500
PROGRESS_BAR_REVEAL_MS = 2000


class MainWindow(QMainWindow):
    """The single Galley window in its empty, ready state."""

    def __init__(self) -> None:
        super().__init__()
        self._initial_show_done = False
        self._split_settled = False
        self._splitter: PaneSplitter | None = None
        self._formatting = False
        self._format_started_s = 0.0
        self._pending_write: tuple[int, int] | None = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._apply_deferred_reveal)
        self._output_before_format = ""
        self._format_source = "JSON"
        self._format_target = "JSON"
        self._format_idle_text = ""
        self._session = ParseSession(self)
        self.setWindowTitle("Galley")
        self.setFont(ui_font())
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)
        self._apply_palette()
        self._actions = ShellActions(self)
        self._build_menus()
        self._input_pane = PaneWidget(PaneRole.INPUT)
        self._output_pane = PaneWidget(PaneRole.OUTPUT)
        self._active_editor = self._input_pane.editor
        self._splitter = self._build_splitter()
        self._editing = self._build_caption()
        self._format_button = self._build_format_button()
        toolbar = self._build_toolbar()
        self._status_message, self._direction = self._build_status_bar()
        self._assemble(toolbar)
        self._connect()
        self._sync_input_state()
        self._sync_output_state()
        self._sync_direction()
        self._splitter.apply_even_split()

    def start_parse_worker(self) -> None:
        """Start the warm formatter after runtime configuration."""
        self._session.start()

    def shutdown_parse_worker(self) -> None:
        """Reap the formatter when the window is done with it."""
        self._session.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._session.shutdown()
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._initial_show_done:
            return
        self._initial_show_done = True
        self._focus_input()
        self._settle_split()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._settle_split()

    def _settle_split(self) -> None:
        splitter = self._splitter
        if splitter is None or self._split_settled or splitter.width() <= 200:
            return
        self._split_settled = True
        splitter.apply_even_split()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        fullscreen = self.isFullScreen()
        action = self._actions.full_screen
        if action.isChecked() != fullscreen:
            action.setChecked(fullscreen)

    def _apply_palette(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
        palette.setColor(QPalette.ColorRole.Base, QColor(EDITOR_BG))
        palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
        palette.setColor(QPalette.ColorRole.Button, QColor(WINDOW_BG))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT_ON_ACCENT))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    def _build_menus(self) -> None:
        actions = self._actions
        bar = self.menuBar()
        bar.setStyleSheet(menu_style())

        galley = _add_menu(bar, "Galley")
        galley.addAction(actions.about)
        galley.addAction(actions.preferences)
        galley.addSeparator()
        galley.addAction(actions.quit)

        file_menu = _add_menu(bar, "File")
        file_menu.addAction(actions.open_file)

        edit = _add_menu(bar, "Edit")
        edit.addAction(actions.undo)
        edit.addAction(actions.redo)
        edit.addSeparator()
        edit.addAction(actions.cut)
        edit.addAction(actions.copy)
        edit.addAction(actions.paste)
        edit.addAction(actions.select_all)
        edit.addSeparator()
        edit.addAction(actions.find)
        edit.addAction(actions.find_replace)
        edit.addSeparator()
        edit.addAction(actions.escape_string)
        edit.addAction(actions.unescape_string)

        view = _add_menu(bar, "View")
        font_menu = view.addMenu("Font")
        font_menu.setObjectName("menuFont")
        view.addAction(actions.bigger)
        view.addAction(actions.smaller)
        view.addAction(actions.actual_size)
        view.addSeparator()
        view.addAction(actions.word_wrap)
        view.addAction(actions.show_whitespace)
        guides = view.addMenu("Indent Guides")
        guides.setObjectName("menuIndentGuides")
        guides.addAction(actions.guides_off)
        guides.addAction(actions.guides_lines)
        guides.addAction(actions.guides_depth)
        view.addSeparator()
        view.addAction(actions.full_screen)

        window_menu = _add_menu(bar, "Window")
        window_menu.addAction(actions.pop_out)
        window_menu.addAction(actions.show_window)
        window_menu.addAction(actions.bring_back)

        _add_menu(bar, "Help")

        self._tab_menu = QMenu(self)
        self._tab_menu.setObjectName("tabMenu")
        self._tab_menu.addAction(actions.rename)
        self._tab_menu.addAction(actions.duplicate)
        self._tab_menu.addAction(actions.pop_out)
        self._tab_menu.addSeparator()
        self._tab_menu.addAction(actions.close_tab)
        self._tab_menu.addAction(actions.close_others)
        self._tab_menu.addAction(actions.close_tabs_right)

        self._editor_menu = QMenu(self)
        self._editor_menu.setObjectName("editorMenu")
        self._editor_menu.addAction(actions.undo)
        self._editor_menu.addAction(actions.redo)
        self._editor_menu.addSeparator()
        self._editor_menu.addAction(actions.cut)
        self._editor_menu.addAction(actions.copy)
        self._editor_menu.addAction(actions.paste)
        self._editor_menu.addAction(actions.select_all)

    def _build_splitter(self) -> PaneSplitter:
        splitter = PaneSplitter()
        splitter.addWidget(self._input_pane)
        splitter.addWidget(self._output_pane)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _build_caption(self) -> QLabel:
        caption = QLabel("Editing <b>Input 1</b>")
        caption.setObjectName("editingCaption")
        caption.setTextFormat(Qt.TextFormat.RichText)
        caption.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        caption.setStyleSheet(muted_text_style() + " font-size: 11px;")
        return caption

    def _build_format_button(self) -> QPushButton:
        button = QPushButton()
        button.setObjectName("formatButton")
        button.setAccessibleName("Format")
        button.setStyleSheet(primary_button_style())
        button.setMinimumHeight(28)
        hint = self._actions.format.shortcut().toString(
            QKeySequence.SequenceFormat.NativeText
        )
        self._format_idle_text = f"{{·}} Format {hint}".rstrip()
        button.setText(self._format_idle_text)
        self._format_button = button
        self._actions.format.enabledChanged.connect(self._on_format_action_enabled)
        button.setEnabled(self._actions.format.isEnabled())
        button.clicked.connect(self._actions.format.trigger)
        return button

    def _build_toolbar(self) -> QWidget:
        actions = self._actions
        toolbar = QWidget()
        toolbar.setObjectName("mainToolbar")
        toolbar.setMinimumHeight(56)
        toolbar.setAutoFillBackground(True)

        indent = QComboBox()
        indent.setObjectName("indentSize")
        indent.setAccessibleName("Indent size")
        indent.setToolTip("Indent size")
        indent.addItems(["2 spaces", "4 spaces", "Tab"])

        tray = QFrame()
        tray.setObjectName("undoRedoTray")
        tray.setStyleSheet(
            f"QFrame#undoRedoTray {{ background: {GUTTER_BG}; border-radius: 4px; }}"
        )
        tray_row = QHBoxLayout(tray)
        tray_row.setContentsMargins(2, 2, 2, 2)
        tray_row.setSpacing(0)
        tray_row.addWidget(_tool_button(actions.undo))
        tray_row.addWidget(_tool_button(actions.redo))

        format_column = QWidget()
        format_layout = QVBoxLayout(format_column)
        format_layout.setContentsMargins(0, 2, 0, 2)
        format_layout.setSpacing(0)
        format_layout.addWidget(self._format_button)
        format_layout.addWidget(self._editing)

        row = QHBoxLayout(toolbar)
        row.setContentsMargins(12, 4, 12, 4)
        row.setSpacing(4)
        row.addWidget(_tool_button(actions.paste))
        row.addWidget(_tool_button(actions.copy))
        row.addSpacing(16)
        row.addWidget(tray)
        row.addSpacing(16)
        row.addWidget(_tool_button(actions.outdent))
        row.addWidget(_tool_button(actions.indent))
        row.addWidget(indent)
        row.addSpacing(16)
        row.addWidget(_tool_button(actions.sort_keys))
        row.addWidget(_tool_button(actions.minify))
        row.addSpacing(16)
        row.addWidget(_tool_button(actions.expand_all))
        row.addWidget(_tool_button(actions.collapse_all))
        row.addSpacing(16)
        row.addWidget(_tool_button(actions.validate))
        row.addStretch(1)
        row.addWidget(_tool_button(actions.find))
        row.addSpacing(8)
        row.addWidget(format_column)
        return toolbar

    def _build_status_bar(self) -> tuple[QLabel, QLabel]:
        status = self.statusBar()
        status.setStyleSheet(status_bar_style())
        status.setSizeGripEnabled(True)
        message = QLabel("Ready")
        message.setObjectName("statusMessage")
        message.setStyleSheet(muted_text_style())
        self._format_spinner = QProgressBar()
        self._format_spinner.setObjectName("formatSpinner")
        self._format_spinner.setAccessibleName("Formatting")
        self._format_spinner.setRange(0, 0)
        self._format_spinner.setTextVisible(False)
        self._format_spinner.setFixedWidth(48)
        self._format_spinner.setStyleSheet(progress_style())
        self._format_spinner.hide()
        self._write_progress = QProgressBar()
        self._write_progress.setObjectName("writeProgress")
        self._write_progress.setAccessibleName("Writing output")
        self._write_progress.setTextVisible(True)
        self._write_progress.setFormat("%p%")
        self._write_progress.setFixedWidth(120)
        self._write_progress.setStyleSheet(progress_style())
        self._write_progress.hide()
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setObjectName("cancelButton")
        self._cancel_button.setAccessibleName("Cancel")
        self._cancel_button.hide()
        direction = QLabel("JSON → JSON")
        direction.setObjectName("directionLabel")
        direction.setStyleSheet(muted_text_style())
        status.addWidget(self._format_spinner)
        status.addWidget(message, 1)
        status.addWidget(self._write_progress)
        status.addWidget(self._cancel_button)
        status.addPermanentWidget(direction)
        return message, direction

    def _assemble(self, toolbar: QWidget) -> None:
        central = QWidget()
        central.setObjectName("central")
        central.setAutoFillBackground(True)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(16, 8, 16, 8)
        host_layout.addWidget(self._splitter)
        layout.addWidget(host, 1)
        self.setCentralWidget(central)

    def _connect(self) -> None:
        actions = self._actions
        for stub in actions.stubs():
            stub.triggered.connect(self._not_yet)
        actions.quit.triggered.connect(QApplication.quit)
        actions.undo.triggered.connect(self._undo)
        actions.redo.triggered.connect(self._redo)
        actions.cut.triggered.connect(self._cut)
        actions.copy.triggered.connect(self._copy)
        actions.paste.triggered.connect(self._paste)
        actions.select_all.triggered.connect(self._select_all)
        actions.word_wrap.toggled.connect(self._set_word_wrap)
        actions.show_whitespace.toggled.connect(self._set_show_whitespace)
        actions.bigger.triggered.connect(self._zoom_in)
        actions.smaller.triggered.connect(self._zoom_out)
        actions.actual_size.triggered.connect(self._actual_size)
        actions.full_screen.toggled.connect(self._set_full_screen)
        actions.format.triggered.connect(self._format)
        self._cancel_button.clicked.connect(self._cancel_format)
        self._session.write_progress.connect(self._on_write_progress)
        self._session.succeeded.connect(self._on_format_succeeded)
        self._session.failed.connect(self._on_format_failed)

        self._input_pane.editor.textChanged.connect(self._sync_input_state)
        self._output_pane.editor.textChanged.connect(self._sync_output_state)
        self._input_pane.format_changed.connect(self._sync_direction)
        self._output_pane.format_changed.connect(self._sync_direction)
        self._bind_pane(self._input_pane)
        self._bind_pane(self._output_pane)

        input_paste = self._input_pane.paste_button
        footer_paste = self._input_pane.footer_paste_button
        open_button = self._input_pane.open_button
        clear_button = self._input_pane.clear_button
        if input_paste is not None:
            input_paste.clicked.connect(self._paste_input)
        if footer_paste is not None:
            footer_paste.clicked.connect(self._paste_input)
        if open_button is not None:
            open_button.clicked.connect(actions.open_file.trigger)
        if clear_button is not None:
            clear_button.clicked.connect(self._clear_input)

        options = self._output_pane.options_button
        copy_output = self._output_pane.copy_button
        if options is not None:
            options.clicked.connect(self._not_yet)
        if copy_output is not None:
            copy_output.clicked.connect(self._copy_output)

        rail = self._splitter.rail
        if rail is not None:
            rail.swap_button.clicked.connect(self._not_yet)
            rail.layout_button.clicked.connect(self._not_yet)

        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._on_focus_changed)

    def _bind_pane(self, pane: PaneWidget) -> None:
        pane.tab_strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pane.tab_strip.customContextMenuRequested.connect(self._show_tab_menu)
        pane.new_tab_button.clicked.connect(self._actions.new_tab.trigger)
        pane.close_tab_button.clicked.connect(self._actions.close_tab.trigger)
        pane.pop_out_button.clicked.connect(self._actions.pop_out.trigger)
        viewport = pane.editor.viewport()
        viewport.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        viewport.customContextMenuRequested.connect(self._show_editor_menu)

    def _show_tab_menu(self, pos: QPoint) -> None:
        source = self.sender()
        if isinstance(source, QWidget):
            self._tab_menu.popup(source.mapToGlobal(pos))

    def _show_editor_menu(self, pos: QPoint) -> None:
        source = self.sender()
        if isinstance(source, QWidget):
            self._editor_menu.popup(source.mapToGlobal(pos))

    def _not_yet(self) -> None:
        self.statusBar().showMessage(NOT_YET_MESSAGE, _NOT_YET_MS)

    def _format(self) -> None:
        if self._formatting:
            return
        text = self._input_pane.editor.toPlainText()
        if text == "":
            return
        self._formatting = True
        self._format_started_s = _monotonic()
        self._pending_write = None
        self._reveal_timer.stop()
        self._output_before_format = self._output_pane.editor.toPlainText()
        self._format_source = self._input_pane.format_name()
        self._format_target = self._output_pane.format_name()
        self._write_progress.hide()
        self._format_spinner.hide()
        self._cancel_button.hide()
        self._sync_format_button()
        self._apply_deferred_reveal()
        self._session.start()
        self._session.submit(text, self._format_source, self._format_target)

    def _cancel_format(self) -> None:
        """Restore the previous output, then kill the worker."""
        if not self._formatting:
            return
        self._output_pane.editor.setPlainText(self._output_before_format)
        self._status_message.setText(CANCELLED_MESSAGE)
        self._write_progress.hide()
        self._format_spinner.hide()
        self._cancel_button.hide()
        self._formatting = False
        self._pending_write = None
        self._reveal_timer.stop()
        self._sync_format_button()
        self._session.cancel()

    def _on_write_progress(self, written: int, total: int) -> None:
        if not self._formatting:
            return
        self._pending_write = (written, total)
        self._apply_deferred_reveal()

    def _apply_deferred_reveal(self) -> None:
        """Show the spinner status and the bar once their placeholders elapse."""
        if not self._formatting:
            return
        spinner_due = _reveal_due(SPINNER_REVEAL_MS, self._format_started_s)
        write_due = self._pending_write is not None and _reveal_due(
            PROGRESS_BAR_REVEAL_MS, self._format_started_s
        )
        if write_due and self._pending_write is not None:
            written, total = self._pending_write
            maximum = total if total > 0 else 1
            self._write_progress.setMaximum(maximum)
            self._write_progress.setValue(min(written, maximum))
            self._write_progress.show()
            self._format_spinner.hide()
            self._cancel_button.show()
            self._status_message.setText(WRITING_MESSAGE)
        elif spinner_due:
            self._format_spinner.show()
            self._cancel_button.show()
            if not self._write_progress.isVisible():
                self._status_message.setText(FORMATTING_MESSAGE)
        self._arm_reveal_timer()

    def _arm_reveal_timer(self) -> None:
        if not self._formatting or self._reveal_timer.isActive():
            return
        elapsed = _elapsed_ms(self._format_started_s)
        remaining: list[float] = []
        if not _reveal_due(SPINNER_REVEAL_MS, self._format_started_s):
            remaining.append(SPINNER_REVEAL_MS - elapsed)
        if self._pending_write is not None and not _reveal_due(
            PROGRESS_BAR_REVEAL_MS, self._format_started_s
        ):
            remaining.append(PROGRESS_BAR_REVEAL_MS - elapsed)
        if not remaining:
            return
        self._reveal_timer.start(max(0, int(min(remaining))))

    def _on_format_succeeded(self, text: str) -> None:
        if not self._formatting:
            return
        self._output_pane.editor.setPlainText(text)
        self._finish_format(self._success_status())

    def _on_format_failed(self, message: str) -> None:
        if not self._formatting:
            return
        self._finish_format(message)

    def _finish_format(self, status: str) -> None:
        self._formatting = False
        self._pending_write = None
        self._reveal_timer.stop()
        self._status_message.setText(status)
        self._write_progress.hide()
        self._format_spinner.hide()
        self._cancel_button.hide()
        self._sync_format_button()

    def _success_status(self) -> str:
        source = self._format_source.casefold()
        target = self._format_target.casefold()
        if source != target:
            return "Converted"
        if target == "yaml":
            return "Valid YAML"
        return "Valid JSON"

    def _focus_input(self) -> None:
        self._input_pane.editor.setFocus()
        self._active_editor = self._input_pane.editor
        self._update_caption()
        self._update_focus_ring()

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        editor = _editor_from(new)
        if editor is not None:
            self._active_editor = editor
            self._update_caption()
        self._update_focus_ring()

    def _update_caption(self) -> None:
        if self._active_editor is self._output_pane.editor:
            name = "Output 1"
        else:
            name = "Input 1"
        self._editing.setText(f"Editing <b>{name}</b>")

    def _update_focus_ring(self) -> None:
        focused = QApplication.focusWidget()
        self._input_pane.set_keyboard_focus(_is_inside(focused, self._input_pane))
        self._output_pane.set_keyboard_focus(_is_inside(focused, self._output_pane))

    def _target_editor(self) -> CodeEditor:
        focused = _editor_from(QApplication.focusWidget())
        if focused is not None:
            return focused
        return self._active_editor

    def _editors(self) -> tuple[CodeEditor, CodeEditor]:
        return self._input_pane.editor, self._output_pane.editor

    def _undo(self) -> None:
        self._target_editor().undo()

    def _redo(self) -> None:
        self._target_editor().redo()

    def _cut(self) -> None:
        self._target_editor().cut()

    def _copy(self) -> None:
        self._target_editor().copy()

    def _paste(self) -> None:
        self._target_editor().paste()

    def _paste_input(self) -> None:
        self._input_pane.editor.paste()
        self._input_pane.editor.setFocus()

    def _select_all(self) -> None:
        self._target_editor().selectAll()

    def _clear_input(self) -> None:
        self._input_pane.editor.clear()
        self._input_pane.editor.setFocus()

    def _copy_output(self) -> None:
        QApplication.clipboard().setText(self._output_pane.editor.toPlainText())

    def _set_word_wrap(self, checked: bool) -> None:
        mode = (
            CodeEditor.LineWrapMode.WidgetWidth
            if checked
            else CodeEditor.LineWrapMode.NoWrap
        )
        for editor in self._editors():
            editor.setLineWrapMode(mode)

    def _set_show_whitespace(self, checked: bool) -> None:
        flag = QTextOption.Flag.ShowTabsAndSpaces
        for editor in self._editors():
            option = editor.document().defaultTextOption()
            flags = option.flags()
            option.setFlags(flags | flag if checked else flags & ~flag)
            editor.document().setDefaultTextOption(option)

    def _zoom_in(self) -> None:
        self._zoom(1)

    def _zoom_out(self) -> None:
        self._zoom(-1)

    def _zoom(self, delta: int) -> None:
        for editor in self._editors():
            font = editor.font()
            size = font.pointSize()
            if size < 0:
                size = CODE_POINT_SIZE
            font.setPointSize(max(8, min(48, size + delta)))
            editor.setFont(font)
            editor.refresh_gutter()

    def _actual_size(self) -> None:
        for editor in self._editors():
            font = editor.font()
            font.setPointSize(CODE_POINT_SIZE)
            editor.setFont(font)
            editor.refresh_gutter()

    def _set_full_screen(self, checked: bool) -> None:
        if checked == self.isFullScreen():
            return
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    def _on_format_action_enabled(self, _enabled: bool) -> None:
        self._sync_format_button()

    def _sync_format_button(self) -> None:
        """Keep Format disabled for the whole job without changing its label."""
        self._format_button.setEnabled(
            self._actions.format.isEnabled() and not self._formatting
        )

    def _sync_input_state(self) -> None:
        editor = self._input_pane.editor
        text = editor.toPlainText()
        empty = text == ""
        self._actions.format.setEnabled(not empty)
        clear_button = self._input_pane.clear_button
        if clear_button is not None:
            clear_button.setEnabled(not empty)
        self._input_pane.set_empty_visible(empty)
        if empty:
            self._input_pane.count_label.setText("Empty")
            return
        self._input_pane.count_label.setText(
            f"{_line_label(editor.blockCount())} · {len(text)} characters"
        )

    def _sync_output_state(self) -> None:
        editor = self._output_pane.editor
        text = editor.toPlainText()
        empty = text == ""
        copy_button = self._output_pane.copy_button
        if copy_button is not None:
            copy_button.setEnabled(not empty)
        self._output_pane.set_empty_visible(empty)
        if empty:
            self._output_pane.count_label.setText("")
            return
        self._output_pane.count_label.setText(_line_label(editor.blockCount()))

    def _sync_direction(self) -> None:
        left = self._input_pane.format_name()
        right = self._output_pane.format_name()
        self._direction.setText(f"{left} → {right}")


def _add_menu(bar: QMenuBar, title: str) -> QMenu:
    menu = bar.addMenu(title)
    menu.setObjectName("menu" + title.replace(" ", ""))
    return menu


def _tool_button(action: QAction) -> QToolButton:
    button = QToolButton()
    button.setDefaultAction(action)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setAutoRaise(True)
    return button


def _monotonic() -> float:
    return time.monotonic()


def _elapsed_ms(started_s: float) -> float:
    return (_monotonic() - started_s) * 1000


def _reveal_due(delay_ms: int, started_s: float) -> bool:
    if delay_ms <= 0:
        return True
    return _elapsed_ms(started_s) >= delay_ms


def _editor_from(widget: QWidget | None) -> CodeEditor | None:
    while widget is not None:
        if isinstance(widget, CodeEditor):
            return widget
        widget = widget.parentWidget()
    return None


def _is_inside(widget: QWidget | None, ancestor: QWidget) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.parentWidget()
    return False


def _line_label(count: int) -> str:
    word = "line" if count == 1 else "lines"
    return f"{count} {word}"
