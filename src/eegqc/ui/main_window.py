from copy import copy
from dataclasses import asdict, replace
from pathlib import Path
import re
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, QTimer, QSettings
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
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from eegqc.adapters.eeglab import (
    channel_spectrum,
    component_properties,
    component_thumbnail,
    channel_location_thumbnail,
    export_set,
    load_set,
)
from eegqc.application.persistence import export_labels, restore_session, save_session
from eegqc.application.session import Session
from eegqc.demo import make_demo
from eegqc.ui.jobs import Jobs
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
        self.setWindowTitle("EEGqc · 人工质量控制")
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
        self.jobs = Jobs(self)
        self.jobs.failed.connect(self._job_error)
        self.network_jobs = Jobs(self)
        self.network_jobs.failed.connect(self._job_error)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._request_render)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._autosave)
        self._menus()
        self._layout()
        self.configure_workflows()
        self.default_layout = self.saveState()
        self._action(self.views_menu, "保存当前面板布局", self.save_layout)
        self._action(self.views_menu, "载入保存的面板布局", self.load_layout)
        self._action(self.views_menu, "恢复默认面板布局", lambda: self.restoreState(self.default_layout))
        self.statusBar().showMessage("打开 .set 文件，或从文件菜单载入合成演示数据")

    def _action(self, menu, text, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    def _menus(self):
        files = self.menuBar().addMenu("文件")
        self.file_menu = files
        self._action(files, "打开 .set…", self.open_dialog, "Ctrl+O")
        self._action(files, "搜索数据目录…", self.search_directory)
        self._action(files, "合成演示数据", lambda: self.open_demo())
        files.addSeparator()
        self._action(files, "保存质控记录…", self.save, "Ctrl+S")
        self._action(files, "恢复质控记录…", self.restore)
        self._action(files, "导出清理后的 .set…", self.export)
        self._action(files, "导出人工标签 CSV…", self.labels_export)
        edit = self.menuBar().addMenu("编辑")
        self._action(edit, "撤销", lambda: self._history(False), "Ctrl+Z")
        self._action(edit, "重做", lambda: self._history(True), "Ctrl+Shift+Z")
        self._action(edit, "标记 / 取消当前 epoch", self.toggle_current)
        self._action(edit, "上一页", lambda: self.page(-1))
        self._action(edit, "下一页", lambda: self.page(1))
        self.views_menu = self.menuBar().addMenu("视图")
        algorithms = self.menuBar().addMenu("算法")
        self.algorithm_menu = algorithms
        self._action(algorithms, "振幅阈值候选检测…", self.detect)
        self._action(algorithms, "固定当前工作状态作为对比参考", self.freeze_reference)
        help_menu = self.menuBar().addMenu("帮助")
        self._action(
            help_menu,
            "操作说明",
            lambda: QMessageBox.information(
                self,
                "EEGqc",
                "B：标记/取消 epoch；方向键：翻页；Ctrl+Z：撤销。\n"
                "IC 或插值修改先进入候选预览，采用后可继续标记。\n"
                "质控记录保存为 JSON；清理数据通过文件 → 导出 .set。\n"
                "合成演示数据只用于体验界面，不是实验数据。",
            ),
        )

    def _layout(self):
        nav = QToolBar("工作区", self)
        nav.setMovable(False)
        self.addToolBar(nav)
        brand = QLabel("EEGqc   ")
        brand.setObjectName("brand")
        nav.addWidget(brand)
        self.workspace_buttons = QButtonGroup(self)
        for i, name in enumerate(("Epoch 检查", "通道检查", "IC 检查", "结果复核")):
            item = button(name, lambda checked=False, n=i: self.set_workspace(n))
            item.setCheckable(True)
            self.workspace_buttons.addButton(item, i)
            nav.addWidget(item)
        self.workspace_buttons.button(0).setChecked(True)
        nav.addSeparator()
        nav.addWidget(button("打开数据集", self.open_dialog))
        nav.addWidget(button("‹ 数据集", lambda: self.next_dataset(-1)))
        nav.addWidget(button("数据集 ›", lambda: self.next_dataset(1)))
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.setSpacing(7)
        self.context = QLabel("尚未载入数据")
        self.context.setObjectName("context")
        layout.addWidget(self.context)
        self.stack = QStackedWidget()
        self._epoch_page()
        self._channel_page()
        self._ic_page()
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        self.stack.addWidget(self.review_text)
        layout.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        footer.addWidget(QLabel("数据集评级"))
        self.ratings = QButtonGroup(self)
        for label in ("可用", "待复核", "不可用"):
            radio = QRadioButton(label)
            self.ratings.addButton(radio)
            footer.addWidget(radio)
            radio.toggled.connect(lambda checked: self._rating_changed() if checked else None)
        self.dataset_note = QLineEdit()
        self.dataset_note.setPlaceholderText("数据集备注…")
        self.dataset_note.editingFinished.connect(self._rating_changed)
        footer.addWidget(self.dataset_note, 1)
        footer.addWidget(button("保存记录", self.save))
        footer.addWidget(button("保存并下一份 →", self.save_next, True))
        layout.addLayout(footer)
        self.setCentralWidget(root)
        self._inspector()
        self._erp_dock()

    def _epoch_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.batch_mark = button("标记选中 epochs", lambda: self.mark_selected(True))
        self.batch_keep = button("确认保留", lambda: self.mark_selected(False))
        toolbar.addWidget(self.batch_mark)
        toolbar.addWidget(self.batch_keep)
        self.amplitude = QComboBox()
        self.amplitude.addItems(["25 μV", "50 μV", "100 μV", "200 μV"])
        self.amplitude.setCurrentIndex(1)
        self.amplitude.currentIndexChanged.connect(self.request_render)
        toolbar.addWidget(self.amplitude)
        self.channel_count = QComboBox()
        self.channel_count.addItems(["16 通道", "32 通道", "64 通道"])
        self.channel_count.currentIndexChanged.connect(self.request_render)
        toolbar.addWidget(self.channel_count)
        self.channel_start = QSpinBox()
        self.channel_start.setPrefix("起始通道 ")
        self.channel_start.setMinimum(1)
        self.channel_start.valueChanged.connect(self.request_render)
        toolbar.addWidget(self.channel_start)
        toolbar.addStretch()
        self.compare = QCheckBox("叠加参考")
        self.compare.toggled.connect(self.request_render)
        toolbar.addWidget(self.compare)
        toolbar.addWidget(button("辅助检测…", self.detect))
        layout.addLayout(toolbar)
        self.waveform = Waveform()
        self.waveform.epoch_selected.connect(self.select_epoch)
        self.waveform.range_selected.connect(self.select_interval)
        layout.addWidget(self.waveform, 1)
        row = QHBoxLayout()
        row.addWidget(button("‹", lambda: self.page(-1)))
        self.navigator = QListWidget()
        self.navigator.setViewMode(QListWidget.ViewMode.IconMode)
        self.navigator.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.navigator.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.navigator.setGridSize(QSize(42, 30))
        self.navigator.setMaximumHeight(78)
        self.navigator.itemClicked.connect(lambda item: self.select_epoch(self.navigator.row(item)))
        for target in (self.waveform, self.navigator):
            for sequence, callback in (
                ("B", self.toggle_current),
                ("Left", lambda: self.page(-1)),
                ("Right", lambda: self.page(1)),
            ):
                shortcut = QShortcut(QKeySequence(sequence), target)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(callback)
        row.addWidget(self.navigator, 1)
        self.time_window_label = QLabel()
        row.addWidget(self.time_window_label, 1)
        self.time_window_label.hide()
        row.addWidget(button("›", lambda: self.page(1)))
        layout.addLayout(row)
        self.epoch_summary = QLabel("B 标记 / 取消 · Ctrl+Z 撤销")
        self.epoch_summary.setObjectName("muted")
        layout.addWidget(self.epoch_summary)
        self.stack.addWidget(page)

    def _channel_page(self):
        splitter = QSplitter()
        self.channel_table = QTableWidget(0, 3)
        self.channel_table.setHorizontalHeaderLabels(["通道", "插值预览", "排除导出"])
        self.channel_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.channel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.channel_table.itemChanged.connect(self._channel_changed)
        self.channel_table.currentCellChanged.connect(lambda *_: self._request_property())
        splitter.addWidget(self.channel_table)
        properties = QWidget()
        layout = QVBoxLayout(properties)
        self.property_label = QLabel("选择通道查看当前工作数据的属性")
        self.property_label.setWordWrap(True)
        property_header = QHBoxLayout()
        property_header.addWidget(self.property_label, 1)
        self.channel_location = QLabel()
        self.channel_location.setFixedSize(125, 125)
        property_header.addWidget(self.channel_location)
        layout.addLayout(property_header)
        self.spectrum = plot("功率谱 · EEGPrep spectopo")
        self.spectrum.setLabel("bottom", "频率", units="Hz")
        self.spectrum.setLabel("left", "功率 (dB μV²/Hz)")
        self.spectrum_curve = self.spectrum.plot(pen=pg.mkPen("#168b9d", width=1.7))
        layout.addWidget(self.spectrum)
        image_plot = pg.PlotItem()
        image_plot.setLabel("bottom", "相对事件时间", units="s")
        image_plot.setLabel("left", "Epoch")
        image_plot.getViewBox().setAspectLocked(False)
        self.erp_image = pg.ImageView(view=image_plot)
        self.erp_image.setColorMap(pg.colormap.get("CET-D1"))
        self.erp_image.ui.roiBtn.hide()
        self.erp_image.ui.menuBtn.hide()
        layout.addWidget(QLabel("Epoch × 时间 · 当前工作数据 (μV)"))
        layout.addWidget(self.erp_image)
        splitter.addWidget(properties)
        splitter.setSizes([330, 760])
        self.stack.addWidget(splitter)

    def _ic_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("选择排除的成分；地形图来自 EEGPrep topoplot"))
        row.addStretch()
        row.addWidget(button("清除 IC 选择", self.clear_ics))
        row.addWidget(button("ICLabel…", self.run_iclabel))
        self.ic_page = QSpinBox()
        self.ic_page.setPrefix("页 ")
        self.ic_page.setMinimum(1)
        self.ic_page.valueChanged.connect(self._populate_ics)
        row.addWidget(self.ic_page)
        layout.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.ic_container = QWidget()
        self.ic_container.setObjectName("icContainer")
        self.ic_container.setStyleSheet("QWidget#icContainer { background: #2b333d; }")
        self.ic_grid = QGridLayout(self.ic_container)
        self.ic_grid.setSpacing(16)
        scroll.setWidget(self.ic_container)
        layout.addWidget(scroll, 1)
        self.ic_widgets = []
        self.stack.addWidget(page)

    def _inspector(self):
        dock = QDockWidget("检查与预览", self)
        dock.setObjectName("inspector")
        self.inspector = dock
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        self.current_label = QLabel("当前 Epoch")
        self.current_label.setObjectName("heading")
        layout.addWidget(self.current_label)
        self.current_status = QLabel("未检查")
        layout.addWidget(self.current_status)
        self.reason = QComboBox()
        self.reason.addItem("不确定 / 待复核", "uncertain")
        for label, value in (
            ("未分类伪迹", "signal_artifact"),
            ("眨眼 / 眼动", "eye"),
            ("肌电", "muscle"),
            ("电极 / 运动", "movement/electrode"),
            ("工频", "line_noise"),
            ("漂移", "drift"),
            ("平直 / 饱和", "flat/clipping"),
            ("行为错误", "behavioral"),
            ("实验筛选", "condition_selection"),
        ):
            self.reason.addItem(label, value)
        self.reason.setCurrentIndex(self.reason.findData("signal_artifact"))
        layout.addWidget(QLabel("标记原因"))
        layout.addWidget(self.reason)
        self.epoch_note = QPlainTextEdit()
        self.epoch_note.setPlaceholderText("当前 epoch 的判断依据…")
        self.epoch_note.setMaximumHeight(80)
        layout.addWidget(self.epoch_note)
        self.mark_button = button("标记为坏", lambda: self.mark_selected(True))
        layout.addWidget(self.mark_button)
        self.keep_button = button("确认保留 / 取消拒绝", lambda: self.mark_selected(False))
        layout.addWidget(self.keep_button)
        self.processing_label = QLabel("当前工作数据：输入")
        self.processing_label.setWordWrap(True)
        layout.addWidget(self.processing_label)
        self.adopt_button = button("采用预览并继续 QC", self.adopt, True)
        layout.addWidget(self.adopt_button)
        layout.addWidget(button("取消候选预览", self.reset_preview))
        self.average_ref = QCheckBox("平均参考预览")
        self.average_ref.toggled.connect(self._reference_changed)
        layout.addWidget(self.average_ref)
        self.erp_toggle_button = button(
            "显示 / 隐藏条件 ERP", lambda: self.erp_dock.setVisible(not self.erp_dock.isVisible())
        )
        layout.addWidget(self.erp_toggle_button)
        layout.addWidget(button("固定当前状态为对比参考", self.freeze_reference))
        layout.addWidget(QLabel("历史记录"))
        self.history_label = QLabel("尚无操作")
        self.history_label.setWordWrap(True)
        self.history_label.setObjectName("muted")
        layout.addWidget(self.history_label)
        layout.addStretch()
        dock.setWidget(body)
        dock.setMinimumWidth(290)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.views_menu.addAction(dock.toggleViewAction())

    def _erp_dock(self):
        self.erp_dock = QDockWidget("条件 ERP · 当前与固定参考", self)
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
        self.erp_baseline = QCheckBox("基线：起点至 0 s")
        self.erp_baseline.toggled.connect(self.request_render)
        self.erp_comparison = QComboBox()
        self.erp_comparison.addItems(
            ["整体 QC 前后", "只比较处理效果（相同 trials）", "只比较 epoch 剔除效果"]
        )
        self.erp_comparison.currentIndexChanged.connect(self.request_render)
        self.split_conditions = QCheckBox("按条件叠加")
        self.split_conditions.toggled.connect(self.request_render)
        self.use_custom_conditions = QCheckBox("使用组合条件")
        self.use_custom_conditions.toggled.connect(self.request_render)
        for item in (
            QLabel("条件"),
            self.condition_field,
            self.condition_value,
            QLabel("通道"),
            self.erp_channel,
            self.erp_baseline,
            self.split_conditions,
        ):
            toolbar.addWidget(item)
        layout.addLayout(toolbar)
        advanced = QHBoxLayout()
        advanced.addWidget(self.erp_comparison)
        advanced.addWidget(self.use_custom_conditions)
        advanced.addWidget(button("编辑条件组合…", self.edit_conditions))
        advanced.addStretch()
        layout.addLayout(advanced)
        self.erp_plot = plot("未载入分段数据")
        self.erp_plot.setMinimumHeight(180)
        self.erp_plot.setLabel("bottom", "相对事件时间", units="ms")
        self.erp_plot.setLabel("left", "振幅", units="μV")
        self.erp_plot.getAxis("left").enableAutoSIPrefix(False)
        self.erp_legend = self.erp_plot.addLegend(offset=(12, 10), colCount=2, labelTextSize="9pt")
        self.erp_ref_curve = self.erp_plot.plot(pen=pg.mkPen("#929daa", width=1.5), name="参考")
        self.erp_curve = self.erp_plot.plot(pen=pg.mkPen("#148d9f", width=2), name="当前 / 候选")
        self.condition_curves = {}
        layout.addWidget(self.erp_plot)
        self.erp_dock.setWidget(body)
        self.erp_dock.visibilityChanged.connect(self.request_render)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.erp_dock)
        self.views_menu.addAction(self.erp_dock.toggleViewAction())
        self.erp_dock.hide()

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 EEG 数据", "", "EEGLAB (*.set)")
        if path:
            self.open_path(path)

    def save_layout(self):
        QSettings("EEGqc", "EEGqc").setValue("layout", self.saveState())
        self.statusBar().showMessage("面板布局已保存")

    def load_layout(self):
        saved = QSettings("EEGqc", "EEGqc").value("layout")
        if saved is not None:
            self.restoreState(saved)
        else:
            self.statusBar().showMessage("尚未保存面板布局")

    def open_demo(self):
        if not self._autosave():
            return
        self.jobs.invalidate()
        self.statusBar().showMessage("正在生成合成演示数据…")
        self.jobs.submit("load", make_demo, self.set_dataset)

    def open_path(self, path):
        if not self._autosave():
            return
        self.jobs.invalidate()
        self.statusBar().showMessage("正在通过 EEGPrep 读取数据…")
        self.jobs.submit("load", lambda: load_set(path), self.set_dataset)

    def set_dataset(self, ds):
        self.jobs.invalidate()
        self.session = Session(ds)
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
            except (ValueError, OSError) as error:
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
        self.condition_field.addItem("全部", "")
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
        self.request_render()

    def search_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "选择数据目录")
        if not folder:
            return
        pattern, accepted = QInputDialog.getText(self, "搜索数据", "文件名正则表达式", text=r".*\.set$")
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
            self.statusBar().showMessage("没有找到匹配的数据集")

    def next_dataset(self, direction=1):
        if self.session is None or not self.queue:
            return
        current = self.session.dataset.source
        index = self.queue.index(current) if current in self.queue else -1
        index += direction
        if 0 <= index < len(self.queue):
            self.open_path(self.queue[index])
        else:
            self.statusBar().showMessage("已到数据集队列边界")

    def set_workspace(self, index):
        self.workspace_buttons.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)
        if index == 1:
            self._request_property()
        if index == 2:
            self._populate_ics()
        if index == 3:
            self.erp_dock.show()
        self.request_render()

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
            self.current_label.setText(f"时间 {left / ds.sfreq:.2f}–{right / ds.sfreq:.2f} s")

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
            self.select_epoch(int(np.clip(self.first_epoch + direction * 5, 0, ds.n_epochs - 1)))

    def mark_selected(self, rejected):
        if self.session is None:
            return
        if self.session.candidate != self.session.review.processing:
            self.statusBar().showMessage("先采用候选预览，再在该状态上标记")
            return
        if self.rendered_state != (self.session.dataset.data_id, self.session.review.processing):
            self.statusBar().showMessage("预览更新中，显示当前工作状态后可继续标记。")
            return
        if not self.session.dataset.continuous:
            shown_first = self.session.view_context.get("first_epoch", -5)
            if not shown_first <= self.current_epoch < shown_first + 5:
                self.statusBar().showMessage("正在切换到所选 epoch，请等待波形显示。")
                return
        if self.session.dataset.continuous:
            if self.session.view_context.get("continuous_start_sample") != self.continuous_start:
                self.statusBar().showMessage("正在切换时间窗口，请等待波形显示。")
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

    def toggle_current(self):
        if self.session is not None:
            if self.session.dataset.continuous and self.interval_selection:
                start, stop = self.interval_selection
                overlaps = any(d.start < stop and d.stop > start for d in self.session.review.intervals)
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

    def _rating_changed(self):
        if self.session is not None and self.ratings.checkedButton():
            self.session.set_rating(self.ratings.checkedButton().text(), self.dataset_note.text())
            self._changed()

    def _changed(self):
        self._sync_state()
        self.request_render()
        self.autosave_timer.start(700)

    def _describe(self, processing):
        ds = self.session.dataset
        parts = []
        if processing.asr:
            parts.append(
                f"ASR（cutoff={processing.asr.cutoff:g}，输入：{self._describe(processing.asr.parent)}）"
            )
        if processing.excluded_ics:
            parts.append("排除 IC " + ", ".join(str(i + 1) for i in processing.excluded_ics))
        if processing.interpolated:
            parts.append("插值 " + ", ".join(ds.labels[i] for i in processing.interpolated))
        if processing.dropped:
            parts.append("排除通道 " + ", ".join(ds.labels[i] for i in processing.dropped))
        if processing.average_reference:
            parts.append("平均参考")
        return "；".join(parts) or "输入数据"

    def _set_context_badge(self, setting):
        ds = self.session.dataset
        demo = " · 合成演示" if ds.source is None else ""
        extent = f"连续 {ds.data.shape[2] / ds.sfreq:.1f} s" if ds.continuous else f"{ds.n_epochs} epochs"
        text = f"{ds.source.name if ds.source else ds.name}{demo}    |    {ds.n_channels} 通道 · {ds.sfreq:g} Hz · {extent}"
        if setting.get("low") or setting.get("high"):
            text += (
                f"    |    显示滤波 {setting.get('low') or 0:g}–{setting.get('high') or ds.sfreq / 2:g} Hz"
            )
        self.context.setText(text)

    def _sync_state(self):
        if self.session is None:
            return
        s, ds = self.session, self.session.dataset
        self._set_context_badge(s.view_context.get("display_filter", {}))
        decision = dict(s.review.decisions).get(self.current_epoch)
        self.current_label.setText(f"Epoch {self.current_epoch + 1:03d} / {ds.n_epochs}")
        if ds.continuous:
            self.current_label.setText("连续数据 · 时间段检查")
        self.current_status.setText(
            "已标记为坏" if decision and decision.rejected else "已确认保留" if decision else "未检查"
        )
        self.current_status.setStyleSheet(
            "color: #f18a83" if decision and decision.rejected else "color: #a9c2ce"
        )
        self.current_status.setToolTip(f"原始 TrialID: {ds.trial_ids[self.current_epoch]}")
        if decision:
            self.reason.setCurrentIndex(max(0, self.reason.findData(decision.reason)))
            self.epoch_note.setPlainText(decision.note)
        else:
            self.epoch_note.clear()
        pending = s.candidate != s.review.processing
        self.adopt_button.setEnabled(pending)
        self.mark_button.setEnabled(not pending)
        self.keep_button.setEnabled(not pending)
        self.mark_button.setText("标记选中时间段" if ds.continuous else "标记为坏")
        self.keep_button.setText("清除重叠时间标记" if ds.continuous else "确认保留 / 取消拒绝")
        self.batch_mark.setText("标记选中时间段" if ds.continuous else "标记选中 epochs")
        self.batch_keep.setText("清除重叠标记" if ds.continuous else "确认保留")
        self.epoch_note.setPlaceholderText(
            "当前时间段的判断依据…" if ds.continuous else "当前 epoch 的判断依据…"
        )
        self.average_ref.blockSignals(True)
        self.average_ref.setChecked(s.candidate.average_reference)
        self.average_ref.blockSignals(False)
        self.processing_label.setText(
            "当前："
            + self._describe(s.review.processing)
            + ("\n候选：" + self._describe(s.candidate) if pending else "")
        )
        n = len(s.review.rejected)
        self.epoch_summary.setText(
            f"已标记 {n} / {ds.n_epochs} epochs · {100 * n / ds.n_epochs:.1f}%"
            "     B 标记/取消 · Ctrl+Z 撤销 · Ctrl+单击多选"
        )
        if ds.continuous:
            self.epoch_summary.setText(
                f"连续数据 · {len(s.review.intervals)} 个时间标记 · 拖动蓝色选择范围后标记"
            )
        for i in range(self.navigator.count()):
            item = self.navigator.item(i)
            item.setForeground(QColor("#f18a83") if i in s.review.rejected else QColor("#c8d4df"))
            item.setBackground(QColor("#563c40") if i in s.review.rejected else QColor("#2b333d"))
        for radio in self.ratings.buttons():
            radio.blockSignals(True)
            radio.setChecked(radio.text() == s.review.rating)
            radio.blockSignals(False)
        if not self.dataset_note.hasFocus():
            self.dataset_note.setText(s.review.note)
        stale = sum(d.processing != s.review.processing for _, d in s.review.decisions)
        self.history_label.setText(
            f"{len(s._undo)} 步可撤销 · {len(s.review.decisions)} 个已检查 epochs"
            + (f"\n{stale} 个判断来自较早处理状态，待复核" if stale else "")
        )
        if ds.continuous:
            self.history_label.setText(f"{len(s._undo)} 步可撤销 · {len(s.review.intervals)} 个时间标记")
        self.review_text.setPlainText(
            "当前工作状态\n\n"
            + self._describe(s.review.processing)
            + f"\n\nEpoch 剔除：{n} / {ds.n_epochs}\n已人工检查：{len(s.review.decisions)}\n"
            f"\n评级：{s.review.rating}\n备注：{s.review.note}\n\n"
            "条件 ERP 可以比较整体 QC、固定 trials 的处理效果和 epoch 剔除效果。\n"
            "保存记录保留质控决定；文件 → 导出 .set 生成清理后的数据。\n"
            "导出插值/重参考/删除通道后的数据时，会清除不再适用的 ICA 派生字段。"
        )

    def request_render(self, *_):
        if self.session:
            self.view_revision += 1
            self.render_timer.start(35)

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
            self.statusBar().showMessage("当前范围没有保留通道")
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
        view_context = {
            "workspace": self.stack.currentIndex(),
            "visible_channels": channels,
            "first_epoch": first,
            "continuous_start_sample": self.continuous_start,
            "display_filter": asdict(display_filter),
            "baseline": list(baseline) if baseline else None,
            "erp_visible": show_erp,
            "erp_conditions": {name: asdict(q) for name, q in conditions},
            "iclabel_seen": {k: s.iclabel[k] for k in ("algorithm", "backend", "decomposition_id")}
            if s.iclabel and s.iclabel.get("shown")
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
                intervals=current.intervals,
            )
            if ds.continuous:
                stop = min(continuous_start + values.shape[2], ds.data.shape[2])
                self.time_window_label.setText(
                    f"{continuous_start / ds.sfreq:.1f}–{stop / ds.sfreq:.1f} s / "
                    f"{ds.data.shape[2] / ds.sfreq:.1f} s    ·    ← → 切换时间窗口"
                )
            if erps:
                self._display_erps(erps, ds.labels[erp_channel])
            s.view_context = view_context
            self._set_context_badge(view_context["display_filter"])
            self.rendered_state = (ds.data_id, candidate)
            self.statusBar().showMessage(
                f"预览更新 {(time.perf_counter() - t0) * 1000:.0f} ms · "
                "源文件未修改" + (" · 候选尚未采用" if candidate != current.processing else "")
            )

        self.jobs.submit("render", compute, display)
        if self.stack.currentIndex() == 1:
            self._request_property()

    def _display_erps(self, erps, channel):
        wanted = set()
        summaries = []
        if len(erps) == 1:
            for curve, label in ((self.erp_curve, "当前 / 候选"), (self.erp_ref_curve, "参考")):
                if self.erp_legend.getLabel(curve) is None:
                    self.erp_legend.addItem(curve, label)
            group, a, b = erps[0]
            self.erp_curve.setData(
                a.times * 1000, a.data[0] * 1e6
            ) if a.data is not None else self.erp_curve.setData([], [])
            self.erp_ref_curve.setData(
                b.times * 1000, b.data[0] * 1e6
            ) if b.data is not None else self.erp_ref_curve.setData([], [])
            summaries.append(f"{group[1] if group else '全部'} 当前 N={a.count} / 参考 N={b.count}")
        else:
            self.erp_legend.removeItem(self.erp_curve)
            self.erp_legend.removeItem(self.erp_ref_curve)
            self.erp_curve.setData([], [])
            self.erp_ref_curve.setData([], [])
            colors = ["#148d9f", "#b06d42", "#7960a0", "#477f52", "#ad5268"]
            for i, (group, a, b) in enumerate(erps):
                for kind, result in (("当前", a), ("参考", b)):
                    key = (group, kind)
                    wanted.add(key)
                    if key not in self.condition_curves:
                        pen = pg.mkPen(
                            colors[i % len(colors)],
                            width=1.7,
                            style=Qt.PenStyle.SolidLine if kind == "当前" else Qt.PenStyle.DashLine,
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
        self.erp_plot.setTitle(f"{channel} · " + "  |  ".join(summaries), color="#526271", size="10pt")

    def _populate_channel_table(self):
        if self.session is None:
            return
        ds = self.session.dataset
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
        self.channel_table.blockSignals(False)

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

    def _request_property(self):
        if self.session is None or self.stack.currentIndex() != 1:
            return
        s, ds = self.session, self.session.dataset
        channel = max(0, self.channel_table.currentRow())
        processing, revision = s.candidate, s.revision

        def compute():
            values = s.engine.signal(processing, channels=[channel], display_filter=self.display_filter)[:, 0]
            freqs, psd = channel_spectrum(ds, values)
            return freqs, psd, values * 1e6, channel_location_thumbnail(ds, channel)

        def display(result):
            if self.session is not s or revision != s.revision:
                return
            freqs, psd, values, location = result
            image = QImage(
                location.data,
                location.shape[1],
                location.shape[0],
                location.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy()
            self.channel_location.setPixmap(
                QPixmap.fromImage(image).scaled(125, 125, Qt.AspectRatioMode.KeepAspectRatio)
            )
            self.property_label.setText(f"{ds.labels[channel]} · 当前 / 候选状态 · EEGPrep 属性计算")
            self.spectrum_curve.setData(freqs, psd)
            bound = max(1.0, float(np.percentile(np.abs(values), 98)))
            self.erp_image.setImage(
                values.T, autoRange=True, levels=(-bound, bound), pos=(ds.tmin, 0), scale=(1 / ds.sfreq, 1)
            )

        self.jobs.submit("property", compute, display)

    def _populate_ics(self, *_):
        if self.session is None:
            return
        while self.ic_grid.count():
            item = self.ic_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.ic_widgets.clear()
        ds = self.session.dataset
        if ds.ica is None:
            self.ic_grid.addWidget(QLabel("该数据没有 ICA 分解；Epoch 和通道检查仍可使用。"), 0, 0)
            return
        first = (self.ic_page.value() - 1) * 12
        for slot, component in enumerate(range(first, min(first + 12, ds.ica.count))):
            card = QWidget()
            card.setStyleSheet("background: #303943; border-radius: 4px;")
            layout = QVBoxLayout(card)
            image = QLabel("地形图计算中…")
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setMinimumSize(165, 150)
            check = QCheckBox(f"排除 IC {component + 1:02d}")
            check.setChecked(component in self.session.candidate.excluded_ics)
            check.setEnabled(self.session.candidate.asr is None)
            check.toggled.connect(lambda checked, c=component: self._ic_changed(c, checked))
            layout.addWidget(image, 1)
            layout.addWidget(check)
            layout.addWidget(
                button("查看 IC 属性", lambda checked=False, c=component: self.show_ic_property(c))
            )
            self.ic_grid.addWidget(card, slot // 4, slot % 4)
            self.ic_widgets.append((component, image))
        self._request_topos()

    def _request_topos(self):
        s = self.session
        if self.stack.currentIndex() != 2:
            return
        components = [component for component, _ in self.ic_widgets]

        def compute():
            return {c: component_thumbnail(s.dataset, c) for c in components if c not in self.topos}

        def display(result):
            if self.session is not s:
                return
            self.topos.update(result)
            for component, image in self.ic_widgets:
                if component not in self.topos:
                    continue
                rgba = self.topos[component]
                height, width = rgba.shape[:2]
                qimage = QImage(
                    rgba.data, width, height, rgba.strides[0], QImage.Format.Format_RGBA8888
                ).copy()
                image.setPixmap(
                    QPixmap.fromImage(qimage).scaled(
                        165,
                        165,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        self.jobs.submit("topos", compute, display)

    def _ic_changed(self, component, checked):
        values = set(self.session.candidate.excluded_ics)
        values.add(component) if checked else values.discard(component)
        self._propose(replace(self.session.candidate, excluded_ics=tuple(sorted(values))))

    def show_ic_property(self, component):
        s, ds = self.session, self.session.dataset
        self.statusBar().showMessage(f"计算 IC {component + 1} 属性…")

        def display(result):
            if self.session is not s:
                return
            activations, freqs, psd = result
            dialog = QDialog(self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.setWindowTitle(f"IC {component + 1:02d} · 来源分解的属性")
            dialog.resize(1000, 650)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("成分活动基于输入 ICA 分解；波形对比和清理效果在工作区查看。"))
            activity = plot("IC 活动 · 前 5 个 epochs")
            for i, epoch in enumerate(activations[:5]):
                activity.plot(
                    ds.times + i * len(ds.times) / ds.sfreq, epoch, pen=pg.mkPen("#168b9d", width=1)
                )
            activity.setLabel("left", "激活 (原 ICA 尺度)")
            activity.setLabel("bottom", "时间", units="s")
            layout.addWidget(activity)
            spectrum = plot("IC 投影功率谱 · EEGPrep compute_spectra")
            spectrum.plot(freqs, psd, pen=pg.mkPen("#168b9d", width=1.5))
            spectrum.setLabel("bottom", "频率", units="Hz")
            spectrum.setLabel("left", "投影功率 (dB)")
            layout.addWidget(spectrum)
            self.property_dialog = dialog
            dialog.show()

        self.jobs.submit("ic-property", lambda: component_properties(ds, component), display)

    def detect(self):
        if self.session is None or self.session.dataset.continuous:
            self.statusBar().showMessage("振幅候选检测目前用于分段数据")
            return
        threshold, ok = QInputDialog.getDouble(self, "振幅阈值", "绝对振幅阈值 (μV)", 100, 1, 10000, 1)
        if not ok:
            return
        s, revision, proc = self.session, self.session.revision, self.session.candidate

        def done(indices):
            if self.session is not s or revision != s.revision:
                return
            self.navigator.clearSelection()
            for index in indices:
                self.navigator.item(int(index)).setSelected(True)
                s.suggestions[int(index)] = (f"amplitude_threshold:{threshold:g}uV;processing={proc}",)
            self.statusBar().showMessage(
                f"发现 {len(indices)} 个候选；仅选中，尚未写入人工标签。确认后可标记。"
            )

        self.jobs.submit("detect", lambda: s.engine.amplitude_candidates(proc, threshold), done)

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
            path, _ = QFileDialog.getSaveFileName(self, "保存质控记录", "review.eegqc.json", "JSON (*.json)")
            if not path:
                return False
            self.session_path = Path(path)
        try:
            save_session(self.session, self.session_path)
            self.statusBar().showMessage(f"记录已保存：{self.session_path}")
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
        path, _ = QFileDialog.getOpenFileName(self, "恢复质控记录", "", "JSON (*.json)")
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
        path, _ = QFileDialog.getSaveFileName(self, "导出当前已采用状态", default, "EEGLAB (*.set)")
        if path:
            snapshot = copy(self.session)
            self.jobs.submit(
                "export",
                lambda: export_set(snapshot, path),
                lambda saved: self.statusBar().showMessage(f"已导出：{saved}"),
            )

    def labels_export(self):
        if self.session is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出已人工检查的标签", "eegqc_labels.csv", "CSV (*.csv)")
        if path:
            try:
                export_labels(self.session, path)
                self.statusBar().showMessage(f"已导出人工标签：{path}")
            except OSError as error:
                self.report_error(str(error))

    def report_error(self, message):
        self.statusBar().showMessage("操作未完成：" + message)
        QMessageBox.warning(self, "操作未完成", message)

    def _job_error(self, message):
        self.statusBar().showMessage("操作未完成：" + message)
        if self.session and self.session.candidate != self.session.review.processing:
            self.processing_label.setText(
                "候选预览不可用：" + message + "\n可取消候选预览，保留已有工作状态。"
            )

    def closeEvent(self, event):
        if not self._autosave():
            event.ignore()
            return
        self.render_timer.stop()
        self.autosave_timer.stop()
        self.jobs.invalidate()
        self.network_jobs.invalidate()
        if self.jobs.active is not None or self.network_jobs.active is not None:
            event.ignore()
            self.hide()
            QTimer.singleShot(100, self.close)
            return
        event.accept()
