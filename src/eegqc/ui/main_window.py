from dataclasses import asdict, replace
from pathlib import Path
import re
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from eegqc.adapters.eeglab import (
    channel_spectrum,
    component_properties,
    component_thumbnail,
    channel_location_thumbnail,
    signal_topography,
    load_set,
)
from eegqc.application.persistence import export_labels, restore_session, save_session
from eegqc.application.session import Session
from eegqc.demo import make_demo
from eegqc.ui.epochrank import EpochAssistance
from eegqc.ui.epoch_rail import EpochRail
from eegqc.ui.properties import SignalProperties
from eegqc.ui.jobs import Jobs
from eegqc.ui.long_tasks import LongTasks
from eegqc.ui.theme import STYLE
from eegqc.ui.waveform import Waveform
from eegqc.ui.workflows import WorkflowActions


def button(text, function, primary=False):
    widget = QPushButton(text)
    widget.setProperty("primary", primary)
    widget.clicked.connect(function)
    return widget


def plot(title):
    widget = pg.PlotWidget(background="#fafbfd", title=title)
    for name in ("bottom", "left"):
        widget.getAxis(name).setTextPen("#536170")
    widget.showGrid(x=True, y=True, alpha=0.12)
    return widget


class MainWindow(QMainWindow, WorkflowActions):
    def __init__(self):
        super().__init__()
        pg.setConfigOptions(background="#fafbfd", foreground="#536170")
        self.setWindowTitle("EEGqc · EEG inspection and quality control")
        self.resize(1500, 1000)
        self.setStyleSheet(STYLE)
        self.session = None
        self.queue: list[Path] = []
        self.current_epoch = 0
        self.first_epoch = 0
        self.continuous_start = 0
        self.session_path = None
        self.topos = {}
        self.view_revision = 0
        self.rendered_state = None
        self.rendered_view_revision = -1
        self._navigator_review = None
        self.property_request = 0
        self.ic_property_request = 0
        self.jobs = Jobs(self)
        self.jobs.failed.connect(self._job_error)
        self.property_jobs = Jobs(self)
        self.property_jobs.failed.connect(self._job_error)
        self.network_jobs = Jobs(self)
        self.network_jobs.failed.connect(self._job_error)
        self.long_tasks = LongTasks(self)
        self.long_tasks.failed.connect(self._job_error)
        self.long_tasks.changed.connect(self._long_task_changed)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._request_render)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._autosave)
        self._menus()
        self._layout()
        self.configure_workflows()
        self.epochrank.add_track()
        self._action(self.epoch_actions, "Add assistance indicator", lambda: self.epochrank.add_track())
        self.task_label = QLabel()
        self.task_cancel = button("Cancel task", self.long_tasks.cancel)
        self.task_result = button("Inspect completed preview", self._show_task_candidate)
        self.task_result.hide()
        self.pending_task_candidate = None
        self.task_cancel.hide()
        self.statusBar().addPermanentWidget(self.task_label)
        self.statusBar().addPermanentWidget(self.task_result)
        self.statusBar().addPermanentWidget(self.task_cancel)
        self.default_layout = self.saveState()
        self._action(self.views_menu, "Save layout", self.save_layout)
        self._action(self.views_menu, "Load saved layout", self.load_layout)
        self._action(
            self.views_menu, "Restore default layout", lambda: self.restoreState(self.default_layout)
        )
        self.statusBar().showMessage("Open .set file, or load synthetic data from the File menu")

    def _action(self, menu, text, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    def _menus(self):
        files = self.menuBar().addMenu("File")
        self.file_menu = files
        self._action(files, "Open .set…", self.open_dialog, "Ctrl+O")
        self._action(files, "Search dataset folder…", self.search_directory)
        self._action(files, "Synthetic demo", lambda: self.open_demo())
        files.addSeparator()
        self._action(files, "Save QC records…", self.save, "Ctrl+S")
        self._action(files, "Restore QC records…", self.restore)
        self._action(files, "Export cleaned .set…", self.export)
        self._action(files, "Export reviewed labels CSV…", self.labels_export)
        edit = self.menuBar().addMenu("Edit")
        self._action(edit, "Undo", lambda: self._history(False), "Ctrl+Z")
        self._action(edit, "Redo", lambda: self._history(True), "Ctrl+Shift+Z")
        self._action(edit, "Toggle epoch rejection", self.toggle_current)
        self._action(edit, "Previous page", lambda: self.page(-1))
        self._action(edit, "Next page", lambda: self.page(1))
        self.views_menu = self.menuBar().addMenu("View")
        algorithms = self.menuBar().addMenu("Algorithms")
        self.algorithm_menu = algorithms
        self._action(self.views_menu, "Freeze the applied state as reference", self.freeze_reference)
        self._action(self.views_menu, "Restore the reference (undoable)", self.restore_reference)
        help_menu = self.menuBar().addMenu("Help")
        self._action(
            help_menu,
            "Controls",
            lambda: QMessageBox.information(
                self,
                "EEGqc",
                "B: toggle rejection; arrow keys: page; Ctrl+Z: undo.\n"
                "IC or interpolation changes create a preview. Apply it before labeling.\n"
                "QC records are saved as JSON; Export cleaned data via File → Export .set.\n"
                "Synthetic data is for demonstration only.",
            ),
        )

    def _layout(self):
        self._navigation()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 10, 18, 12)
        layout.setSpacing(7)
        self.context = QLabel("No dataset loaded")
        self.context.setObjectName("context")
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        tools = QHBoxLayout()
        tools.addWidget(
            button("Datasets", lambda: self.navigation.setVisible(not self.navigation.isVisible()))
        )
        self.channel_tool = button("Channels…", lambda: self.set_workspace(1))
        self.ic_tool = button("ICs…", lambda: self.set_workspace(2))
        for item in (self.channel_tool, self.ic_tool):
            item.setEnabled(False)
            tools.addWidget(item)
        hint = QLabel("Click channel names for properties · Right-click / drag for scalp maps")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        tools.addWidget(hint, 1)
        layout.addLayout(tools)
        self.processing_notice = QWidget()
        notice = QHBoxLayout(self.processing_notice)
        notice.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        notice.addWidget(self.preview_label, 1)
        notice.addWidget(button("Apply preview", self.adopt, True))
        notice.addWidget(button("Reset", self.reset_preview))
        self.processing_notice.hide()
        layout.addWidget(self.processing_notice)
        self.epoch_view = self._epoch_page()
        layout.addWidget(self.epoch_view, 1)
        self.channel_properties = SignalProperties()
        self.ic_properties = SignalProperties()
        self.bad_channel = QCheckBox("Bad channel · interpolate in live preview")
        self.bad_channel.toggled.connect(self._mark_property_channel)
        self.channel_properties.layout().insertWidget(1, self.bad_channel)
        self.property_label = self.channel_properties.heading
        self.channel_location = self.channel_properties.map
        self.channel_dialog = self._property_dialog(
            "Channels · live interpolation preview", self._channel_page(), self.channel_properties
        )
        self.ic_dialog = self._property_dialog(
            "ICs · live removal preview", self._ic_page(), self.ic_properties
        )
        self.review_dialog = QDialog(self)
        self.review_dialog.setWindowTitle("QC summary")
        self.review_dialog.resize(600, 600)
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        QVBoxLayout(self.review_dialog).addWidget(self.review_text)
        self.topography_dialog = QDialog(self)
        self.topography_dialog.setWindowTitle("Scalp topography")
        topo_layout = QVBoxLayout(self.topography_dialog)
        self.topography_caption = QLabel()
        self.topography_caption.setWordWrap(True)
        topo_layout.addWidget(self.topography_caption)
        self.topography_image = QLabel()
        self.topography_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        topo_layout.addWidget(self.topography_image)
        self.topography_selection = None
        root.setObjectName("qcBody")
        root.setStyleSheet("QWidget#qcBody { background: #ffffff; }")
        self.setCentralWidget(root)
        self._inspector()
        self._erp_dock()

    def _navigation(self):
        dock = QDockWidget("Datasets / batch", self)
        self.navigation = dock
        dock.setObjectName("navigation")
        dock.setTitleBarWidget(QWidget())
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        body = QWidget()
        body.setObjectName("navigationBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 20, 12, 16)
        layout.setSpacing(10)
        layout.addWidget(button("Open dataset…", self.open_dialog))
        layout.addWidget(button("Search dataset folder…", self.search_directory))
        layout.addWidget(button("Save QC records", self.save, True))
        layout.addWidget(button("Restore QC records…", self.restore))
        layout.addWidget(button("Export cleaned .set…", self.export))
        layout.addWidget(button("QC summary…", lambda: self.set_workspace(3)))
        layout.addWidget(button("Hide sidebar ‹", dock.hide))
        label = QLabel("Dataset / batch")
        label.setObjectName("sectionLabel")
        layout.addWidget(label)
        self.dataset_list = QListWidget()
        self.dataset_list.setObjectName("datasetList")
        self.dataset_list.setWordWrap(True)
        self.dataset_list.itemClicked.connect(self._choose_dataset)
        layout.addWidget(self.dataset_list, 1)
        row = QHBoxLayout()
        row.addWidget(button("‹ Previous", lambda: self.next_dataset(-1)))
        row.addWidget(button("Next ›", lambda: self.next_dataset(1)))
        layout.addLayout(row)
        note = QLabel("Decisions are linked to the signal state\nCtrl+Z Undo · Ctrl+S Save")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        dock.setWidget(body)
        dock.setFixedWidth(190)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.views_menu.addAction(dock.toggleViewAction())

    def _choose_dataset(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and (self.session is None or Path(path) != self.session.dataset.source):
            self.open_path(path)

    def _sync_dataset_list(self):
        ds = self.session.dataset
        self.dataset_list.clear()
        paths = list(self.queue)
        if ds.source and ds.source not in paths:
            paths.append(ds.source)
        for path in paths:
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.dataset_list.addItem(item)
            if path == ds.source:
                self.dataset_list.setCurrentItem(item)
        if ds.source is None:
            self.dataset_list.addItem("Synthetic demo")
            self.dataset_list.setCurrentRow(self.dataset_list.count() - 1)

    def _epoch_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.batch_mark = button("Mark selected epochs", lambda: self.mark_selected(True))
        self.batch_keep = button("Confirm as good", lambda: self.mark_selected(False))
        self.amplitude = QComboBox()
        self.amplitude.addItems(["25 μV", "50 μV", "100 μV", "200 μV"])
        self.amplitude.setCurrentIndex(1)
        self.amplitude.currentIndexChanged.connect(self.request_render)
        toolbar.addWidget(QLabel("Scale"))
        toolbar.addWidget(self.amplitude)
        self.channel_count = QComboBox()
        self.channel_count.addItems(["16 channels", "32 channels", "64 channels"])
        self.channel_count.currentIndexChanged.connect(self.request_render)
        toolbar.addWidget(self.channel_count)
        self.channel_start = QSpinBox()
        self.channel_start.setPrefix("First channel ")
        self.channel_start.setMinimum(1)
        self.channel_start.valueChanged.connect(self.request_render)
        toolbar.addWidget(self.channel_start)
        toolbar.addStretch()
        self.compare = QCheckBox("Overlay reference")
        self.compare.toggled.connect(self.request_render)
        toolbar.addWidget(self.compare)
        toolbar.addWidget(button("Add assistance", lambda: self.epochrank.add_track()))
        layout.addLayout(toolbar)
        self.waveform = Waveform()
        self.waveform.epoch_selected.connect(self._click_epoch)
        self.waveform.range_selected.connect(self.select_interval)
        self.waveform.channel_selected.connect(self.show_channel_property)
        self.waveform.topography_requested.connect(self.show_topography)
        layout.addWidget(self.waveform, 1)
        overview = QHBoxLayout()
        title = QLabel("Epoch overview")
        title.setObjectName("sectionLabel")
        overview.addWidget(title)
        legend = QLabel("Gray: unreviewed · Green: kept · Red: rejected · Amber: review again")
        legend.setObjectName("muted")
        overview.addWidget(legend)
        overview.addStretch()
        self.batch_mark.hide()
        self.batch_keep.hide()
        layout.addLayout(overview)
        row = QGridLayout()
        row.addWidget(button("‹", lambda: self.page(-1)), 0, 0)
        self.navigator = EpochRail()
        self.navigator.itemClicked.connect(lambda item: self.select_epoch(self.navigator.row(item)))
        self.navigator.itemSelectionChanged.connect(self._selection_summary)
        for target in (self.waveform, self.navigator):
            for sequence, callback in (
                ("B", self.toggle_current),
                ("Left", lambda: self.page(-1)),
                ("Right", lambda: self.page(1)),
            ):
                if target is self.navigator and sequence in ("Left", "Right"):
                    continue
                shortcut = QShortcut(QKeySequence(sequence), target)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(callback)
        row.addWidget(self.navigator, 0, 1)
        self.time_window_label = QLabel()
        row.addWidget(self.time_window_label, 0, 1)
        self.time_window_label.hide()
        row.addWidget(button("›", lambda: self.page(1)), 0, 2)
        self.epochrank = EpochAssistance(self)
        row.addWidget(self.epochrank, 1, 1)
        self.navigator.setViewportMargins(
            0, 0, self.epochrank.scroll.verticalScrollBar().sizeHint().width(), 0
        )
        row.setColumnStretch(1, 1)
        layout.addLayout(row)
        self.epoch_summary = QLabel("Click waveform to toggle rejection · Ctrl+Z undo")
        self.epoch_summary.setObjectName("muted")
        layout.addWidget(self.epoch_summary)
        return page

    def _channel_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(
            "Select a channel to inspect its scalp location, spectrum and trial activity on the right.\n"
            "Mark bad channels for interpolation. Changes update the main waveform immediately."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.channel_table = QTableWidget(0, 3)
        self.channel_table.setHorizontalHeaderLabels(["Channel", "Bad · interpolate", "Exclude from export"])
        self.channel_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.channel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.channel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.channel_table.itemChanged.connect(self._channel_changed)
        self.channel_table.currentCellChanged.connect(lambda *_: self._request_property())
        layout.addWidget(self.channel_table)
        return page

    def _ic_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        hint = QLabel("Click a map to exclude / restore. Red = excluded. Right-click for properties only.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row.addStretch()
        row.addWidget(button("Reset ICs", self.clear_ics))
        row.addWidget(button("ICLabel…", self.run_iclabel))
        row.addWidget(button("Fit new ICA…", self.run_ica))
        self.ic_context = QLabel()
        self.ic_context.setWordWrap(True)
        layout.addWidget(self.ic_context)
        self.ic_page = QSpinBox()
        self.ic_page.setPrefix("Page ")
        self.ic_page.setMinimum(1)
        self.ic_page.valueChanged.connect(self._populate_ics)
        row.addWidget(self.ic_page)
        layout.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.ic_container = QWidget()
        self.ic_container.setObjectName("icContainer")
        self.ic_container.setStyleSheet("QWidget#icContainer { background: #f3f6fa; }")
        self.ic_grid = QGridLayout(self.ic_container)
        self.ic_grid.setSpacing(16)
        scroll.setWidget(self.ic_container)
        layout.addWidget(scroll, 1)
        self.ic_widgets = []
        return page

    def _inspector(self):
        dock = QDockWidget("Properties / decisions", self)
        dock.setObjectName("inspector")
        self.inspector = dock
        body = QWidget()
        body.setObjectName("inspectorBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        self.current_label = QLabel("Current Epoch")
        self.current_label.setObjectName("heading")
        layout.addWidget(self.current_label)
        self.current_status = QLabel("Unreviewed")
        layout.addWidget(self.current_status)
        self.reason = QComboBox()
        self.reason.addItem("Uncertain / review later", "uncertain")
        for label, value in (
            ("Unspecified artifact", "signal_artifact"),
            ("Blink / eye movement", "eye"),
            ("Muscle", "muscle"),
            ("Electrode / motion", "movement/electrode"),
            ("Line noise", "line_noise"),
            ("Drift", "drift"),
            ("Flat / clipping", "flat/clipping"),
            ("Behavioral exclusion", "behavioral"),
            ("Condition selection", "condition_selection"),
        ):
            self.reason.addItem(label, value)
        self.reason.setCurrentIndex(self.reason.findData("signal_artifact"))
        layout.addWidget(QLabel("Decision reason"))
        layout.addWidget(self.reason)
        self.epoch_note = QPlainTextEdit()
        self.epoch_note.setPlaceholderText("Current epoch decision notes…")
        self.epoch_note.setMaximumHeight(80)
        layout.addWidget(self.epoch_note)
        self.mark_button = button("Reject", lambda: self.mark_selected(True))
        self.mark_button.setObjectName("rejectButton")
        layout.addWidget(self.mark_button)
        self.mark_button.hide()
        self.keep_button = button("Confirm as good", lambda: self.mark_selected(False))
        self.keep_button.setProperty("primary", True)
        layout.addWidget(self.keep_button)
        hint = QLabel("Click an epoch waveform to reject it; click again to keep it.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        layout.addWidget(QLabel("Dataset rating"))
        ratings = QHBoxLayout()
        self.ratings = QButtonGroup(self)
        for label, value in (("Usable", "可用"), ("Needs review", "待复核"), ("Unusable", "不可用")):
            radio = QRadioButton(label)
            radio.setProperty("rating", value)
            self.ratings.addButton(radio)
            ratings.addWidget(radio)
            radio.toggled.connect(lambda checked: self._rating_changed() if checked else None)
        layout.addLayout(ratings)
        self.dataset_note = QLineEdit()
        self.dataset_note.setPlaceholderText("Dataset notes…")
        self.dataset_note.editingFinished.connect(self._rating_changed)
        layout.addWidget(self.dataset_note)
        layout.addWidget(button("Save / next dataset →", self.save_next, True))
        tabs = QTabWidget()
        self.inspector_tabs = tabs
        tabs.addTab(body, "Epoch details")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        self.processing_label = QLabel("Applied signal: input data")
        self.processing_label.setWordWrap(True)
        layout.addWidget(self.processing_label)
        self.adopt_button = button("Apply preview & continue QC", self.adopt, True)
        layout.addWidget(self.adopt_button)
        layout.addWidget(button("Reset preview", self.reset_preview))
        self.average_ref = QCheckBox("Average-reference preview")
        self.average_ref.toggled.connect(self._reference_changed)
        layout.addWidget(self.average_ref)
        self.erp_toggle_button = button(
            "Show / hide conditional ERP", lambda: self.erp_dock.setVisible(not self.erp_dock.isVisible())
        )
        layout.addWidget(self.erp_toggle_button)
        layout.addWidget(button("Freeze applied state as reference", self.freeze_reference))
        layout.addWidget(button("Restore reference as applied state", self.restore_reference))
        layout.addWidget(QLabel("History"))
        self.history_label = QLabel("No changes yet")
        self.history_label.setWordWrap(True)
        self.history_label.setObjectName("muted")
        layout.addWidget(self.history_label)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        tabs.addTab(scroll, "Processing")
        dock.setWidget(tabs)
        dock.setMinimumWidth(320)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.views_menu.addAction(dock.toggleViewAction())

    def _erp_dock(self):
        self.erp_dock = QDockWidget("Condition ERP · Applied vs. frozen reference", self)
        self.erp_dock.setObjectName("erp")
        body = QWidget()
        layout = QVBoxLayout(body)
        toolbar = QHBoxLayout()
        self.condition_field = QComboBox()
        self.condition_field.currentIndexChanged.connect(self._condition_values)
        self.condition_value = QComboBox()
        self.condition_value.currentIndexChanged.connect(self.request_render)
        self.erp_channel = QComboBox()
        self.erp_channel.currentIndexChanged.connect(self.request_render)
        self.erp_baseline = QCheckBox("Baseline: epoch start to 0 s")
        self.erp_baseline.toggled.connect(self.request_render)
        self.erp_comparison = QComboBox()
        self.erp_comparison.addItems(
            [
                "Overall QC before / after",
                "Compare processing only (same trials)",
                "Compare only epoch rejection effects",
            ]
        )
        self.erp_comparison.currentIndexChanged.connect(self.request_render)
        self.split_conditions = QCheckBox("Overlay conditions")
        self.split_conditions.toggled.connect(self.request_render)
        self.use_custom_conditions = QCheckBox("Use condition queries")
        self.use_custom_conditions.toggled.connect(self.request_render)
        for item in (
            QLabel("Condition"),
            self.condition_field,
            self.condition_value,
            QLabel("channels"),
            self.erp_channel,
            self.erp_baseline,
            self.split_conditions,
        ):
            toolbar.addWidget(item)
        layout.addLayout(toolbar)
        advanced = QHBoxLayout()
        advanced.addWidget(self.erp_comparison)
        advanced.addWidget(self.use_custom_conditions)
        advanced.addWidget(button("Edit condition queries…", self.edit_conditions))
        advanced.addStretch()
        layout.addLayout(advanced)
        self.erp_plot = plot("No epoched data loaded")
        self.erp_plot.setMinimumHeight(160)
        self.erp_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.erp_plot.setLabel("bottom", "Time relative to event", units="ms")
        self.erp_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.erp_plot.setLabel("left", "Amplitude", units="μV")
        self.erp_plot.getAxis("left").enableAutoSIPrefix(False)
        self.erp_legend = self.erp_plot.addLegend(offset=(12, 10), colCount=2, labelTextSize="9pt")
        self.erp_ref_curve = self.erp_plot.plot(pen=pg.mkPen("#929daa", width=1.5), name="Reference")
        self.erp_curve = self.erp_plot.plot(pen=pg.mkPen("#148d9f", width=2), name="Applied / preview")
        self.condition_curves = {}
        layout.addWidget(self.erp_plot)
        self.erp_dock.setWidget(body)
        self.erp_dock.visibilityChanged.connect(self.request_render)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.erp_dock)
        self.views_menu.addAction(self.erp_dock.toggleViewAction())
        self.erp_dock.hide()

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open EEG data", "", "EEGLAB (*.set)")
        if path:
            self.open_path(path)

    def save_layout(self):
        QSettings("EEGqc", "EEGqc").setValue("layout", self.saveState())
        self.statusBar().showMessage("Layout saved")

    def load_layout(self):
        saved = QSettings("EEGqc", "EEGqc").value("layout")
        if saved is not None:
            self.restoreState(saved)
        else:
            self.statusBar().showMessage("No saved layout")

    def open_demo(self):
        if not self._autosave():
            return
        self.jobs.invalidate()
        self.statusBar().showMessage("Generating synthetic data…")
        self.jobs.submit("load", make_demo, self.set_dataset)

    def open_path(self, path):
        if not self._autosave():
            return
        self.jobs.invalidate()
        self.statusBar().showMessage("Loading via EEGPrep Loading dataset…")
        self.jobs.submit("load", lambda: load_set(path), self.set_dataset)

    def set_dataset(self, ds):
        if not self._autosave():
            return
        self.jobs.invalidate()
        self.property_jobs.invalidate()
        self.long_tasks.invalidate()
        self.task_result.hide()
        self.pending_task_candidate = None
        self.session = Session(ds)
        self._sync_dataset_list()
        self._navigator_review = None
        self.current_epoch = self.first_epoch = self.continuous_start = 0
        self.topos.clear()
        self.interval_selection = None
        self.rendered_state = None
        self.session_path = ds.source.with_suffix(".eegqc.json") if ds.source else None
        self.navigator.setVisible(not ds.continuous)
        self.time_window_label.setVisible(ds.continuous)
        self.erp_toggle_button.setEnabled(not ds.continuous)
        if ds.continuous:
            self.erp_dock.hide()
        if self.session_path and self.session_path.exists():
            try:
                restore_session(self.session, self.session_path)
            except (ValueError, OSError, KeyError) as error:
                self.report_error(str(error))
                # An incompatible sidecar must not be overwritten by autosave.
                self.session_path = None
        self.channel_start.setMaximum(ds.n_channels)
        self.channel_start.setValue(1)
        self.navigator.clear()
        for i in range(ds.n_epochs):
            self.navigator.addItem(QListWidgetItem(f"{i + 1:03d}"))
        self.erp_channel.blockSignals(True)
        self.erp_channel.clear()
        self.erp_channel.addItems(ds.labels)
        self.erp_channel.setCurrentIndex(ds.labels.index("Pz") if "Pz" in ds.labels else 0)
        self.erp_channel.blockSignals(False)
        self.condition_field.blockSignals(True)
        self.condition_field.clear()
        self.condition_field.addItem("All", "")
        for field in ds.conditions:
            self.condition_field.addItem(field, field)
        self.condition_field.blockSignals(False)
        self._condition_values()
        self.use_custom_conditions.setChecked(bool(self.session.erp_conditions))
        self.erp_baseline.setEnabled(ds.tmin < 0)
        self.erp_baseline.setChecked(ds.tmin < 0)
        self.ic_page.setMaximum(max(1, ((ds.ica.count if ds.ica else 0) + 11) // 12))
        self.ic_page.setValue(1)
        self._populate_channel_table()
        self._populate_ics()
        self._sync_state()
        for dialog in (self.channel_dialog, self.ic_dialog, self.review_dialog, self.topography_dialog):
            dialog.hide()
        self.topography_selection = None
        self.set_workspace(0)

    def search_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose dataset folder")
        if not folder:
            return
        pattern, accepted = QInputDialog.getText(
            self, "Search datasets", "Filename regular expression", text=r".*\.set$"
        )
        if not accepted:
            return
        try:
            expression = re.compile(pattern)
            self.queue = sorted(p for p in Path(folder).rglob("*.set") if expression.search(p.name))
        except re.error as error:
            self.report_error(str(error))
            return
        if self.queue:
            self.open_path(self.queue[0])
        else:
            self.statusBar().showMessage("No matching datasets found")

    def next_dataset(self, direction=1):
        if self.session is None or not self.queue:
            return
        current = self.session.dataset.source
        index = self.queue.index(current) if current in self.queue else -1
        index += direction
        if 0 <= index < len(self.queue):
            self.open_path(self.queue[index])
        else:
            self.statusBar().showMessage("End of the dataset queue")

    def _property_dialog(self, title, page, properties):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1000, 680)
        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()
        row.addWidget(page, 1)
        row.addWidget(properties, 1)
        layout.addLayout(row, 1)
        controls = QHBoxLayout()
        note = QLabel("Changes update the main waveform. Closing keeps the preview; Apply commits it for QC.")
        note.setWordWrap(True)
        controls.addWidget(note, 1)
        controls.addWidget(button("Reset preview", self.reset_preview))
        controls.addWidget(button("Apply", self.adopt, True))
        controls.addWidget(button("Close", dialog.close))
        layout.addLayout(controls)
        return dialog

    def set_workspace(self, index):
        # Auxiliary windows never replace or resize the epoch view.
        if index == 0:
            self.waveform.setFocus()
            self.request_render()
            return
        if self.session is None or (index == 2 and self.session.ica_for() is None):
            return
        dialog = {1: self.channel_dialog, 2: self.ic_dialog, 3: self.review_dialog}[index]
        dialog.show()
        dialog.raise_()
        if index == 1:
            self._request_property()
        elif index == 2:
            self._populate_ics()
            self.show_ic_property(min(getattr(self, "active_ic", 0), self.session.ica_for().count - 1))
        else:
            self._update_review_text()

    def show_channel_property(self, channel):
        if self.session is None:
            return
        self.channel_table.setCurrentCell(channel, 0)
        self.set_workspace(1)

    def _mark_property_channel(self, checked):
        item = self.channel_table.item(max(0, self.channel_table.currentRow()), 1)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def show_topography(self, epoch, start, stop):
        if self.session is None:
            return
        if (
            self.rendered_state != (self.session.dataset.data_id, self.session.candidate)
            or self.rendered_view_revision != self.view_revision
        ):
            self.statusBar().showMessage("Wait for the waveform preview to finish updating.")
            return
        self.topography_selection = (epoch, start, stop)
        self.topography_dialog.show()
        self.topography_dialog.raise_()
        self._request_topography()

    def _request_topography(self):
        if not self.topography_dialog.isVisible() or self.topography_selection is None:
            return
        s, ds = self.session, self.session.dataset
        epoch, start, stop = self.topography_selection
        processing, revision, setting = s.candidate, s.revision, self.display_filter
        channels = [
            i
            for i in range(ds.n_channels)
            if i not in processing.dropped
            and str(ds.chanlocs[i].get("type", "EEG")).upper() in ("", "EEG")
            and self._has_locations(ds, [i])
        ]
        self.topography_image.clear()
        if len(channels) < 3:
            self.topography_caption.setText(
                "Scalp maps require at least three retained EEG channels with electrode locations."
            )
            return
        t0 = (0 if ds.continuous else ds.tmin) + start / ds.sfreq
        t1 = (0 if ds.continuous else ds.tmin) + (stop - 1) / ds.sfreq
        interval = (
            f"{t0 * 1000:.0f} ms"
            if stop == start + 1
            else f"{t0 * 1000:.0f}–{t1 * 1000:.0f} ms · time average"
        )
        caption = ("Continuous data" if ds.continuous else f"Epoch {epoch + 1}") + " · " + interval
        caption += (
            "\n"
            + ("Preview: " if processing != s.review.processing else "Applied: ")
            + self._describe(processing)
        )
        caption += (
            f"\n{len(channels)} positioned EEG channels · displayed signal · ranges stay within one epoch"
        )
        self.topography_caption.setText(caption + "\nUpdating…")

        def compute():
            values = (
                s.engine.signal(
                    processing,
                    epochs=[epoch],
                    channels=channels,
                    samples=slice(start, stop),
                    display_filter=setting,
                )[0].mean(axis=1)
                * 1e6
            )
            return signal_topography(ds, channels, values)

        def display(rgba):
            if self.session is not s or s.revision != revision or self.display_filter != setting:
                return
            image = QImage(
                rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format.Format_RGBA8888
            ).copy()
            self.topography_image.setPixmap(QPixmap.fromImage(image))
            self.topography_caption.setText(caption)

        self.property_jobs.submit("signal-topography", compute, display)

    def _condition_values(self):
        self.condition_value.blockSignals(True)
        self.condition_value.clear()
        if self.session is not None:
            field = self.condition_field.currentData()
            if field:
                self.condition_value.addItems(sorted(set(self.session.dataset.conditions[field].tolist())))
        self.condition_value.blockSignals(False)
        self.request_render()

    def select_epoch(self, index):
        if self.session is None:
            return
        self.current_epoch = index
        self.first_epoch = index // 5 * 5
        self._sync_state()
        self.request_render()

    def select_interval(self, start, stop):
        if self.session and self.session.dataset.continuous:
            ds = self.session.dataset
            left = max(0, self.continuous_start + round(start * ds.sfreq))
            right = min(ds.data.shape[2], self.continuous_start + round(stop * ds.sfreq))
            self.interval_selection = (left, right) if left < right else None
            self._sync_state()

    def page(self, direction):
        if self.session is None:
            return
        ds = self.session.dataset
        if ds.continuous:
            self.continuous_start = int(
                np.clip(
                    self.continuous_start + direction * round(10 * ds.sfreq),
                    0,
                    max(0, ds.data.shape[2] - round(10 * ds.sfreq)),
                )
            )
            self.request_render()
        else:
            self.navigator.choose(int(np.clip(self.first_epoch + direction * 5, 0, ds.n_epochs - 1)))

    def mark_selected(self, rejected):
        if self.session is None:
            return
        if self.session.candidate != self.session.review.processing:
            self.statusBar().showMessage("Apply the preview before labeling this signal state")
            return
        if self.rendered_state != (self.session.dataset.data_id, self.session.review.processing):
            self.statusBar().showMessage("Updating the view. Wait for the applied signal before labeling.")
            return
        if not self.session.dataset.continuous:
            shown_first = self.session.view_context.get("first_epoch", -5)
            if not shown_first <= self.current_epoch < shown_first + 5:
                self.statusBar().showMessage("Switching to selected epoch, Wait for the waveform.")
                return
        if self.session.dataset.continuous:
            if self.session.view_context.get("continuous_start_sample") != self.continuous_start:
                self.statusBar().showMessage("Changing the time window. Wait for the waveform.")
                return
            if self.interval_selection is not None:
                self.session.mark_interval(
                    *self.interval_selection,
                    rejected=rejected,
                    reason=self.reason.currentData(),
                    note=self.epoch_note.toPlainText(),
                )
                self._changed()
            return
        epochs = [self.navigator.row(i) for i in self.navigator.selectedItems()] or [self.current_epoch]
        self.session.mark(epochs, rejected, self.reason.currentData(), self.epoch_note.toPlainText())
        self._changed()

    def _click_epoch(self, index):
        if self.session is None or self.session.dataset.continuous:
            return
        # Toggle only a waveform that belongs to the currently displayed processing state.
        if index != self.current_epoch:
            self.navigator.choose(index)
        else:
            self.navigator.clearSelection()
            self.navigator.item(index).setSelected(True)
        self.mark_selected(index not in self.session.review.rejected)

    def toggle_current(self):
        if self.session is not None:
            if self.session.dataset.continuous and self.interval_selection:
                start, stop = self.interval_selection
                overlaps = any(
                    d.start < stop and d.stop > start for d in self.session.review.active_intervals
                )
                self.mark_selected(not overlaps)
                return
            self.mark_selected(self.current_epoch not in self.session.review.rejected)

    def _history(self, redo):
        if self.session:
            self.session.redo() if redo else self.session.undo()
            self._populate_channel_table()
            self._populate_ics()
            self._changed()

    def _propose(self, processing):
        self.session.propose(processing)
        self._sync_state()
        if self.ic_dialog.isVisible() and self.session.ica_for():
            self.show_ic_property(min(getattr(self, "active_ic", 0), self.session.ica_for().count - 1))
        self.request_render()

    def adopt(self):
        if self.session is None:
            return
        try:
            self.session.adopt()
        except ValueError as error:
            self.report_error(str(error))
            return
        self._changed()

    def reset_preview(self):
        if self.session:
            self._propose(self.session.review.processing)
            self._populate_channel_table()
            self._populate_ics()

    def clear_ics(self):
        if self.session:
            self._propose(replace(self.session.candidate, excluded_ics=()))
            self._populate_ics()

    def _reference_changed(self, value):
        if self.session:
            self._propose(replace(self.session.candidate, average_reference=value))

    def freeze_reference(self):
        if self.session:
            self.session.freeze_reference()
            self._changed()

    def restore_reference(self):
        if self.session:
            self.session.restore_reference()
            self._populate_channel_table()
            self._populate_ics()
            self._changed()

    def _selection_summary(self):
        if self.session is None or self.session.dataset.continuous:
            return
        count = len(self.navigator.selectedItems()) or 1
        self.batch_mark.setText(f"Mark selected {count} epochs")
        self.batch_keep.setText(f"Confirm as good {count} epochs")

    def _rating_changed(self):
        if self.session is not None and self.ratings.checkedButton():
            self.session.set_rating(self.ratings.checkedButton().property("rating"), self.dataset_note.text())
            self._changed()

    def _changed(self):
        self._sync_state()
        if self.ic_dialog.isVisible() and self.session.ica_for():
            self.show_ic_property(min(getattr(self, "active_ic", 0), self.session.ica_for().count - 1))
        self.request_render()
        self.autosave_timer.start(700)

    def _describe(self, processing):
        ds = self.session.dataset
        parts = []
        if processing.asr:
            parts.append(
                f"ASR(cutoff={processing.asr.cutoff:g}, Input: {self._describe(processing.asr.parent)})"
            )
        if processing.decomposition_id:
            ica = self.session.ica_for(processing)
            parts.append(
                "ICA "
                + processing.decomposition_id[:8]
                + "(Fitted input: "
                + self._describe(ica.input_processing)
                + ")"
            )
        if processing.excluded_ics:
            parts.append("Exclude IC " + ", ".join(str(i + 1) for i in processing.excluded_ics))
        if processing.interpolated:
            parts.append("Interpolate " + ", ".join(ds.labels[i] for i in processing.interpolated))
        if processing.dropped:
            parts.append("Excluded channels " + ", ".join(ds.labels[i] for i in processing.dropped))
        if processing.average_reference:
            parts.append("Average reference")
        return "; ".join(parts) or "Input data"

    def _set_context_badge(self, setting):
        ds = self.session.dataset
        demo = " · Synthetic demo" if ds.source is None else ""
        extent = (
            f"Continuous {ds.data.shape[2] / ds.sfreq:.1f} s" if ds.continuous else f"{ds.n_epochs} epochs"
        )
        text = f"{ds.source.name if ds.source else 'EEGqc'}{demo}    |    {ds.n_channels} channels · {ds.sfreq:g} Hz · {extent}"
        if setting.get("low") or setting.get("high"):
            text += f"    |    Display filter {setting.get('low') or 0:g}–{setting.get('high') or ds.sfreq / 2:g} Hz"
        if self.context.text() != text:
            self.context.setText(text)

    def _sync_state(self):
        self.epochrank.refresh()
        if self.session is None:
            return
        s, ds = self.session, self.session.dataset
        decisions = dict(s.review.decisions)
        rejected = s.review.rejected
        self._set_context_badge(s.view_context.get("display_filter", {}))
        decision = decisions.get(self.current_epoch)
        stale_decision = decision is not None and decision.processing != s.review.processing
        self.current_label.setText(f"Epoch {self.current_epoch + 1:03d} / {ds.n_epochs}")
        if ds.continuous:
            self.current_label.setText("Continuous data · interval review")
        self.current_status.setText(
            "Earlier decision · review again"
            if stale_decision
            else "Rejected"
            if decision and decision.rejected
            else "Kept"
            if decision
            else "Unreviewed"
        )
        self.current_status.setStyleSheet(
            "color: #b84350" if decision and decision.rejected else "color: #48726d"
        )
        self.current_status.setToolTip(f"Original TrialID: {ds.trial_ids[self.current_epoch]}")
        if decision:
            self.reason.setCurrentIndex(max(0, self.reason.findData(decision.reason)))
            self.epoch_note.setPlainText(decision.note)
        else:
            self.epoch_note.clear()
        if ds.continuous:
            self.current_status.setToolTip("Interval marks use original sample coordinates")
            if self.interval_selection is None:
                self.current_status.setText("Drag the blue range to select an interval")
            else:
                left, right = self.interval_selection
                overlaps = [d for d in s.review.intervals if d.start < right and d.stop > left]
                active = [d for d in overlaps if d.processing == s.review.processing]
                self.current_label.setText(f"Time {left / ds.sfreq:.2f}–{right / ds.sfreq:.2f} s")
                self.current_status.setText(
                    "Marked for removal"
                    if active
                    else "Earlier interval decision · review again"
                    if overlaps
                    else "Selected · not yet marked"
                )
                if overlaps:
                    interval = (active or overlaps)[-1]
                    self.reason.setCurrentIndex(max(0, self.reason.findData(interval.reason)))
                    self.epoch_note.setPlainText(interval.note)
        pending = s.candidate != s.review.processing
        self.processing_notice.setVisible(pending)
        self.preview_label.setText("Live preview: " + self._describe(s.candidate))
        self.adopt_button.setEnabled(pending)
        self.asr_action.setEnabled(ds.continuous)
        self.iclabel_action.setEnabled(s.ica_for() is not None)
        self.epoch_actions.setEnabled(not ds.continuous)
        self.mark_button.setVisible(ds.continuous)
        self.mark_button.setEnabled(not pending)
        self.keep_button.setEnabled(not pending)
        self.mark_button.setText("Mark selected interval" if ds.continuous else "Reject")
        self.keep_button.setText("Clear overlapping interval marks" if ds.continuous else "Confirm as good")
        self.batch_mark.setText("Mark selected interval" if ds.continuous else "Mark selected epochs")
        self.batch_keep.setText("Clear overlapping marks" if ds.continuous else "Confirm as good")
        self.epoch_note.setPlaceholderText(
            "Notes for this interval…" if ds.continuous else "Current epoch decision notes…"
        )
        self.average_ref.blockSignals(True)
        self.average_ref.setChecked(s.candidate.average_reference)
        self.average_ref.blockSignals(False)
        self.processing_label.setText(
            "Input: imported signal\nApplied: "
            + self._describe(s.review.processing)
            + ("\nPreview: " + self._describe(s.candidate) if pending else "")
            + "\nFrozen reference: "
            + self._describe(s.reference.processing)
        )
        ica = s.ica_for()
        self.channel_tool.setEnabled(True)
        self.ic_tool.setEnabled(ica is not None)
        self.ic_tool.setToolTip(
            "Inspect component properties"
            if ica
            else "No ICA decomposition. Fit ICA from Algorithms > Processing if needed."
        )
        self.ic_context.setText(
            f"Decomposition {ica.decomposition_id[:8]} · "
            + ("Preview, not applied" if pending else "Applied")
            + " · properties and ICLabel use the fitted input"
            if ica
            else "This stage has no ICA; Continue basic QC or fit a new ICA."
        )
        n = len(rejected)
        self.epoch_summary.setText(
            f"Marked {n} / {ds.n_epochs} epochs · {100 * n / ds.n_epochs:.1f}%"
            "     Click waveform to toggle rejection · Ctrl+Z undo"
        )
        if ds.continuous:
            self.epoch_summary.setText(
                f"Continuous data · {len(s.review.intervals)} interval marks · drag the blue range to select an interval"
            )
        previous = self._navigator_review
        if previous is s.review:
            changed_epochs = ()
        elif previous is None or previous.processing != s.review.processing:
            changed_epochs = range(self.navigator.count())
        elif previous.decisions is s.review.decisions:
            changed_epochs = ()
        else:
            previous_decisions = dict(previous.decisions)
            changed_epochs = (
                i
                for i in previous_decisions.keys() | decisions.keys()
                if previous_decisions.get(i) is not decisions.get(i)
            )
        for i in changed_epochs:
            item = self.navigator.item(i)
            d = decisions.get(i)
            stale_item = d is not None and d.processing != s.review.processing
            state = (
                "Needs review"
                if stale_item
                else "Rejected"
                if i in rejected
                else "Kept"
                if d
                else "Unreviewed"
            )
            color = "#936b16" if stale_item else "#ad3547" if i in rejected else "#287464" if d else "#526174"
            if item.data(Qt.ItemDataRole.UserRole) != state:
                item.setData(Qt.ItemDataRole.UserRole, state)
                item.setForeground(QColor(color))
                item.setBackground(QColor("#fbe4e7") if i in rejected else QColor("#f1f5f8"))
                item.setToolTip(f"Epoch {i + 1} · {state}")
        self.navigator.set_position(self.current_epoch, self.first_epoch)
        self._navigator_review = s.review
        for radio in self.ratings.buttons():
            radio.blockSignals(True)
            radio.setChecked(radio.property("rating") == s.review.rating)
            radio.blockSignals(False)
        if not self.dataset_note.hasFocus():
            self.dataset_note.setText(s.review.note)
        stale = sum(d.processing != s.review.processing for _, d in s.review.decisions)
        self.history_label.setText(
            f"{len(s._undo)} undo steps · {len(s.review.decisions)} reviewed epochs"
            + (f"\n{stale} decisions belong to an earlier processing state and need review" if stale else "")
        )
        if ds.continuous:
            stale_intervals = sum(d.processing != s.review.processing for d in s.review.intervals)
            self.history_label.setText(
                f"{len(s._undo)} undo steps · {len(s.review.intervals)} interval marks"
                + (f"\n{stale_intervals} earlier interval decisions need review" if stale_intervals else "")
            )
        self._selection_summary()
        if self.review_dialog.isVisible():
            self._update_review_text()

    def _update_review_text(self):
        s, ds = self.session, self.session.dataset
        text = (
            "Applied state\n\n"
            + self._describe(s.review.processing)
            + f"\n\nEpoch Rejected: {len(s.review.rejected)} / {ds.n_epochs}\nReviewed: {len(s.review.decisions)}\n"
            f"\nRating: {next((r.text() for r in self.ratings.buttons() if r.property('rating') == s.review.rating), s.review.rating)}\nNotes: {s.review.note}\n\n"
            "Conditional ERP compares processing, epoch rejection, or overall QC.\n"
            "Save records to preserve decisions; File → Export .set to create cleaned data.\n"
            "Exporting interpolated, re-referenced or reduced-channel data clears incompatible ICA derived fields."
        )
        if self.review_text.toPlainText() != text:
            self.review_text.setPlainText(text)

    def request_render(self, *_):
        if self.session:
            self.view_revision += 1
            if not self.context.text().endswith("    |    Updating; previous curves retained"):
                self.context.setText(self.context.text() + "    |    Updating; previous curves retained")
            self.render_timer.start(15)

    def _request_render(self):
        if self.session is None:
            return
        s, ds = self.session, self.session.dataset
        revision, first = s.revision, self.first_epoch
        view_revision, continuous_start = self.view_revision, self.continuous_start
        current, reference, candidate = s.review, s.reference, s.candidate
        start = self.channel_start.value() - 1
        count = int(self.channel_count.currentText().split()[0])
        channels = [i for i in range(start, min(ds.n_channels, start + count)) if i not in candidate.dropped]
        if not channels:
            self.statusBar().showMessage("No retained channels in this range")
            return
        epochs = slice(first, min(first + 5, ds.n_epochs))
        samples = slice(None)
        if ds.continuous:
            epochs = slice(0, 1)
            samples = slice(continuous_start, continuous_start + round(10 * ds.sfreq))
        conditions = self.condition_queries()
        display_filter = self.display_filter
        erp_channel = max(0, self.erp_channel.currentIndex())
        baseline = (ds.tmin, 0.0) if self.erp_baseline.isChecked() else None
        mode = self.erp_comparison.currentIndex()
        show_erp = self.erp_dock.isVisible() and not ds.continuous
        compare = self.compare.isChecked()
        ica = s.ica_for(candidate)
        view_context = {
            "workspace": 0,
            "visible_channels": channels,
            "first_epoch": first,
            "continuous_start_sample": self.continuous_start,
            "display_filter": asdict(display_filter),
            "baseline": list(baseline) if baseline else None,
            "erp_visible": show_erp,
            "erp_conditions": {name: asdict(q) for name, q in conditions},
            "iclabel_seen": {k: s.iclabel[k] for k in ("algorithm", "backend", "decomposition_id")}
            if s.iclabel
            and s.iclabel.get("shown")
            and ica is not None
            and s.iclabel["decomposition_id"] == ica.decomposition_id
            else None,
        }
        t0 = time.perf_counter()

        def compute():
            values = s.engine.signal(candidate, epochs, channels, samples, display_filter=display_filter)
            ref = (
                s.engine.signal(
                    reference.processing, epochs, channels, samples, display_filter=display_filter
                )
                if compare
                else None
            )
            curves = None
            if show_erp:
                ref_proc = candidate if mode == 2 else reference.processing
                ref_rejected = current.rejected if mode == 1 else reference.rejected
                curves = []
                for name, group in conditions:
                    a = s.engine.erp(
                        candidate,
                        current.rejected,
                        condition=group,
                        channels=[erp_channel],
                        baseline=baseline,
                        display_filter=display_filter,
                    )
                    b = s.engine.erp(
                        ref_proc,
                        ref_rejected,
                        condition=group,
                        channels=[erp_channel],
                        baseline=baseline,
                        display_filter=display_filter,
                    )
                    curves.append((("query", name), a, b))
            return revision, values, ref, curves

        def display(result):
            if self.session is not s or s.revision != result[0] or self.view_revision != view_revision:
                return
            _, values, ref, erps = result
            self.waveform.display(
                ds,
                first,
                channels,
                values,
                ref,
                current.rejected,
                float(self.amplitude.currentText().split()[0]),
                continuous_start=continuous_start,
                intervals=current.active_intervals,
                interpolated=candidate.interpolated,
            )
            if ds.continuous:
                stop = min(continuous_start + values.shape[2], ds.data.shape[2])
                self.time_window_label.setText(
                    f"{continuous_start / ds.sfreq:.1f}–{stop / ds.sfreq:.1f} s / "
                    f"{ds.data.shape[2] / ds.sfreq:.1f} s    ·    ← → Change time window"
                )
            if erps:
                self._display_erps(erps, ds.labels[erp_channel])
            s.view_context = view_context
            self._set_context_badge(view_context["display_filter"])
            self.rendered_state = (ds.data_id, candidate)
            self.rendered_view_revision = view_revision
            self.statusBar().showMessage(
                f"View updated in {(time.perf_counter() - t0) * 1000:.0f} ms · "
                "Source file unchanged"
                + (" · Preview not applied" if candidate != current.processing else "")
            )

        self.jobs.submit("render", compute, display)
        if self.channel_dialog.isVisible():
            self._request_property()
        self._request_topography()

    def _display_erps(self, erps, channel):
        wanted = set()
        summaries = []
        if len(erps) == 1:
            for curve, label in ((self.erp_curve, "Applied / preview"), (self.erp_ref_curve, "Reference")):
                if self.erp_legend.getLabel(curve) is None:
                    self.erp_legend.addItem(curve, label)
            group, a, b = erps[0]
            self.erp_curve.setData(
                a.times * 1000, a.data[0] * 1e6
            ) if a.data is not None else self.erp_curve.setData([], [])
            self.erp_ref_curve.setData(
                b.times * 1000, b.data[0] * 1e6
            ) if b.data is not None else self.erp_ref_curve.setData([], [])
            summaries.append(f"{group[1] if group else 'All'} Current N={a.count} / Reference N={b.count}")
        else:
            self.erp_legend.removeItem(self.erp_curve)
            self.erp_legend.removeItem(self.erp_ref_curve)
            self.erp_curve.setData([], [])
            self.erp_ref_curve.setData([], [])
            colors = ["#148d9f", "#b06d42", "#7960a0", "#477f52", "#ad5268"]
            for i, (group, a, b) in enumerate(erps):
                for kind, result in (("Current", a), ("Reference", b)):
                    key = (group, kind)
                    wanted.add(key)
                    if key not in self.condition_curves:
                        pen = pg.mkPen(
                            colors[i % len(colors)],
                            width=1.7,
                            style=Qt.PenStyle.SolidLine if kind == "Current" else Qt.PenStyle.DashLine,
                        )
                        self.condition_curves[key] = self.erp_plot.plot(pen=pen, name=f"{group[1]} · {kind}")
                    curve = self.condition_curves[key]
                    curve.setData(
                        result.times * 1000, result.data[0] * 1e6
                    ) if result.data is not None else curve.setData([], [])
                summaries.append(f"{group[1]} N={a.count}/{b.count}")
        for key in list(self.condition_curves):
            if key not in wanted:
                self.erp_plot.removeItem(self.condition_curves.pop(key))
        baseline = "Baseline: epoch start to 0 s" if self.erp_baseline.isChecked() else "No baseline"
        self.erp_plot.setTitle(
            f"{channel} · {baseline} · {self.erp_comparison.currentText()}<br>" + "  |  ".join(summaries),
            color="#526271",
            size="10pt",
        )

    def _populate_channel_table(self):
        if self.session is None:
            return
        ds = self.session.dataset
        selected = max(0, self.channel_table.currentRow())
        self.channel_table.blockSignals(True)
        self.channel_table.setRowCount(ds.n_channels)
        for i, name in enumerate(ds.labels):
            self.channel_table.setItem(i, 0, QTableWidgetItem(name))
            for col, chosen in (
                (1, self.session.candidate.interpolated),
                (2, self.session.candidate.dropped),
            ):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if i in chosen else Qt.CheckState.Unchecked)
                self.channel_table.setItem(i, col, item)
        self._set_interpolation_availability()
        self.channel_table.setCurrentCell(min(selected, ds.n_channels - 1), 0)
        self.channel_table.blockSignals(False)

    @staticmethod
    def _has_locations(ds, channels):
        return all(
            np.isfinite(ds.chanlocs[i].get("theta", np.nan))
            and np.isfinite(ds.chanlocs[i].get("radius", np.nan))
            for i in channels
        )

    def _set_interpolation_availability(self):
        ds = self.session.dataset
        positioned = set()
        for i, loc in enumerate(ds.chanlocs):
            xyz = np.asarray([loc.get(key, np.nan) for key in ("X", "Y", "Z")], dtype=float)
            if (
                str(loc.get("type", "EEG")).upper() in ("", "EEG")
                and np.isfinite(xyz).all()
                and np.linalg.norm(xyz) > 0
            ):
                positioned.add(i)
        chosen = set(self.session.candidate.interpolated)
        blocked = self.channel_table.blockSignals(True)
        for i in range(ds.n_channels):
            item = self.channel_table.item(i, 1)
            enabled = i in positioned and (i in chosen or len(positioned - chosen - {i}) >= 3)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                if enabled
                else Qt.ItemFlag.NoItemFlags
            )
            item.setToolTip(
                ""
                if enabled
                else "Spherical interpolation requires this EEG valid channel XYZ and at least three other well-located EEG channels; waveform review remains available."
            )
        self.channel_table.blockSignals(blocked)

    def _channel_changed(self, item):
        if self.session is None or item.column() == 0:
            return
        values = []
        for col in (1, 2):
            values.append(
                tuple(
                    i
                    for i in range(self.channel_table.rowCount())
                    if self.channel_table.item(i, col).checkState() == Qt.CheckState.Checked
                )
            )
        self._propose(replace(self.session.candidate, interpolated=values[0], dropped=values[1]))
        self._set_interpolation_availability()

    def _request_property(self):
        if self.session is None or not self.channel_dialog.isVisible():
            return
        s, ds = self.session, self.session.dataset
        channel = max(0, self.channel_table.currentRow())
        mark = self.channel_table.item(channel, 1)
        self.bad_channel.blockSignals(True)
        self.bad_channel.setChecked(mark.checkState() == Qt.CheckState.Checked)
        self.bad_channel.setEnabled(bool(mark.flags() & Qt.ItemFlag.ItemIsEnabled))
        self.bad_channel.setToolTip(mark.toolTip())
        self.bad_channel.blockSignals(False)
        processing, revision = s.candidate, s.revision
        self.property_request += 1
        request = self.property_request
        display_filter = self.display_filter
        self.property_label.setText(f"{ds.labels[channel]} · updating properties (previous curves retained)")

        def compute():
            values = s.engine.signal(processing, channels=[channel], display_filter=display_filter)[:, 0]
            freqs, psd = channel_spectrum(ds, values)
            location = channel_location_thumbnail(ds, channel) if self._has_locations(ds, [channel]) else None
            return freqs, psd, values * 1e6, location

        def display(result):
            if (
                self.session is not s
                or revision != s.revision
                or request != self.property_request
                or not self.channel_dialog.isVisible()
            ):
                return
            freqs, psd, values, location = result
            owner = "Preview" if processing != s.review.processing else "Applied"
            self.channel_properties.display(
                title=f"Channel {ds.labels[channel]} · {owner}",
                dataset=ds,
                values=values,
                freqs=freqs,
                psd=psd,
                location=location,
                units="µV",
                context=f"{self._describe(processing)}. Display filtering affects this view only.",
            )

        self.property_jobs.submit("property", compute, display)

    def _populate_ics(self, *_):
        if self.session is None:
            return
        while self.ic_grid.count():
            item = self.ic_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.ic_widgets.clear()
        ds = replace(self.session.dataset, ica=self.session.ica_for())
        self.ic_page.blockSignals(True)
        self.ic_page.setMaximum(max(1, ((ds.ica.count if ds.ica else 0) + 11) // 12))
        self.ic_page.blockSignals(False)
        if ds.ica is None:
            self.ic_grid.addWidget(
                QLabel(
                    "This dataset has no ICA decomposition; Epoch and channel inspection remain available."
                ),
                0,
                0,
            )
            return
        first = (self.ic_page.value() - 1) * 12
        for slot, component in enumerate(range(first, min(first + 12, ds.ica.count))):
            card = QPushButton()
            card.setMinimumHeight(150)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setObjectName("icCard")
            card.setCheckable(True)
            card.setChecked(component in self.session.candidate.excluded_ics)
            card.setToolTip(
                "Click to exclude / restore; right-click to inspect without changing the preview."
            )
            layout = QVBoxLayout(card)
            image = QLabel("Loading scalp map…")
            image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setMinimumSize(100, 95)
            layout.addWidget(image)
            label = QLabel(f"IC {component + 1:02d}")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)
            card.clicked.connect(lambda checked, c=component: self._click_ic(c, checked))
            card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            card.customContextMenuRequested.connect(lambda point, c=component: self.show_ic_property(c))
            self.ic_grid.addWidget(card, slot // 3, slot % 3)
            self.ic_widgets.append((component, image))
        self._request_topos()

    def _request_topos(self):
        s = self.session
        if not self.ic_dialog.isVisible():
            return
        components = [component for component, _ in self.ic_widgets]
        ds = replace(s.dataset, ica=s.ica_for())
        decomposition_id = ds.ica.decomposition_id
        if not self._has_locations(ds, ds.ica.channels):
            for _, image in self.ic_widgets:
                image.setText("No electrode locations\nYou can still inspect IC activity and properties")
            return

        def compute():
            return {
                (decomposition_id, c): component_thumbnail(ds, c)
                for c in components
                if (decomposition_id, c) not in self.topos
            }

        def display(result):
            if (
                self.session is not s
                or s.ica_for() is None
                or s.ica_for().decomposition_id != decomposition_id
            ):
                return
            self.topos.update(result)
            for component, image in self.ic_widgets:
                if (decomposition_id, component) not in self.topos:
                    continue
                rgba = self.topos[(decomposition_id, component)]
                height, width = rgba.shape[:2]
                qimage = QImage(
                    rgba.data, width, height, rgba.strides[0], QImage.Format.Format_RGBA8888
                ).copy()
                image.setPixmap(
                    QPixmap.fromImage(qimage).scaled(
                        110,
                        110,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        self.property_jobs.submit("topos", compute, display)

    def _ic_changed(self, component, checked):
        values = set(self.session.candidate.excluded_ics)
        values.add(component) if checked else values.discard(component)
        self._propose(replace(self.session.candidate, excluded_ics=tuple(sorted(values))))

    def _click_ic(self, component, excluded):
        self.active_ic = component
        self._ic_changed(component, excluded)

    def show_ic_property(self, component):
        s = self.session
        processing = s.candidate
        ds = replace(s.dataset, ica=s.ica_for(processing))
        self.active_ic = component
        self.ic_property_request += 1
        request = self.ic_property_request
        self.ic_properties.heading.setText(f"IC {component + 1:02d} · loading properties…")

        def compute():
            activations, freqs, psd = component_properties(s.component_dataset(processing), component)
            location = (
                component_thumbnail(ds, component) if self._has_locations(ds, ds.ica.channels) else None
            )
            return activations, freqs, psd, location

        def display(result):
            if (
                self.session is not s
                or request != self.ic_property_request
                or not self.ic_dialog.isVisible()
                or s.ica_for() is None
                or s.ica_for().decomposition_id != ds.ica.decomposition_id
            ):
                return
            values, freqs, psd, location = result
            self.ic_properties.display(
                title=f"IC {component + 1:02d} · {'Excluded' if component in s.candidate.excluded_ics else 'Retained'}"
                + (" · preview" if s.candidate != s.review.processing else " · applied"),
                dataset=ds,
                values=values,
                freqs=freqs,
                psd=psd,
                location=location,
                units="ICA units",
                context=f"Decomposition {ds.ica.decomposition_id[:8]} · fitted input. "
                "IC activity is shown before component removal; the spectrum uses its scalp projection.",
            )

        self.property_jobs.submit("ic-property", compute, display)

    def _autosave(self):
        if self.session and self.session_path and self.session.revision != self.session.saved_revision:
            try:
                save_session(self.session, self.session_path)
            except OSError as error:
                self.report_error(str(error))
                return False
        return True

    def save(self):
        if self.session is None:
            return False
        if self.session_path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save QC records", "review.eegqc.json", "JSON (*.json)"
            )
            if not path:
                return False
            self.session_path = Path(path)
        try:
            save_session(self.session, self.session_path)
            self.statusBar().showMessage(f"Records saved: {self.session_path}")
            return True
        except OSError as error:
            self.report_error(str(error))
            return False

    def save_next(self):
        if self.save():
            self.next_dataset(1)

    def restore(self):
        if self.session is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Restore QC records", "", "JSON (*.json)")
        if path:
            try:
                restore_session(self.session, path)
            except (ValueError, OSError, KeyError) as error:
                self.report_error(str(error))
                return
            self.session_path = Path(path)
            self._populate_channel_table()
            self._populate_ics()
            self._changed()

    def export(self):
        if self.session is None:
            return
        ds = self.session.dataset
        default = str(ds.source.with_name("qced_" + ds.source.name)) if ds.source else "qced_demo.set"
        path, _ = QFileDialog.getSaveFileName(self, "Export the applied state", default, "EEGLAB (*.set)")
        if path:
            from eegqc.application.long_tasks import export_task

            s = self.session
            self._submit_long_task(
                "export",
                export_task,
                args=(s, path),
                data_id=ds.data_id,
                input_state=s.review.processing,
                output_path=path,
                callback=lambda saved: self.statusBar().showMessage(f"Exported: {saved}"),
                valid=lambda: self.session is s,
            )

    def labels_export(self):
        if self.session is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export reviewed labels", "eegqc_labels.csv", "CSV (*.csv)"
        )
        if path:
            try:
                export_labels(self.session, path)
                self.statusBar().showMessage(f"Labels exported: {path}")
            except OSError as error:
                self.report_error(str(error))

    def report_error(self, message):
        self.statusBar().showMessage("Action incomplete: " + message)
        self.processing_label.setText("Action incomplete: " + message)

    def _job_error(self, message):
        self.statusBar().showMessage("Action incomplete: " + message)
        if self.session and self.session.candidate != self.session.review.processing:
            self.processing_label.setText(
                "Preview unavailable: " + message + "\nReset the preview to retain the applied state."
            )
        dialog = getattr(self, "ica_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.prepare_button.setEnabled(True)
            dialog.summary.setText("Action incomplete: " + message)

    def _long_task_changed(self, record):
        running = record.status in ("running", "cancelling")
        self.task_cancel.setVisible(running)
        self.task_cancel.setEnabled(record.status == "running")
        names = {
            "asr": "ASR",
            "ica": "ICA",
            "ica-input": "ICA Input check",
            "export": "Export",
            "contribution": "Export contribution",
        }
        states = {"succeeded": "Completed", "failed": "failed", "cancelled": "Canceled"}
        self.task_label.setText(
            f"{names.get(record.kind, record.kind)} · {states.get(record.status, record.stage)} · {record.elapsed:.0f} s"
        )
        if record.kind == "ica-input" and record.status in ("cancelled", "failed"):
            dialog = getattr(self, "ica_dialog", None)
            if dialog is not None and dialog.isVisible():
                dialog.prepare_button.setEnabled(True)
                dialog.summary.setText(
                    "Input check canceled. Adjust the selection and check again."
                    if record.status == "cancelled"
                    else "Input check failed. Review the selection and parameters."
                )

    def _receive_task_candidate(self, session, original_processing, original_candidate, apply):
        if session.review.processing == original_processing and session.candidate == original_candidate:
            apply()
        else:
            self.pending_task_candidate = (session, apply)
            self.task_result.show()
            self.statusBar().showMessage(
                "Computation finished after the processing state changed. Click“Inspect completed preview”to inspect the result for its original input."
            )
            self.processing_label.setText(
                self.processing_label.text()
                + "\nCompleted task input: "
                + self._describe(original_processing)
            )

    def _show_task_candidate(self):
        if self.pending_task_candidate:
            session, apply = self.pending_task_candidate
            if self.session is session:
                apply()
        self.pending_task_candidate = None
        self.task_result.hide()

    def closeEvent(self, event):
        if not self._autosave():
            event.ignore()
            return
        for dialog in (self.channel_dialog, self.ic_dialog, self.review_dialog, self.topography_dialog):
            dialog.hide()
        self.render_timer.stop()
        self.autosave_timer.stop()
        self.jobs.invalidate()
        self.property_jobs.invalidate()
        self.network_jobs.invalidate()
        self.epochrank.jobs.invalidate()
        self.long_tasks.close()
        if (
            self.jobs.active is not None
            or self.property_jobs.active is not None
            or self.network_jobs.active is not None
            or self.epochrank.jobs.active is not None
        ):
            event.ignore()
            self.hide()
            QTimer.singleShot(100, self.close)
            return
        event.accept()
