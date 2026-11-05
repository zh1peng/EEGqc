from dataclasses import asdict, replace

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
from eegqc.ui.ica_dialog import ICAFitDialog


class WorkflowActions:
    """User-facing scientific workflows, kept separate from the plotting window."""

    def configure_workflows(self):
        self.display_filter = DisplayFilter()
        self.interval_selection = None
        self.rater_id = local_rater_id()
        processing = self.algorithm_menu.addMenu("Processing · creates a preview")
        self._action(processing, "Fit ICA on applied data…", self.run_ica)
        self.asr_action = self._action(processing, "ASR · continuous data only…", self.run_asr)
        components = self.algorithm_menu.addMenu("IC assistance · review suggestions")
        self.iclabel_action = self._action(components, "ICLabel probabilities…", self.run_iclabel)
        self.epoch_actions = self.algorithm_menu.addMenu("Epoch assistance · review suggestions")
        self._action(self.views_menu, "Display filter (waveforms and ERP)…", self.configure_display_filter)
        self._action(self.file_menu, "Export EpochLabel contribution…", self.contribute)
        self._action(self.file_menu, "Upload reviewed contribution…", self.upload_contribution)
        self._action(self.file_menu, "Withdraw uploaded contribution…", self.withdraw_contribution)

    def _submit_long_task(self, *args, **kwargs):
        if self.long_tasks.busy:
            self.statusBar().showMessage("A task is running or canceling. Wait before starting another.")
            return False
        try:
            self.long_tasks.submit(*args, **kwargs)
        except (ValueError, OSError) as error:
            self.report_error(str(error))
            return False
        return True

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
        dialog.setWindowTitle("Display filter")
        form = QFormLayout(dialog)
        enabled = QCheckBox(
            "Applies to waveforms, properties and ERP view only; not applied to exported data"
        )
        enabled.setChecked(self.display_filter != DisplayFilter())
        low, high = QDoubleSpinBox(), QDoubleSpinBox()
        for item in (low, high):
            item.setRange(0, 2000)
            item.setDecimals(2)
            item.setSuffix(" Hz")
        low.setValue(self.display_filter.low or 1.0)
        high.setValue(self.display_filter.high or 40.0)
        form.addRow(enabled)
        form.addRow("High-pass (0 off)", low)
        form.addRow("Low-pass (0 off)", high)
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
        if self.session is None or self.session.ica_for() is None:
            self.statusBar().showMessage("This stage has no ICA; Continue basic QC, or first fit a new ICA.")
            return
        self.set_workspace(2)
        s = self.session
        processing = s.candidate
        ds = replace(s.dataset, ica=s.ica_for(processing))
        if not self._has_locations(ds, ds.ica.channels):
            self.statusBar().showMessage(
                "ICLabel requires electrode locations; waveform review remains available."
            )
            return
        if s.iclabel and s.iclabel["decomposition_id"] == ds.ica.decomposition_id:
            self.show_iclabel(s.iclabel)
            return
        self.statusBar().showMessage(f"ICLabel Classifying decomposition {ds.ica.decomposition_id[:8]}…")

        def done(report):
            if (
                self.session is not s
                or s.ica_for() is None
                or s.ica_for().decomposition_id != ds.ica.decomposition_id
            ):
                return
            s.iclabel = report
            s.revision += 1
            self.autosave_timer.start(700)
            self.show_iclabel(report)

        self.property_jobs.submit("iclabel", lambda: classify_ics(s.component_dataset(processing)), done)

    def show_iclabel(self, report):
        report["shown"] = True
        self.session.view_context["iclabel_seen"] = {
            k: report[k] for k in ("algorithm", "backend", "decomposition_id")
        }
        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"ICLabel · Decomposition {report['decomposition_id'][:8]} probability suggestions"
        )
        dialog.resize(1100, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "Probabilities are suggestions. Select components to preview, then apply the processing change."
            )
        )
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
        choices.addWidget(QLabel("Probability threshold"))
        choices.addWidget(threshold)
        layout.addLayout(choices)
        matrix = np.asarray(report["probabilities"])
        table = QTableWidget(len(matrix), 8)
        table.setHorizontalHeaderLabels(["Exclude IC", *report["classes"]])
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
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Preview selected exclusions")
        layout.addWidget(buttons)
        if dialog.exec():
            selected = tuple(
                row for row in range(len(matrix)) if table.item(row, 0).checkState() == Qt.CheckState.Checked
            )
            self._propose(replace(self.session.candidate, excluded_ics=selected))
            self._populate_ics()

    def run_ica(self):
        if self.session is None:
            return
        from copy import copy
        from eegqc.application.long_tasks import fit_ica_task, prepare_ica_task

        s = self.session
        snapshot = copy(s)
        snapshot.review = s.review
        snapshot.candidate = s.review.processing
        dialog = ICAFitDialog(snapshot, self._describe(snapshot.review.processing), self)
        self.ica_dialog = dialog

        def prepare():
            parameters = dialog.parameters()
            dialog.prepare_button.setEnabled(False)
            dialog.summary.setText("Checking frozen input and effective rank…")

            def done(prepared):
                if not dialog.isVisible():
                    return
                dialog.prepare_button.setEnabled(True)
                if parameters == dialog.parameters():
                    dialog.show_prepared(prepared)

            submitted = self._submit_long_task(
                "ica-input",
                prepare_ica_task,
                args=(snapshot,),
                kwargs=parameters,
                data_id=s.dataset.data_id,
                input_state=snapshot.review.processing,
                parameters=parameters,
                callback=done,
                valid=lambda: self.session is s,
            )
            if not submitted:
                dialog.prepare_button.setEnabled(True)

        def fit(prepared):
            if self.session is not s:
                dialog.summary.setText(
                    "Dataset changed. Close this dialog and start again from the current dataset."
                )
                return

            original_candidate = s.candidate

            def apply(ica):
                s.propose_ica(ica)
                self._populate_ics()
                self._sync_state()
                self.request_render()
                self.statusBar().showMessage(
                    "New ICA preview ready. Inspect the components, then apply to continue QC."
                )

            def done(ica):
                self._receive_task_candidate(
                    s, prepared.input_processing, original_candidate, lambda: apply(ica)
                )

            submitted = self._submit_long_task(
                "ica",
                fit_ica_task,
                args=(prepared,),
                data_id=s.dataset.data_id,
                input_state=prepared.input_processing,
                parameters=prepared.params,
                callback=done,
                valid=lambda: self.session is s,
            )
            if submitted:
                dialog.close()

        dialog.prepare_requested.connect(prepare)
        dialog.fit_requested.connect(fit)
        dialog.show()

    def run_asr(self):
        if self.session is None or not self.session.dataset.continuous:
            self.statusBar().showMessage(
                "ASR requires continuous data; separate epochs are not concatenated into continuous data."
            )
            return
        s, ds = self.session, self.session.dataset
        dialog = QDialog(self)
        dialog.setWindowTitle("ASR background preview")
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                "Choose a calibration interval.ASR Results preserve sample coordinates. After applying, the original ICA is not reused for the new stage."
            )
        )
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
        form.addRow("Cutoff (standard deviations)", cutoff)
        form.addRow("Calibration start", start)
        form.addRow("Calibration end", stop)
        form.addRow("Preprocessing high-pass Hz(0 no extra filtering)", highpass)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        parent = s.review.processing.asr.parent if s.review.processing.asr else s.review.processing
        original_processing, original_candidate = s.review.processing, s.candidate
        config = ASRConfig(parent, cutoff.value(), start.value(), stop.value(), highpass.value() or None)
        candidate = Processing(dropped=parent.dropped, asr=config)
        self.statusBar().showMessage("Preparing ASR preview; compare with the reference when complete…")
        from eegqc.application.long_tasks import asr_task

        def done(value):
            processing, signal = value
            s.engine.install_asr(processing.asr, signal)

            def apply():
                self._propose(processing)
                self._populate_channel_table()
                self._populate_ics()

            self._receive_task_candidate(s, original_processing, original_candidate, apply)

        self._submit_long_task(
            "asr",
            asr_task,
            args=(s, candidate),
            data_id=ds.data_id,
            input_state=parent,
            parameters=asdict(config),
            callback=done,
            valid=lambda: self.session is s,
        )

    def contribute(self):
        if self.session is None:
            return
        s = self.session
        dialog = QDialog(self)
        dialog.setWindowTitle("EpochLabel · Review contribution contents")
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                "Export only reviewed epochs, Includes the signal state used for each judgment; excludes file paths and free-text notes."
            )
        )
        fields = {}
        for key, label in (
            ("lab", "Anonymous lab ID"),
            ("study", "Anonymous study ID"),
            ("participant", "Anonymous participant ID(consistent across files)"),
            ("recording", "Anonymous recording ID"),
        ):
            edit = QLineEdit(s.contribution_groups.get(key, ""))
            fields[key] = edit
            form.addRow(label, edit)
        waves = QCheckBox("Include reviewed multichannel waveforms for research and model training")
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
        form.addRow("Condition fields to share (inspect sample values)", condition_fields)
        consent = QCheckBox(
            "I have reviewed these fields and am authorized to share this data for EpochLabel research"
        )
        form.addRow(consent)
        counts = {}
        for _, d in s.review.decisions:
            counts[d.quality] = counts.get(d.quality, 0) + 1
        form.addRow(QLabel(f"To export: {counts}; Anonymous rater: {self.rater_id[:8]}…"))
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
        path, _ = QFileDialog.getSaveFileName(self, "Save contribution", "epochlabel.zip", "ZIP (*.zip)")
        if not path:
            return
        from copy import copy
        from eegqc.application.long_tasks import export_bundle_task

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
        self._submit_long_task(
            "contribution",
            export_bundle_task,
            args=(snapshot, path),
            kwargs=dict(
                rater_id=self.rater_id,
                groups=snapshot.contribution_groups,
                consent=True,
                include_waveforms=include,
                condition_fields=shared_fields,
            ),
            data_id=s.dataset.data_id,
            input_state=s.review.processing,
            output_path=path,
            callback=lambda result: self.statusBar().showMessage(f"Contribution exported: {result}"),
            valid=lambda: self.session is s,
        )

    def upload_contribution(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose reviewed contribution", "", "ZIP (*.zip)")
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
        dialog.setWindowTitle("Confirm contribution upload")
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                f"{len(manifest['samples'])} reviewed samples; waveforms: {manifest['include_waveforms']}\n"
                f"Anonymous groups: {manifest['groups']}"
            )
        )
        endpoint, token = QLineEdit("http://127.0.0.1:8765"), QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Project receiver URL", endpoint)
        form.addRow("Project token (not saved)", token)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Upload this contribution")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            url, secret = endpoint.text().strip(), token.text()
            self.network_jobs.submit(
                "upload",
                lambda: upload_bundle(path, url, secret),
                lambda result: self.statusBar().showMessage(
                    f"Received; receipt saved: {result['receipt_file']}"
                ),
            )

    def withdraw_contribution(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose upload receipt", "", "Receipt (*.receipt.json)")
        if not path:
            return
        from eegqc.epochlabel.client import withdraw_bundle

        dialog = QDialog(self)
        dialog.setWindowTitle("Withdraw contribution")
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                "Use the original project token to withdraw the contribution associated with this receipt."
            )
        )
        token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Project token", token)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Withdraw this contribution")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            secret = token.text()
            self.network_jobs.submit(
                "withdraw",
                lambda: withdraw_bundle(path, secret),
                lambda result: self.statusBar().showMessage(f"Withdrawn: {result['removed']}"),
            )
