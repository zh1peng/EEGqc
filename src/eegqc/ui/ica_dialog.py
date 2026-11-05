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
        self.setWindowTitle("Fit new ICA · Extended Infomax")
        self.resize(620, 640)
        self.prepared = None
        ds = session.dataset
        layout = QVBoxLayout(self)
        info = QLabel(
            "Input is frozen to the applied state at launch: "
            + description
            + "\nDisplay filtering is excluded from fitting. Review the result before applying it."
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
        layout.addWidget(QLabel("Fit channels (default: retained EEG channels, in original order)"))
        layout.addWidget(self.channels)
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItem(
            "Currently retained intervals" if ds.continuous else "Currently retained epochs", "retained"
        )
        self.scope.addItem("All samples", "all")
        form.addRow("Sample range", self.scope)
        self.dimensions = QSpinBox()
        self.dimensions.setRange(0, ds.n_channels)
        self.dimensions.setSpecialValueText("Use effective rank")
        form.addRow("Components / PCA Dimensions", self.dimensions)
        layout.addLayout(form)
        advanced_toggle = QCheckBox("Advanced settings")
        layout.addWidget(advanced_toggle)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        self.max_iter = QSpinBox()
        self.max_iter.setRange(1, 10000)
        self.max_iter.setValue(512)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setValue(0)
        advanced_form.addRow("Max iterations maxsteps", self.max_iter)
        advanced_form.addRow("Random seed", self.seed)
        advanced.setVisible(False)
        advanced_toggle.toggled.connect(advanced.setVisible)
        layout.addWidget(advanced)
        self.summary = QLabel("Check effective rank, channel count and sample count before fitting.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.prepare_button = QPushButton("Check input and rank")
        self.prepare_button.clicked.connect(self.prepare_requested)
        self.fit_button = QPushButton("Start fitting")
        self.fit_button.setEnabled(False)
        self.fit_button.clicked.connect(lambda: self.fit_requested.emit(self.prepared))
        close = QPushButton("Close")
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
        self.summary.setText("Input or parameters changed. Check effective rank again.")

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
            f"Frozen input: {len(prepared.channels)} channels × {prepared.sample_count:,} samples\nEffective rank {prepared.rank}; Fit {n_components} components. Algorithm: EEGPrep Extended Infomax."
        )
        self.fit_button.setEnabled(True)
