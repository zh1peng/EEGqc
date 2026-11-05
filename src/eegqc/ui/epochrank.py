"""Aligned, independent epoch assistance indicators below the navigation rail."""

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from eegqc.application.epochrank import FEATURE_VERSION, epoch_vectors, feedback, rank_epochs
from eegqc.epochlabel.model import predict_epochs
from eegqc.ui.epoch_rail import EpochRail
from eegqc.ui.jobs import Jobs


class AssistanceRail(EpochRail):
    """The same epoch coordinates and scrub behavior as the main rail."""

    def __init__(self):
        super().__init__()
        self.setAccessibleName("Epoch assistance indicator")
        self.setFixedHeight(74)
        self.values = np.empty(0)
        self.markers = set()
        self.reviewed = set()
        self.tooltips = {}
        self.threshold = None

    def track(self):
        return QRectF(8, 7, max(1, self.viewport().width() - 16), 54)

    def reset_scores(self, count):
        self.clear()
        self.addItems([""] * count)
        self.values = np.full(count, np.nan)
        self.markers, self.reviewed, self.tooltips = set(), set(), {}
        self.threshold = None
        self.viewport().update()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor("#ffffff"))
        if not self.count():
            return
        rect = self.track()
        step = rect.width() / self.count()
        painter.fillRect(rect, QColor("#f4f7fa"))
        painter.fillRect(
            QRectF(
                rect.left() + self.first_visible * step,
                rect.top(),
                min(5, self.count() - self.first_visible) * step,
                rect.height(),
            ),
            QColor("#deeff0"),
        )
        finite = self.values[np.isfinite(self.values)]
        if len(finite):
            low, high = min(0.0, float(finite.min())), float(finite.max())
            if self.threshold is not None:
                low, high = min(low, self.threshold), max(high, self.threshold)
            high = max(high, low + 1e-9)

            def point(index, value):
                return QPointF(
                    rect.left() + (index + 0.5) * step,
                    rect.bottom() - (value - low) / (high - low) * (rect.height() - 6) - 3,
                )

            if self.threshold is not None:
                y = point(0, self.threshold).y()
                painter.setPen(QPen(QColor("#bc883d"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            connected = False
            for epoch, value in enumerate(self.values):
                if not np.isfinite(value):
                    connected = False
                    continue
                p = point(epoch, value)
                path.lineTo(p) if connected else path.moveTo(p)
                connected = True
            painter.setPen(QPen(QColor("#587ca5"), 1.3))
            painter.drawPath(path)
            for epoch, value in enumerate(self.values):
                if not np.isfinite(value):
                    continue
                color = (
                    "#bdc7d3" if epoch in self.reviewed else "#d88b35" if epoch in self.markers else "#587ca5"
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                radius = 3 if epoch in self.markers else 1.6
                painter.drawEllipse(point(epoch, value), radius, radius)
            painter.setPen(QColor("#68798b"))
            painter.drawText(
                rect.adjusted(3, 0, -3, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                f"{low:.2g} … {high:.2g}",
            )
        x = rect.left() + (self.position + 0.5) * step
        painter.setPen(QPen(QColor("#078b98"), 1.5))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

    def _show_hover(self):
        if self.hover_index is not None and self.hover_index < self.count() and not self.dragging:
            detail = self.tooltips.get(self.hover_index, "No current suggestion")
            QToolTip.showText(self.hover_point, f"Epoch {self.hover_index + 1} · {detail}", self)


class AssistanceTrack(QWidget):
    def __init__(self, panel, method):
        super().__init__()
        self.panel, self.window = panel, panel.window
        self.job_key = object()
        self._request = ()
        self._scores_cache = None
        self.ranking = None
        self.suggestions = {}
        self.model = None
        self.model_revision = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        controls = QHBoxLayout()
        self.method = QComboBox()
        for title, key in (
            ("Amplitude", "amplitude"),
            ("EpochRank · similarity", "similarity"),
            ("EpochRank · feedback", "classifier"),
            ("EpochLabel · local model", "epochlabel"),
        ):
            self.method.addItem(title, key)
        self.method.setCurrentIndex(self.method.findData(method))
        self.method.setMaximumWidth(220)
        controls.addWidget(self.method)
        self.limit = QDoubleSpinBox()
        self.limit.setRange(1, 10000)
        self.limit.setValue(100)
        self.limit.setDecimals(0)
        self.limit.setSuffix(" µV")
        self.limit.setKeyboardTracking(False)
        self.limit.setToolTip("Absolute amplitude threshold; all retained channels, before display filtering")
        controls.addWidget(self.limit)
        self.load_button = QPushButton("Load model…")
        self.load_button.clicked.connect(self.load_model)
        controls.addWidget(self.load_button)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        controls.addWidget(self.status, 1)
        remove = QPushButton("×")
        remove.setToolTip("Remove this assistance track")
        remove.clicked.connect(lambda: panel.remove_track(self))
        controls.addWidget(remove)
        layout.addLayout(controls)
        self.rail = AssistanceRail()
        self.rail.itemClicked.connect(lambda item: self.select_epoch(self.rail.row(item)))
        layout.addWidget(self.rail)
        self.method.currentIndexChanged.connect(self.refresh)
        self.limit.valueChanged.connect(self.refresh)

    def message(self, text):
        self.status.setText(text)
        self.status.setToolTip(text)

    def refresh(self, *_):
        s = self.window.session
        self.rail.set_position(self.window.current_epoch, self.window.first_epoch)
        method = self.method.currentData()
        self.limit.setVisible(method == "amplitude")
        self.load_button.setVisible(method == "epochlabel")
        request = (
            (
                s,
                s.review.processing,
                s.review.decisions,
                s.candidate,
                method,
                self.limit.value(),
                self.model_revision,
            )
            if s
            else None
        )
        if request == self._request:
            return
        self._request = request
        self.panel.jobs.invalidate(self.job_key)
        self.ranking, self.suggestions = None, {}
        self.rail.reset_scores(s.dataset.n_epochs if s and not s.dataset.continuous else 0)
        if s is None or s.dataset.continuous:
            self.message("Open epoched data to use assistance.")
            return
        if s.candidate != s.review.processing:
            self.message("Paused · apply or reset the processing preview.")
            return
        labels = feedback(s.review)
        if method in ("similarity", "classifier") and not labels.bad:
            self.message("Reject at least one signal-artifact epoch to start.")
            return
        if method == "classifier" and not labels.good:
            self.message("Also confirm one good epoch to learn from both classes.")
            return
        if method == "epochlabel" and self.model is None:
            self.message("Load a local model; no pretrained EpochLabel model is bundled.")
            return
        processing, model = s.review.processing, self.model
        threshold = self.limit.value()
        cache_key = (s, processing, method, self.model_revision)
        self.message("Updating…")

        def compute():
            if method in ("similarity", "classifier"):
                return rank_epochs(self.panel.vectors(s, processing), labels, method)
            if self._scores_cache is None or self._scores_cache[0] != cache_key:
                if method == "amplitude":
                    channels = [i for i in range(s.dataset.n_channels) if i not in processing.dropped]
                    if not channels:
                        raise ValueError("No retained channels for amplitude assistance")
                    scores = np.concatenate(
                        [
                            np.max(
                                np.abs(s.engine.signal(processing, slice(i, i + 16), channels)), axis=(1, 2)
                            )
                            * 1e6
                            for i in range(0, s.dataset.n_epochs, 16)
                        ]
                    )
                else:
                    scores = predict_epochs(s, model, processing=processing)
                self._scores_cache = (cache_key, scores)
            return self._scores_cache[1]

        def done(result):
            self.rail.reviewed = set(labels.reviewed)
            if method in ("similarity", "classifier"):
                self.ranking = result
                self.rail.values[result.epochs] = (
                    1 / (1 + result.scores) if method == "similarity" else result.scores
                )
                self.rail.markers = set(result.epochs[:1])
                self.rail.threshold = 0 if method == "classifier" else None
                for epoch, score, nearest in zip(result.epochs, result.scores, result.nearest_bad):
                    self.suggestions[int(epoch)] = (
                        f"EpochRank:{FEATURE_VERSION}:{method}:score={score:.6g};nearest_bad={nearest};"
                        f"bad={labels.bad};good={labels.good};processing={processing}"
                    )
                    self.rail.tooltips[int(epoch)] = (
                        f"{'Distance' if method == 'similarity' else 'Decision score'} {score:.3g} · nearest rejected epoch {nearest + 1}"
                    )
                detail = (
                    "similarity = 1 / (1 + distance)"
                    if method == "similarity"
                    else "decision score, not probability"
                )
                self.message(f"{detail} · orange = next suggestion · {len(result.epochs)} unreviewed")
            else:
                self.rail.values = np.asarray(result)
                self.rail.threshold = threshold if method == "amplitude" else 0.5
                candidates = np.flatnonzero(result > threshold if method == "amplitude" else result >= 0.5)
                self.rail.markers = set(candidates) - self.rail.reviewed
                for epoch, score in enumerate(result):
                    detail = (
                        f"Peak |amplitude| {score:.2f} µV"
                        if method == "amplitude"
                        else f"Model probability {score:.3f}"
                    )
                    self.rail.tooltips[epoch] = detail + (
                        " · reviewed" if epoch in self.rail.reviewed else ""
                    )
                    if epoch not in self.rail.reviewed:
                        self.suggestions[epoch] = (
                            f"amplitude_threshold:{threshold:g}uV;peak={score:.6g}uV;processing={processing}"
                            if method == "amplitude"
                            else f"EpochLabel-logistic-baseline:{model.get('model_id', 'unversioned')}:p={score:.4f};processing={processing}"
                        )
                self.message(
                    f"{'Peak |µV|' if method == 'amplitude' else 'Model probability'} · orange = above threshold · {len(self.rail.markers)} unreviewed"
                )
            self.rail.viewport().update()

        self.panel.jobs.submit(self.job_key, compute, done)

    def select_epoch(self, epoch):
        s = self.window.session
        if s is None:
            return
        suggestion = self.suggestions.get(epoch)
        if suggestion and suggestion not in s.suggestions.get(epoch, ()):
            s.suggestions[epoch] = (*s.suggestions.get(epoch, ()), suggestion)
        self.window.navigator.choose(epoch)

    def load_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose local EpochLabel model", "", "JSON (*.json)")
        if path:
            try:
                self.model = json.loads(Path(path).read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                self.message(str(error))
                return
            self.model_revision += 1
            self.load_button.setToolTip(path)
            self.refresh()


class EpochAssistance(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.jobs = Jobs(self)
        self.jobs.failed_job.connect(self._failed)
        self._cache = None
        self.tracks = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        heading = QHBoxLayout()
        label = QLabel("Epoch assistance · suggestions only")
        label.setObjectName("muted")
        label.setToolTip(
            "All retained channels; no display filter. Each track has an independent scale. Click to inspect; only waveform decisions create labels."
        )
        heading.addWidget(label, 1)
        add = QPushButton("+ Indicator")
        add.clicked.connect(lambda: self.add_track())
        heading.addWidget(add)
        layout.addLayout(heading)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        body = QWidget()
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll)
        self.hide()

    def vectors(self, session, processing):
        key = (session.dataset.data_id, processing)
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, epoch_vectors(session.engine, processing))
        return self._cache[1]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._align_navigation)

    def _align_navigation(self):
        self.window.navigator.setViewportMargins(0, 0, self.scroll.verticalScrollBar().width(), 0)

    def add_track(self, method="amplitude"):
        track = AssistanceTrack(self, method)
        self.tracks.append(track)
        self.rows.addWidget(track)
        self._resize_tracks()
        self.show()
        track.refresh()
        return track

    def remove_track(self, track):
        self.jobs.invalidate(track.job_key)
        self.tracks.remove(track)
        self.rows.removeWidget(track)
        track.hide()
        track.deleteLater()
        self._resize_tracks()
        self.setVisible(bool(self.tracks))

    def _resize_tracks(self):
        for track in self.tracks:
            track.setFixedHeight(112)
        self.scroll.setFixedHeight(min(2, len(self.tracks)) * 116)

    def refresh(self):
        for track in self.tracks:
            track.refresh()
        self.setVisible(
            bool(self.tracks) and (self.window.session is None or not self.window.session.dataset.continuous)
        )

    def _failed(self, key, message):
        for track in self.tracks:
            if track.job_key is key:
                track.message("Calculation failed: " + message)
