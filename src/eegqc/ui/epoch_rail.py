"""A scrub rail backed by the same epoch selection model as QC actions."""

from PySide6.QtCore import QEvent, QItemSelectionModel, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QToolTip


class EpochRail(QListWidget):
    def __init__(self):
        super().__init__()
        self.setAccessibleName("Epoch review rail")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(48)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.position = 0
        self.first_visible = 0
        self.dragging = False
        self.wheel_distance = 0
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(650)
        self.hover_timer.timeout.connect(self._show_hover)
        self.hover_index = None
        self.hover_point = None

    def set_position(self, position, first_visible):
        self.position, self.first_visible = position, first_visible
        self.viewport().update()

    def track(self):
        return QRectF(8, 13, max(1, self.viewport().width() - 16), 22)

    def epoch_at(self, x):
        track = self.track()
        return max(0, min(self.count() - 1, int((x - track.left()) / track.width() * self.count())))

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor("#ffffff"))
        if not self.count():
            return
        track = self.track()
        width = track.width() / self.count()
        colors = {"Rejected": "#f28691", "Kept": "#a5cfc5", "Needs review": "#e8ca87"}
        for index in range(self.count()):
            item = self.item(index)
            color = colors.get(item.data(Qt.ItemDataRole.UserRole), "#e4e9ef")
            rect = QRectF(track.left() + index * width, track.top(), max(1, width - 1), track.height())
            painter.fillRect(rect, QColor(color))
        painter.setPen(QPen(QColor("#8bbfc4"), 1))
        painter.drawRect(
            QRectF(
                track.left() + self.first_visible * width,
                track.top() - 3,
                min(5, self.count() - self.first_visible) * width,
                track.height() + 6,
            )
        )
        x = track.left() + (self.position + 0.5) * width
        painter.setPen(QPen(QColor("#078b98"), 2))
        painter.setBrush(QColor(255, 255, 255, 160))
        painter.drawRoundedRect(QRectF(x - 3, track.top() - 5, 6, track.height() + 10), 2, 2)

    def choose(self, index):
        if not self.count():
            return
        index = max(0, min(self.count() - 1, index))
        self.setCurrentRow(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.position = index
        self.viewport().update()
        self.itemClicked.emit(self.item(index))

    def mousePressEvent(self, event):
        self._stop_hover()
        if event.button() != Qt.MouseButton.LeftButton or not self.count():
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.dragging = True
        self.choose(self.epoch_at(event.position().x()))

    def mouseMoveEvent(self, event):
        if not self.count():
            return
        index = self.epoch_at(event.position().x())
        if self.dragging:
            if index != self.position:
                self.choose(index)
        else:
            point = event.globalPosition().toPoint()
            if (
                index != self.hover_index
                or self.hover_point is None
                or (point - self.hover_point).manhattanLength() > 4
            ):
                QToolTip.hideText()
                self.hover_index, self.hover_point = index, point
                self.hover_timer.start()

    def _show_hover(self):
        if self.hover_index is not None and self.hover_index < self.count() and not self.dragging:
            state = self.item(self.hover_index).data(Qt.ItemDataRole.UserRole) or "Unreviewed"
            QToolTip.showText(self.hover_point, f"Epoch {self.hover_index + 1} · {state}", self)

    def _stop_hover(self):
        self.hover_timer.stop()
        self.hover_index = self.hover_point = None
        QToolTip.hideText()

    def leaveEvent(self, event):
        self._stop_hover()
        super().leaveEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            return True  # The custom rail geometry and dwell timer own hover hints.
        return super().viewportEvent(event)

    def clear(self):
        self._stop_hover()
        self.dragging = False
        self.wheel_distance = 0
        super().clear()

    def mouseReleaseEvent(self, event):
        if self.dragging:
            index = self.epoch_at(event.position().x())
            if index != self.position:
                self.choose(index)
        self.dragging = False

    def wheelEvent(self, event):
        self._stop_hover()
        delta = event.pixelDelta().x() or event.pixelDelta().y()
        threshold = 20 if delta else 120
        self.wheel_distance += delta or event.angleDelta().x() or event.angleDelta().y()
        steps = int(self.wheel_distance / threshold)
        if steps:
            self.wheel_distance -= steps * threshold
            self.choose(self.position - steps)
        event.accept()

    def keyPressEvent(self, event):
        targets = {
            Qt.Key.Key_Left: self.position - 1,
            Qt.Key.Key_Right: self.position + 1,
            Qt.Key.Key_PageUp: self.position - 5,
            Qt.Key.Key_PageDown: self.position + 5,
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: self.count() - 1,
        }
        if event.key() in targets:
            self.choose(targets[event.key()])
        else:
            super().keyPressEvent(event)
