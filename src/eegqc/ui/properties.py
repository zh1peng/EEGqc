"""EEGLAB-style property composition using the existing EEGPrep numeric outputs."""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget


class SignalProperties(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.heading = QLabel("Select a channel or component")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        grid = QGridLayout()
        self.map = QLabel()
        self.map.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map.setFixedSize(135, 135)
        grid.addWidget(self.map, 0, 0)
        self.spectrum = self._plot("Power spectrum", "Frequency", "Hz")
        self.spectrum_curve = self.spectrum.plot(pen=pg.mkPen("#197b91", width=1.4))
        grid.addWidget(self.spectrum, 0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid, 2)
        self.image = self._plot("Single-trial activity", "Time", "ms")
        self.image.setLabel("left", "Trial")
        self.image.getViewBox().invertY(True)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.image_item.setLookupTable(pg.colormap.get("CET-D1").getLookupTable())
        self.image.addItem(self.image_item)
        layout.addWidget(self.image, 3)
        self.scale = QLabel()
        self.scale.setWordWrap(True)
        self.scale.setObjectName("muted")
        layout.addWidget(self.scale)
        self.average = self._plot("Trial average", "Time", "ms")
        self.average_curve = self.average.plot(pen=pg.mkPen("#197b91", width=1.5))
        layout.addWidget(self.average, 2)
        self.context = QLabel()
        self.context.setWordWrap(True)
        self.context.setObjectName("muted")
        layout.addWidget(self.context)

    @staticmethod
    def _plot(title, label, units):
        widget = pg.PlotWidget(background="white", title=title)
        widget.setLabel("bottom", label, units=units)
        widget.setMinimumHeight(120)
        widget.setMouseEnabled(x=False, y=False)
        widget.getPlotItem().setMenuEnabled(False)
        return widget

    def display(self, *, title, dataset, values, freqs, psd, location, units, context):
        self.heading.setText(title)
        self.context.setText(context)
        self.map.clear()
        if location is None:
            self.map.setText("No electrode locations")
        else:
            image = QImage(
                location.data,
                location.shape[1],
                location.shape[0],
                location.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy()
            self.map.setPixmap(
                QPixmap.fromImage(image).scaled(
                    135, 135, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
        self.spectrum.setLabel("left", "PSD", units="dB µV²/Hz" if units == "µV" else "dB (projection)")
        self.spectrum_curve.setData(freqs, psd)
        self.spectrum.setXRange(0, min(100, dataset.sfreq / 2), padding=0)
        self.image.setVisible(not dataset.continuous)
        self.scale.setVisible(not dataset.continuous)
        self.average.setTitle("Activity" if dataset.continuous else "Trial average")
        self.average.setLabel("left", "Amplitude", units=units)
        if dataset.continuous:
            self.average.setLabel("bottom", "Time", units="s")
            self.average_curve.setData(dataset.times, values[0])
        else:
            times = dataset.times * 1000
            bound = max(float(np.percentile(np.abs(values), 98)), np.finfo(float).eps)
            self.image_item.setImage(values, levels=(-bound, bound))
            from PySide6.QtCore import QRectF

            self.image_item.setRect(
                QRectF(times[0], 0.5, values.shape[1] * 1000 / dataset.sfreq, len(values))
            )
            self.image.setXRange(times[0], times[-1], padding=0)
            self.image.setYRange(0.5, len(values) + 0.5, padding=0)
            self.scale.setText(
                f"Blue −{bound:.2g}  ·  white 0  ·  red +{bound:.2g} {units}  |  Original trial order"
            )
            self.average.setLabel("bottom", "Time", units="ms")
            self.average_curve.setData(times, values.mean(axis=0))
            self.average.setXRange(times[0], times[-1], padding=0)
