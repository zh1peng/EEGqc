from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from eegqc.adapters.algorithms import classify_ics, iclabel_candidates
from eegqc.application.identity import local_rater_id
from eegqc.core.conditions import Clause, Condition, normalize_condition
from eegqc.core.state import ASRConfig, DisplayFilter, Processing
from eegqc.ui.conditions import ConditionsDialog


class WorkflowActions:
    """User-facing scientific workflows, kept separate from the plotting window."""

    def configure_workflows(self):
        self.display_filter = DisplayFilter()
        self.interval_selection = None
        self.rater_id = local_rater_id()
        algorithms = self.algorithm_menu
        self._action(algorithms, "ICLabel 分类与选择…", self.run_iclabel)
        self._action(algorithms, "ASR 连续数据预览…", self.run_asr)
        self._action(algorithms, "EpochLabel 试验模型建议…", self.run_epochlabel)
        self._action(self.views_menu, "显示滤波（波形与 ERP）…", self.configure_display_filter)
        self._action(self.file_menu, "导出 EpochLabel 贡献包…", self.contribute)
        self._action(self.file_menu, "上传已检查的贡献包…", self.upload_contribution)
        self._action(self.file_menu, "撤回已上传贡献…", self.withdraw_contribution)

    def condition_queries(self):
        s, ds = self.session, self.session.dataset
        if self.use_custom_conditions.isChecked() and s.erp_conditions:
            return list(s.erp_conditions.items())
        field = self.condition_field.currentData()
        query = normalize_condition((field, self.condition_value.currentText()) if field else None)
        if self.split_conditions.isChecked():
            split = field or ("condition" if "condition" in ds.conditions else None)
            if split:
                return [
                    (str(value), Condition((Clause(split, "=", str(value)),)))
                    for value in sorted(set(ds.conditions[split].tolist()))
                ]
        return [(query.describe(), query)]

    def edit_conditions(self):
        if self.session is None or self.session.dataset.continuous:
            return
        dialog = ConditionsDialog(self.session.dataset, self.session.erp_conditions, self)
        if dialog.exec():
            self.session.erp_conditions = dialog.queries
            self.use_custom_conditions.setChecked(bool(dialog.queries))
            self.session.revision += 1
            self._changed()

    def configure_display_filter(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("显示滤波")
        form = QFormLayout(dialog)
        enabled = QCheckBox("用于波形、属性与 ERP 预览；不写入导出数据")
        enabled.setChecked(self.display_filter != DisplayFilter())
        low, high = QDoubleSpinBox(), QDoubleSpinBox()
        for item in (low, high):
            item.setRange(0, 2000)
            item.setDecimals(2)
            item.setSuffix(" Hz")
        low.setValue(self.display_filter.low or 1.0)
        high.setValue(self.display_filter.high or 40.0)
        form.addRow(enabled)
        form.addRow("高通（0 关闭）", low)
        form.addRow("低通（0 关闭）", high)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.display_filter = (
                DisplayFilter(low.value() or None, high.value() or None)
                if enabled.isChecked()
                else DisplayFilter()
            )
            self.request_render()

    def run_iclabel(self):
        if self.session is None or self.session.dataset.ica is None:
            self.statusBar().showMessage("ICLabel 需要已有 ICA 分解")
            return
        s = self.session
        if s.iclabel and s.iclabel["decomposition_id"] == s.dataset.ica.decomposition_id:
            self.show_iclabel(s.iclabel)
            return
        self.statusBar().showMessage("ICLabel 正在后台分类输入分解…")

        def done(report):
            if self.session is not s:
                return
            s.iclabel = report
            s.revision += 1
            self.autosave_timer.start(700)
            self.show_iclabel(report)

        self.jobs.submit("iclabel", lambda: classify_ics(s.dataset), done)

    def show_iclabel(self, report):
        report["shown"] = True
        self.session.view_context["iclabel_seen"] = {
            k: report[k] for k in ("algorithm", "backend", "decomposition_id")
        }
        dialog = QDialog(self)
        dialog.setWindowTitle("ICLabel · 输入分解的概率建议")
        dialog.resize(1100, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("概率仅作为辅助；勾选后进入候选预览，采用后才改变工作状态。"))
        choices = QHBoxLayout()
        categories = []
        for name in report["classes"]:
            check = QCheckBox(name)
            check.setChecked(name in ("Eye", "Muscle", "Line Noise", "Channel Noise"))
            categories.append(check)
            choices.addWidget(check)
        threshold = QDoubleSpinBox()
        threshold.setRange(0.01, 1.0)
        threshold.setSingleStep(0.05)
        threshold.setValue(0.8)
        choices.addWidget(QLabel("概率阈值"))
        choices.addWidget(threshold)
        layout.addLayout(choices)
        matrix = np.asarray(report["probabilities"])
        table = QTableWidget(len(matrix), 8)
        table.setHorizontalHeaderLabels(["排除 IC", *report["classes"]])
        for row, probabilities in enumerate(matrix):
            item = QTableWidgetItem(f"IC {row + 1:02d}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            table.setItem(row, 0, item)
            for col, probability in enumerate(probabilities, 1):
                entry = QTableWidgetItem(f"{probability:.3f}")
                entry.setFlags(Qt.ItemFlag.ItemIsEnabled)
                table.setItem(row, col, entry)
        layout.addWidget(table)

        def select(*_):
            suggested = iclabel_candidates(
                report, [c.text() for c in categories if c.isChecked()], threshold.value()
            )
            for row in range(len(matrix)):
                table.item(row, 0).setCheckState(
                    Qt.CheckState.Checked if row in suggested else Qt.CheckState.Unchecked
                )

        for check in categories:
            check.toggled.connect(select)
        threshold.valueChanged.connect(select)
        select()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("选择并预览")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.session.candidate.asr is None)
        layout.addWidget(buttons)
        if dialog.exec():
            selected = tuple(
                row for row in range(len(matrix)) if table.item(row, 0).checkState() == Qt.CheckState.Checked
            )
            self._propose(replace(self.session.candidate, excluded_ics=selected))
            self._populate_ics()

    def run_asr(self):
        if self.session is None or not self.session.dataset.continuous:
            self.statusBar().showMessage("ASR 用于连续数据；不将独立 epochs 拼接成连续信号。")
            return
        s, ds = self.session, self.session.dataset
        dialog = QDialog(self)
        dialog.setWindowTitle("ASR 后台预览")
        form = QFormLayout(dialog)
        form.addRow(QLabel("选择校准区间。ASR 结果保留采样坐标；采用后原 ICA 不再用于新阶段。"))
        cutoff, start, stop, highpass = [QDoubleSpinBox() for _ in range(4)]
        cutoff.setRange(1, 100)
        cutoff.setValue(20)
        duration = ds.data.shape[2] / ds.sfreq
        for spin in (start, stop):
            spin.setRange(0, duration)
            spin.setSuffix(" s")
        stop.setValue(min(30, duration))
        highpass.setRange(0, 10)
        highpass.setDecimals(2)
        highpass.setValue(0.5)
        form.addRow("截止标准差", cutoff)
        form.addRow("校准起点", start)
        form.addRow("校准终点", stop)
        form.addRow("处理前高通 Hz（0 不另滤波）", highpass)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        parent = s.review.processing.asr.parent if s.review.processing.asr else s.review.processing
        config = ASRConfig(parent, cutoff.value(), start.value(), stop.value(), highpass.value() or None)
        candidate = Processing(dropped=parent.dropped, asr=config)
        revision = s.revision
        self.statusBar().showMessage("正在准备 ASR 候选，完成后可与参考比较…")

        def prepare():
            s.engine.source(candidate)
            return candidate

        def done(value):
            if self.session is s and s.revision == revision:
                self._propose(value)
                self._populate_channel_table()
                self._populate_ics()

        self.jobs.submit("asr", prepare, done)

    def contribute(self):
        if self.session is None:
            return
        s = self.session
        dialog = QDialog(self)
        dialog.setWindowTitle("EpochLabel · 检查贡献内容")
        form = QFormLayout(dialog)
        form.addRow(QLabel("仅导出已人工检查 epochs，包含判断时的信号状态；不含文件路径与自由文本备注。"))
        fields = {}
        for key, label in (
            ("lab", "实验室匿名 ID"),
            ("study", "研究匿名 ID"),
            ("participant", "被试匿名 ID（跨文件保持一致）"),
            ("recording", "记录匿名 ID"),
        ):
            edit = QLineEdit(s.contribution_groups.get(key, ""))
            fields[key] = edit
            form.addRow(label, edit)
        waves = QCheckBox("包含判断时的多通道波形，供研究特征与模型训练")
        waves.setChecked(True)
        form.addRow(waves)
        condition_fields = QListWidget()
        condition_fields.setMaximumHeight(110)
        for name, values in s.dataset.conditions.items():
            sample_values = ", ".join(sorted(set(values.tolist()))[:3])[:70]
            item = QListWidgetItem(f"{name}: {sample_values}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name == "condition" else Qt.CheckState.Unchecked)
            condition_fields.addItem(item)
        form.addRow("可分享的条件字段（检查示例值）", condition_fields)
        consent = QCheckBox("我已检查贡献字段，并有权分享这些数据用于 EpochLabel 研究")
        form.addRow(consent)
        counts = {}
        for _, d in s.review.decisions:
            counts[d.quality] = counts.get(d.quality, 0) + 1
        form.addRow(QLabel(f"待导出：{counts}；匿名评分者：{self.rater_id[:8]}…"))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        consent.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        form.addRow(buttons)
        if not dialog.exec():
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存贡献包", "epochlabel.zip", "ZIP (*.zip)")
        if not path:
            return
        from copy import copy
        from eegqc.epochlabel.bundle import export_bundle

        s.contribution_groups = {k: v.text().strip() for k, v in fields.items()}
        s.revision += 1
        self.autosave_timer.start(700)
        snapshot = copy(s)
        include = waves.isChecked()
        shared_fields = tuple(
            condition_fields.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(condition_fields.count())
            if condition_fields.item(i).checkState() == Qt.CheckState.Checked
        )
        self.jobs.submit(
            "contribution",
            lambda: export_bundle(
                snapshot,
                path,
                rater_id=self.rater_id,
                groups=snapshot.contribution_groups,
                consent=True,
                include_waveforms=include,
                condition_fields=shared_fields,
            ),
            lambda result: self.statusBar().showMessage(f"贡献包已导出：{result}"),
        )

    def upload_contribution(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择已检查的贡献包", "", "ZIP (*.zip)")
        if not path:
            return
        from eegqc.epochlabel.bundle import read_manifest
        from eegqc.epochlabel.client import upload_bundle

        try:
            manifest = read_manifest(path)
        except (ValueError, OSError, KeyError) as error:
            self.report_error(str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("确认发送贡献包")
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                f"{len(manifest['samples'])} 个已检查样本；波形：{manifest['include_waveforms']}\n"
                f"匿名分组：{manifest['groups']}"
            )
        )
        endpoint, token = QLineEdit("http://127.0.0.1:8765"), QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("研究项目接收地址", endpoint)
        form.addRow("项目令牌（不保存）", token)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("发送此贡献包")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            url, secret = endpoint.text().strip(), token.text()
            self.network_jobs.submit(
                "upload",
                lambda: upload_bundle(path, url, secret),
                lambda result: self.statusBar().showMessage(f"已接收；凭据已保存：{result['receipt_file']}"),
            )

    def withdraw_contribution(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择上传凭据", "", "Receipt (*.receipt.json)")
        if not path:
            return
        from eegqc.epochlabel.client import withdraw_bundle

        dialog = QDialog(self)
        dialog.setWindowTitle("撤回贡献包")
        form = QFormLayout(dialog)
        form.addRow(QLabel("使用原研究项目令牌撤回此凭据对应的贡献包。"))
        token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("项目令牌", token)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("撤回此贡献包")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            secret = token.text()
            self.network_jobs.submit(
                "withdraw",
                lambda: withdraw_bundle(path, secret),
                lambda result: self.statusBar().showMessage(f"已撤回：{result['removed']}"),
            )

    def run_epochlabel(self):
        if self.session is None or self.session.dataset.continuous:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择本地 EpochLabel 试验模型", "", "JSON (*.json)")
        if not path:
            return
        from eegqc.epochlabel.model import predict_epochs

        try:
            model = json.loads(Path(path).read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            self.report_error(str(error))
            return
        s, revision = self.session, self.session.revision

        def done(probabilities):
            if self.session is not s or revision != s.revision:
                return
            self.navigator.clearSelection()
            for i in np.flatnonzero(probabilities >= 0.5):
                self.navigator.item(int(i)).setSelected(True)
                s.suggestions[int(i)] = (
                    f"EpochLabel-logistic-baseline:{model.get('model_id', 'unversioned')}:p={probabilities[i]:.4f}",
                )
            self.statusBar().showMessage("已选中试验模型候选（p≥0.5），请人工检查；尚未写入标签。")

        self.jobs.submit("epochlabel", lambda: predict_epochs(s, model), done)
