from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from eegqc.application.epochrank import FEATURE_VERSION, epoch_vectors, feedback, rank_epochs
from eegqc.ui.jobs import Jobs
from eegqc.ui.waveform import Waveform


class EpochRankDock(QDockWidget):
    def __init__(self, window):
        super().__init__("EpochRank · 推荐检查", window)
        self.setObjectName("epochrank")
        self.window = window
        self.jobs = Jobs(self)
        self.jobs.failed.connect(self._failed)
        self._request = ()
        self._cache = None
        self.ranking = None
        self._advance = None
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self.method = QComboBox()
        self.method.addItem("相似坏样本 · 距离排序", "similarity")
        self.method.addItem("累计好坏反馈 · 逻辑回归", "classifier")
        layout.addWidget(self.method)
        hint = QLabel("从一个坏样本开始，随人工反馈更新推荐。")
        hint.setObjectName("muted")
        hint.setToolTip("试验功能：使用全部保留 EEG 通道，不随显示滤波改变；推荐不自动写入标签。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.items = QListWidget()
        self.items.setObjectName("rankList")
        self.items.setMinimumHeight(140)
        self.items.itemClicked.connect(self._select)
        self.items.itemActivated.connect(self._select)
        layout.addWidget(self.items)
        row = QHBoxLayout()
        self.next_button = QPushButton("下一个推荐 / 跳过")
        self.next_button.clicked.connect(self.advance_when_ready)
        row.addWidget(self.next_button)
        self.compare_button = QPushButton("对照相似坏样本")
        self.compare_button.clicked.connect(self.compare_nearest)
        row.addWidget(self.compare_button)
        layout.insertLayout(1, row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.setWidget(scroll)
        self.method.currentIndexChanged.connect(lambda: self.refresh())
        self.visibilityChanged.connect(self._visibility_changed)
        self.hide()

    def _visibility_changed(self, visible):
        self._request = ()
        if visible:
            self.refresh()
        else:
            self.jobs.invalidate()
            self._advance = None

    def refresh(self):
        if not self.isVisible():
            return
        session = self.window.session
        method = self.method.currentData()
        review = session.review if session else None
        request = (
            (session, review.processing, review.decisions, session.candidate, method) if session else None
        )
        if request == self._request:
            return
        self._request = request
        self.jobs.invalidate()
        self.ranking = None
        self.items.setEnabled(False)
        self.next_button.setEnabled(False)
        self.compare_button.setEnabled(False)
        if session is None or session.dataset.continuous:
            self.items.clear()
            self.status.setText("请打开分段数据；EpochRank 当前只针对 epochs。")
            return
        if session.candidate != review.processing:
            self.items.clear()
            self._advance = None
            self.status.setText("候选预览中：采用或取消候选后继续排序。")
            return
        labels = feedback(review)
        counts = f"当前状态：人工坏 {len(labels.bad)} · 人工好 {len(labels.good)}"
        if not labels.bad:
            self.items.clear()
            self.status.setText(
                counts + "\n请先标记至少一个信号伪迹样本。旧状态、不确定和行为剔除不参与训练。"
            )
            return
        if method == "classifier" and not labels.good:
            self.items.clear()
            self.status.setText(counts + "\n逻辑回归还需要至少一个人工确认的好样本。")
            return
        self.status.setText(counts + "\n正在更新检查顺序…")
        processing = review.processing
        cache_key = (session.dataset.data_id, processing)

        def compute():
            # Only the serial ranking worker accesses this one-state feature cache.
            if self._cache is None or self._cache[0] != cache_key:
                self._cache = (cache_key, epoch_vectors(session.engine, processing))
            return rank_epochs(self._cache[1], labels, method)

        def done(result):
            self.ranking = result
            scroll_position = self.items.verticalScrollBar().value()
            self.items.clear()
            self.items.setEnabled(True)
            self.next_button.setEnabled(bool(len(result.epochs)))
            self.compare_button.setEnabled(bool(len(result.epochs)))
            detail = "特征距离越小越相似" if method == "similarity" else "坏样本倾向分数越大越优先（未校准）"
            self.status.setText(counts + f" · 待检查 {len(result.epochs)}\n{detail}；排序仅供人工检查。")
            for position, (epoch, score, nearest) in enumerate(
                zip(result.epochs, result.scores, result.nearest_bad), 1
            ):
                item = QListWidgetItem(
                    f"{position:02d}   Epoch {epoch + 1:03d}\n       相似坏样本  {nearest + 1:03d}"
                )
                item.setToolTip(f"{detail}：{score:.3f}\n使用当前已采用状态的全部保留 EEG 通道。")
                suggestion = (
                    f"EpochRank:{FEATURE_VERSION}:{method}:score={score:.6g};nearest_bad={nearest};"
                    f"bad={labels.bad};good={labels.good};processing={processing}"
                )
                item.setData(Qt.ItemDataRole.UserRole, (int(epoch), suggestion))
                item.setData(Qt.ItemDataRole.UserRole + 1, int(nearest))
                self.items.addItem(item)
                if epoch == self.window.current_epoch:
                    self.items.setCurrentItem(item)
            self.items.verticalScrollBar().setValue(scroll_position)
            if self._advance is not None:
                context = self._advance
                self._advance = None
                if context == (
                    self.window.session,
                    self.window.session.review.processing,
                    self.window.current_epoch,
                ):
                    self.advance_when_ready()

        self.jobs.submit("rank", compute, done)

    def _select(self, item):
        epoch, suggestion = item.data(Qt.ItemDataRole.UserRole)
        session = self.window.session
        seen = session.suggestions.get(epoch, ())
        if suggestion not in seen:
            session.suggestions[epoch] = (*seen, suggestion)
        self.window.navigator.clearSelection()
        self.window.navigator.item(epoch).setSelected(True)
        self.window.workspace_buttons.button(0).click()
        self.window.select_epoch(epoch)

    def advance_when_ready(self):
        window = self.window
        if window.session is None:
            return
        if self.ranking is None:
            self._advance = (window.session, window.session.review.processing, window.current_epoch)
            return
        count = self.items.count()
        position = next(
            (
                i
                for i in range(count)
                if self.items.item(i).data(Qt.ItemDataRole.UserRole)[0] == window.current_epoch
            ),
            -1,
        )
        for offset in range(1, count + 1):
            index = (position + offset) % count
            item = self.items.item(index)
            if item.data(Qt.ItemDataRole.UserRole)[0] != window.current_epoch:
                self.items.setCurrentItem(item)
                self.items.scrollToItem(item)
                self._select(item)
                return
        window.statusBar().showMessage("暂无其他推荐，可继续原始顺序检查。")

    def compare_nearest(self):
        if self.ranking is None or not self.items.count():
            return
        item = self.items.currentItem() or self.items.item(0)
        self._select(item)
        epoch = item.data(Qt.ItemDataRole.UserRole)[0]
        nearest = item.data(Qt.ItemDataRole.UserRole + 1)
        window, session = self.window, self.window.session
        processing = session.review.processing
        ds = session.dataset
        start = window.channel_start.value() - 1
        count = int(window.channel_count.currentText().split()[0])
        channels = list(range(start, min(start + count, ds.n_channels)))
        display_filter = window.display_filter
        amplitude = float(window.amplitude.currentText().split()[0])

        def done(values):
            if window.session is not session or session.review.processing != processing:
                return
            dialog = QDialog(window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.setWindowTitle(f"Epoch {epoch + 1} 与相似坏样本 {nearest + 1}")
            dialog.resize(1000, 600)
            layout = QVBoxLayout(dialog)
            layout.addWidget(
                QLabel("相同通道、幅度与显示滤波；推荐顺序仍基于全部保留 EEG 通道的未显示滤波信号。")
            )
            for row, index in enumerate((epoch, nearest)):
                waveform = Waveform()
                waveform.display(
                    ds, index, channels, values[row : row + 1], None, session.review.rejected, amplitude
                )
                layout.addWidget(waveform)
            dialog.show()
            self.comparison_dialog = dialog

        self.jobs.submit(
            "compare",
            lambda: session.engine.signal(
                processing, epochs=[epoch, nearest], channels=channels, display_filter=display_filter
            ),
            done,
        )

    def _failed(self, message):
        self.status.setText("EpochRank 计算失败：" + message)
