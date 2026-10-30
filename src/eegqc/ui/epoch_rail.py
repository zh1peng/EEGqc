"""A scrub rail backed by the same epoch selection model as QC actions."""

from PySide6.QtCore import QItemSelectionModel, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QToolTip


class EpochRail(QListWidget):
    def __init__(self):
        super().__init__()
        self.setAccessibleName("Epoch 检查滑轨")
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(48)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.position = 0
        self.first_visible = 0
        self.anchor = 0
        self.dragging = False
        self.range_drag = False
        self.wheel_distance = 0

    def set_position(self, position, first_visible):
        if position != self.position and not self.dragging:
            self.anchor = position
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
        colors = {"已拒绝": "#f28691", "已确认保留": "#a5cfc5", "待复核": "#e8ca87"}
        for index in range(self.count()):
            item = self.item(index)
            color = colors.get(item.data(Qt.ItemDataRole.UserRole), "#e4e9ef")
            rect = QRectF(track.left() + index * width, track.top(), max(1, width - 1), track.height())
            painter.fillRect(rect, QColor(color))
            if item.isSelected():
                painter.fillRect(rect, QColor(0, 139, 152, 55))
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

    def choose(self, index, *, extend=False, toggle=False):
        if not self.count():
            return
        index = max(0, min(self.count() - 1, index))
        self.setCurrentRow(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        if extend:
            self.clearSelection()
            for i in range(min(self.anchor, index), max(self.anchor, index) + 1):
                self.item(i).setSelected(True)
        elif toggle:
            self.item(index).setSelected(not self.item(index).isSelected())
            self.anchor = index
        else:
            self.clearSelection()
            self.item(index).setSelected(True)
            self.anchor = index
        self.position = index
        self.viewport().update()
        self.itemClicked.emit(self.item(index))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.count():
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.dragging = True
        self.range_drag = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.choose(
            self.epoch_at(event.position().x()),
            extend=self.range_drag,
            toggle=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
        )

    def mouseMoveEvent(self, event):
        if not self.count():
            return
        index = self.epoch_at(event.position().x())
        if self.dragging:
            if index != self.position:
                self.choose(index, extend=self.range_drag)
        else:
            state = self.item(index).data(Qt.ItemDataRole.UserRole) or "未检查"
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{state} · {(index + 1) / self.count():.0%}  |  拖动浏览 · Shift 选择范围",
                self,
            )

    def mouseReleaseEvent(self, event):
        if self.dragging:
            index = self.epoch_at(event.position().x())
            if index != self.position:
                self.choose(index, extend=self.range_drag)
        self.dragging = False

    def wheelEvent(self, event):
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
            self.choose(
                targets[event.key()], extend=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            )
        else:
            super().keyPressEvent(event)
