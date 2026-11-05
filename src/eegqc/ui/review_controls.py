"""Contextual review controls and note editing for the desktop workflow."""

from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def action(text, callback):
    result = QPushButton(text)
    result.clicked.connect(callback)
    return result


class ReviewControls:
    def _build_candidate_bar(self):
        self.candidate_bar = QWidget()
        row = QHBoxLayout(self.candidate_bar)
        row.setContentsMargins(8, 6, 8, 6)
        self.candidate_summary = QLabel()
        self.candidate_summary.setWordWrap(True)
        row.addWidget(self.candidate_summary, 1)
        row.addWidget(action("与固定参考对比", self._preview_in_waveform))
        row.addWidget(action("采用", self.adopt))
        row.addWidget(action("取消", self.reset_preview))
        self.candidate_bar.setStyleSheet("background: #e8f4f4;")
        self.candidate_bar.hide()
        return self.candidate_bar

    def _preview_in_waveform(self):
        self.set_workspace(0)
        self.compare.setChecked(True)
        self.request_render()

    def _build_review_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.review_counts = QLabel()
        self.review_counts.setWordWrap(True)
        layout.addWidget(self.review_counts)
        self.review_export = QLabel()
        self.review_export.setWordWrap(True)
        layout.addWidget(self.review_export)
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        row = QHBoxLayout()
        row.addWidget(action("继续待复核项", lambda: self.next_unreviewed(pending=True)))
        row.addWidget(action("保存质控记录", self.save))
        export = action("导出清理数据 .set", self.export)
        export.setProperty("primary", True)
        row.addWidget(export)
        layout.addLayout(row)
        layout.addWidget(self.review_text, 1)
        return page

    def _refresh_review_page(self):
        s, ds = self.session, self.session.dataset
        if ds.continuous:
            intervals = sorted((d.start, d.stop) for d in s.review.active_intervals)
            removed, right = 0, 0
            for start, stop in intervals:
                removed += max(0, stop - max(start, right))
                right = max(right, stop)
            count = (
                f"连续数据 · 当前删除 {removed / ds.sfreq:.2f} s · 旧时间判断待复核 {s.pending_review_count}"
            )
            extent = f"{(ds.data.shape[2] - removed) / ds.sfreq:.2f} s"
        else:
            c = s.review.epoch_counts(ds.n_epochs)
            count = (
                f"当前已检查 {c['checked']} / {ds.n_epochs} · 保留 {c['retained']} · 剔除 {c['rejected']}\n"
                f"未检查 {c['unseen']} · 暂待复核 {c['deferred']} · 旧判断待复核 {c['stale']}"
            )
            extent = f"{ds.n_epochs - len(s.review.rejected)} epochs"
        self.review_counts.setText(count)
        self.review_export.setText(
            f"按已采用状态导出：{extent} × {ds.n_channels - len(s.review.processing.dropped)} 通道。\n"
            "未检查与未设为剔除的待复核数据会保留；旧判断不参与当前剔除。"
            + ("\n当前候选尚未采用，不会进入导出。" if s.candidate != s.review.processing else "")
        )
        self.review_text.setPlainText(
            "已采用的处理\n"
            + self._describe(s.review.processing)
            + f"\n\n数据集评级：{s.review.rating}\n备注：{s.review.note}\n\n"
            "保存质控记录用于继续工作；导出清理数据生成新的 .set 文件。\n"
            "下方 ERP 可比较整体 QC、相同 trials 的处理效果或 epoch 剔除效果。"
        )

    def _build_object_inspector(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        self.object_title = QLabel("选择检查对象")
        self.object_title.setObjectName("heading")
        self.object_detail = QLabel()
        self.object_detail.setWordWrap(True)
        layout.addWidget(self.object_title)
        layout.addWidget(self.object_detail)
        self.object_interpolate = QCheckBox("插值此通道 · 预览")
        self.object_drop = QCheckBox("从导出中排除此通道 · 预览")
        self.object_exclude = QCheckBox("排除此 IC · 预览")
        self.object_interpolate.toggled.connect(
            lambda value: self._object_channel_changed("interpolated", value)
        )
        self.object_drop.toggled.connect(lambda value: self._object_channel_changed("dropped", value))
        self.object_exclude.toggled.connect(lambda value: self._ic_changed(self.selected_ic, value))
        for widget in (self.object_interpolate, self.object_drop, self.object_exclude):
            layout.addWidget(widget)
        self.object_activity = pg.PlotWidget(title="IC 活动 · 当前 epoch")
        self.object_activity.setFixedHeight(160)
        self.object_activity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.object_activity.setLabel("bottom", "时间", units="s")
        self.object_spectrum = pg.PlotWidget(title="IC 投影功率谱")
        self.object_spectrum.setFixedHeight(160)
        self.object_spectrum.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.object_spectrum.setLabel("bottom", "频率", units="Hz")
        layout.addWidget(self.object_activity)
        layout.addWidget(self.object_spectrum)
        self.object_compare = action("与固定参考对比波形", self._preview_in_waveform)
        layout.addWidget(self.object_compare)
        layout.addStretch()
        return body

    def _object_channel_changed(self, field, value):
        index = max(0, self.channel_table.currentRow())
        values = set(getattr(self.session.candidate, field))
        values.add(index) if value else values.discard(index)
        self._propose(replace(self.session.candidate, **{field: tuple(sorted(values))}))
        self._populate_channel_table()

    def _sync_object_inspector(self):
        if self.session is None:
            return
        workspace = self.stack.currentIndex()
        self.object_stack.setCurrentIndex(0 if workspace == 0 else 1)
        channel, ic = workspace == 1, workspace == 2
        ica = self.session.ica_for()
        if ic and (ica is None or ica.decomposition_id != self.inspected_decomposition):
            self.selected_ic = None
        for widget in (self.object_interpolate, self.object_drop):
            widget.setVisible(channel)
        for widget in (self.object_exclude, self.object_activity, self.object_spectrum):
            widget.setVisible(ic and self.selected_ic is not None)
        self.object_compare.setVisible(channel or ic)
        s, ds = self.session, self.session.dataset
        if channel:
            index = max(0, self.channel_table.currentRow())
            self.object_title.setText(f"通道 {ds.labels[index]}")
            self.object_detail.setText("属性跟随当前预览。选中处理后，查看波形并采用。")
            self.object_interpolate.setEnabled(
                bool(self.channel_table.item(index, 1).flags() & Qt.ItemFlag.ItemIsUserCheckable)
            )
            for widget, checked in (
                (self.object_interpolate, index in s.candidate.interpolated),
                (self.object_drop, index in s.candidate.dropped),
            ):
                widget.blockSignals(True)
                widget.setChecked(checked)
                widget.blockSignals(False)
        elif ic:
            component = self.selected_ic
            ica = s.ica_for()
            if ica is None or component is None or component >= ica.count:
                self.selected_ic = None
                self.object_title.setText("IC 检查")
                self.object_detail.setText("点击卡片的“查看 IC 属性”查看活动、频谱与分类。")
                return
            self.object_title.setText(f"IC {component + 1:02d}")
            detail = "活动与分类基于此分解的拟合输入。"
            if s.iclabel and s.iclabel.get("decomposition_id") == ica.decomposition_id:
                probabilities = s.iclabel["probabilities"][component]
                index = int(np.argmax(probabilities))
                detail += f"\nICLabel：{s.iclabel['classes'][index]} {probabilities[index]:.0%}"
            self.object_detail.setText(detail)
            self.object_exclude.blockSignals(True)
            self.object_exclude.setChecked(component in s.candidate.excluded_ics)
            self.object_exclude.blockSignals(False)
        else:
            self.object_title.setText("结果复核")
            self.object_detail.setText(
                "核对当前有效判断、待复核项及导出范围。保存记录后可继续修改，导出会生成新的清理数据。"
            )

    def _selected_epochs(self):
        return tuple(self.navigator.row(item) for item in self.navigator.selectedItems()) or (
            self.current_epoch,
        )

    def clear_selected_decisions(self):
        if self.session is None or self.session.candidate != self.session.review.processing:
            return
        self._flush_note()
        self.session.clear_epoch_decisions(self._selected_epochs())
        self._changed()

    def _note_edited(self):
        if self.session is None or self.session.dataset.continuous:
            return
        self.pending_note = (self.session, self._selected_epochs(), self.epoch_note.toPlainText())
        self.note_timer.start(500)
        self.save_status.setText("备注编辑中…")

    def _flush_note(self):
        self.note_timer.stop()
        if self.pending_note is not None:
            session, epochs, note = self.pending_note
            self.pending_note = None
            session.set_epoch_notes(epochs, note)
            self.autosave_timer.start(700)
            self._sync_save_status()

    def _sync_note(self):
        if self.session.dataset.continuous:
            return
        s = self.session
        notes, decisions = dict(s.review.epoch_notes), dict(s.review.decisions)
        values = [notes.get(i, decisions[i].note if i in decisions else "") for i in self._selected_epochs()]
        text = values[0] if len(set(values)) == 1 else ""
        self.epoch_note.blockSignals(True)
        self.epoch_note.setPlainText(text)
        self.epoch_note.blockSignals(False)
        self.epoch_note.setPlaceholderText(
            "批量备注：编辑将应用到整个选区" if len(values) > 1 else "备注自动保存，不改变质量判断"
        )

    def _sync_save_status(self):
        s = self.session
        self.save_status.setText(
            ""
            if s is None
            else "尚未指定记录文件"
            if self.session_path is None
            else "记录已保存"
            if s.revision == s.saved_revision
            else "记录待保存…"
        )

    def next_unreviewed(self, pending=False):
        if self.session is None or self.session.dataset.continuous:
            return
        s, n = self.session, self.session.dataset.n_epochs
        decisions = dict(s.review.decisions)
        for offset in range(1, n + 1):
            index = (self.current_epoch + offset) % n
            decision = decisions.get(index)
            matches = (
                (
                    decision is not None
                    and (decision.processing != s.review.processing or decision.reason == "uncertain")
                )
                if pending
                else decision is None
            )
            if matches:
                self.set_workspace(0)
                self.navigator.choose(index)
                return
        self.statusBar().showMessage("没有待复核项" if pending else "没有未检查项")
