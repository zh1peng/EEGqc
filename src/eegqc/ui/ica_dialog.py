"""A single Extended Infomax fit, with an explicit input/rank preview."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ICAFitDialog(QDialog):
    prepare_requested = Signal()
    fit_requested = Signal(object)

    def __init__(self, session, description, parent=None):
        super().__init__(parent)
        self.setWindowTitle("拟合新 ICA · Extended Infomax")
        self.resize(620, 640)
        self.prepared = None
        ds = session.dataset
        layout = QVBoxLayout(self)
        info = QLabel(
            "输入固定为启动时的已采用状态："
            + description
            + "\n显示滤波不参与拟合。完成后仅生成候选，由您决定采用。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.channels = QListWidget()
        self.channels.setMaximumHeight(220)
        for i, name in enumerate(ds.labels):
            loc = ds.chanlocs[i] if i < len(ds.chanlocs) else {}
            eeg = str(loc.get("type", "EEG")).upper() in ("", "EEG")
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if eeg and i not in session.review.processing.dropped
                else Qt.CheckState.Unchecked
            )
            self.channels.addItem(item)
        layout.addWidget(QLabel("拟合通道（默认保留的 EEG 通道，按原始通道顺序）"))
        layout.addWidget(self.channels)
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItem("当前未拒绝的时间段" if ds.continuous else "当前未拒绝的 epochs", "retained")
        self.scope.addItem("全部样本", "all")
        form.addRow("样本范围", self.scope)
        self.dimensions = QSpinBox()
        self.dimensions.setRange(0, ds.n_channels)
        self.dimensions.setSpecialValueText("使用有效秩")
        form.addRow("成分数 / PCA 维度", self.dimensions)
        layout.addLayout(form)
        advanced_toggle = QCheckBox("高级参数")
        layout.addWidget(advanced_toggle)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        self.max_iter = QSpinBox()
        self.max_iter.setRange(1, 10000)
        self.max_iter.setValue(512)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setValue(0)
        advanced_form.addRow("最大迭代 maxsteps", self.max_iter)
        advanced_form.addRow("随机种子", self.seed)
        advanced.setVisible(False)
        advanced_toggle.toggled.connect(advanced.setVisible)
        layout.addWidget(advanced)
        self.summary = QLabel("先检查所选输入的有效秩、通道数和样本数，再启动拟合。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.prepare_button = QPushButton("检查输入与有效秩")
        self.prepare_button.clicked.connect(self.prepare_requested)
        self.fit_button = QPushButton("启动拟合")
        self.fit_button.setEnabled(False)
        self.fit_button.clicked.connect(lambda: self.fit_requested.emit(self.prepared))
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        for widget in (self.prepare_button, self.fit_button, close):
            row.addWidget(widget)
        layout.addLayout(row)
        self.channels.itemChanged.connect(self.invalidate)
        self.scope.currentIndexChanged.connect(self.invalidate)
        for spin in (self.dimensions, self.max_iter, self.seed):
            spin.valueChanged.connect(self.invalidate)

    def invalidate(self, *_):
        self.prepared = None
        self.fit_button.setEnabled(False)
        self.summary.setText("输入或参数已更改，请检查有效秩。")

    def parameters(self):
        return dict(
            channels=tuple(
                self.channels.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.channels.count())
                if self.channels.item(i).checkState() == Qt.CheckState.Checked
            ),
            sample_scope=self.scope.currentData(),
            n_components=self.dimensions.value() or None,
            max_iter=self.max_iter.value(),
            seed=self.seed.value(),
        )

    def show_prepared(self, prepared):
        self.prepared = prepared
        n_components = self.dimensions.value() or prepared.rank
        self.summary.setText(
            f"已固定输入：{len(prepared.channels)} 通道 × {prepared.sample_count:,} 样本\n有效秩 {prepared.rank}；拟合 {n_components} 个成分。算法：EEGPrep Extended Infomax。"
        )
        self.fit_button.setEnabled(True)
