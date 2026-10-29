import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal


class Waveform(pg.PlotWidget):
    epoch_selected = Signal(int)
    range_selected = Signal(float, float)

    def __init__(self):
        super().__init__(background="#fafbfd")
        self.setMinimumHeight(300)
        self.getPlotItem().setMenuEnabled(False)
        for name in ("left", "bottom"):
            axis = self.getAxis(name)
            axis.setTextPen("#536170")
            axis.setPen("#cbd3dc")
        self.showGrid(x=False, y=True, alpha=0.12)
        self.getAxis("left").setWidth(60)
        self.getAxis("left").setStyle(hideOverlappingLabels=False)
        self.setMouseEnabled(x=True, y=False)
        self.curves = []
        self.reference_curves = []
        self.regions = []
        self.titles = []
        self.zero_lines = []
        self.first_epoch = 0
        self.span = 1.0
        self.page_count = 0
        self.time_regions = []
        self.selection = pg.LinearRegionItem(
            (2, 4), brush=pg.mkBrush(35, 158, 184, 25), pen=pg.mkPen("#269eb0", width=1)
        )
        self.selection.setZValue(8)
        self.selection.sigRegionChangeFinished.connect(
            lambda: self.range_selected.emit(*self.selection.getRegion())
        )
        self.addItem(self.selection)
        self.selection.hide()
        self.last_continuous_window = None
        self.scene().sigMouseClicked.connect(self._clicked)

    def _clicked(self, event):
        if event.button().value != 1 or not self.sceneBoundingRect().contains(event.scenePos()):
            return
        x = self.getViewBox().mapSceneToView(event.scenePos()).x()
        index = int(np.floor(x / self.span))
        if 0 <= index < self.page_count:
            self.epoch_selected.emit(self.first_epoch + index)

    def display(
        self,
        ds,
        first,
        channels,
        values,
        reference,
        rejected,
        amplitude_uv=50.0,
        *,
        continuous_start=0,
        intervals=(),
    ):
        count, nchan, samples = values.shape
        self.first_epoch = first
        self.page_count = count
        self.span = samples / ds.sfreq
        self.selection.setVisible(ds.continuous)
        if ds.continuous and self.last_continuous_window != (ds.data_id, continuous_start, samples):
            self.selection.setBounds((0, self.span))
            self.selection.setRegion((self.span * 0.25, self.span * 0.5))
            self.last_continuous_window = (ds.data_id, continuous_start, samples)
        visible_ranges = [
            (
                max(0, (d.start - continuous_start) / ds.sfreq),
                min(self.span, (d.stop - continuous_start) / ds.sfreq),
            )
            for d in intervals
            if d.stop > continuous_start and d.start < continuous_start + samples
        ]
        while len(self.time_regions) < len(visible_ranges):
            region = pg.LinearRegionItem(
                movable=False, pen=pg.mkPen(None), brush=pg.mkBrush(239, 116, 110, 40)
            )
            region.setZValue(-15)
            self.addItem(region)
            self.time_regions.append(region)
        for i, region in enumerate(self.time_regions):
            region.setVisible(i < len(visible_ranges))
            if i < len(visible_ranges):
                region.setRegion(visible_ranges[i])
        height = amplitude_uv
        top = nchan * height
        while len(self.curves) < nchan:
            self.reference_curves.append(self.plot(pen=pg.mkPen("#a4aeba", width=1)))
            self.curves.append(self.plot(pen=pg.mkPen("#2e5368", width=1)))
            for curve in (self.curves[-1], self.reference_curves[-1]):
                curve.setDownsampling(auto=True, method="peak")
                curve.setClipToView(True)
        x = np.concatenate(
            [np.r_[np.arange(samples) / ds.sfreq + i * self.span, np.nan] for i in range(count)]
        )
        for row, curve in enumerate(self.curves):
            if row >= nchan:
                curve.setData([], [])
                self.reference_curves[row].setData([], [])
                continue
            offset = (nchan - 1 - row) * height
            y = np.concatenate([np.r_[v, np.nan] for v in values[:, row] * 1e6]) + offset
            curve.setData(x, y)
            if reference is not None:
                ref = np.concatenate([np.r_[v, np.nan] for v in reference[:, row] * 1e6]) + offset
                self.reference_curves[row].setData(x, ref)
            else:
                self.reference_curves[row].setData([], [])
        while len(self.regions) < count:
            region = pg.LinearRegionItem(movable=False, pen=pg.mkPen(None))
            region.setZValue(-20)
            self.addItem(region)
            self.regions.append(region)
            label = pg.TextItem(color="#4b6070", anchor=(0.5, 0.5))
            self.addItem(label)
            self.titles.append(label)
            line = pg.InfiniteLine(pen=pg.mkPen("#bdc9d2", style=pg.QtCore.Qt.PenStyle.DashLine))
            line.setZValue(-10)
            self.addItem(line)
            self.zero_lines.append(line)
        for i, region in enumerate(self.regions):
            visible = i < count
            region.setVisible(visible)
            self.titles[i].setVisible(visible)
            self.zero_lines[i].setVisible(visible)
            if visible:
                region.setRegion((i * self.span, (i + 1) * self.span))
                bad = first + i in rejected
                region.setBrush(pg.mkBrush(239, 116, 110, 32) if bad else pg.mkBrush(0, 0, 0, 0))
                title = f"{first + i + 1:03d}" + (" · 已标记" if bad else "")
                self.titles[i].setText(title if not ds.continuous else "连续数据 · 秒")
                self.titles[i].setPos((i + 0.5) * self.span, top - 0.12 * height)
                self.zero_lines[i].setValue(i * self.span - ds.tmin)
        self.getAxis("left").setTicks(
            [[((nchan - 1 - i) * height, ds.labels[ch]) for i, ch in enumerate(channels)]]
        )
        ticks = []
        if ds.continuous:
            ticks = [(i, str(round(continuous_start / ds.sfreq + i, 1))) for i in range(round(self.span) + 1)]
        else:
            for i in range(count):
                for offset in (0.0, -ds.tmin, -ds.tmin + 0.5):
                    if 0 <= offset < self.span:
                        ticks.append((i * self.span + offset, f"{(ds.tmin + offset) * 1000:.0f}"))
        self.getAxis("bottom").setTicks([ticks])
        self.setLabel("bottom", "时间 (s)" if ds.continuous else "相对事件时间 (ms)", color="#536170")
        self.setXRange(0, count * self.span, padding=0.006)
        self.setYRange(-0.6 * height, top + 0.3 * height, padding=0)
