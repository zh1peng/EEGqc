"""Visual QA of the actual combined-condition and contribution dialogs."""

import json
import os
from pathlib import Path
import sys
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from threadpoolctl import threadpool_limits

from eegqc.core.conditions import Clause, Condition
from eegqc.adapters.eeglab import load_set
from eegqc.demo import make_demo
from eegqc.ui.app import configure_app
from eegqc.ui.conditions import ConditionsDialog
from eegqc.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    configure_app(app)
    window = MainWindow()
    window.resize(1600, 1100)
    window.set_dataset(make_demo())
    window.session.erp_conditions = {
        "target_correct": Condition((Clause("condition", "=", "target"), Clause("correct", "=", "true"))),
        "standard_correct": Condition((Clause("condition", "=", "standard"), Clause("correct", "=", "true"))),
    }
    window.use_custom_conditions.setChecked(True)
    window.erp_dock.show()
    window.show()
    window.request_render()
    output = Path("output")
    output.mkdir(exist_ok=True)
    start = perf_counter()
    timer = QTimer()

    def finish():
        if window.jobs.active is not None or window.jobs.pending or window.render_timer.isActive():
            if perf_counter() - start > 30:
                timer.stop()
                window.close()
                app.exit(1)
                print("Workflow view did not finish")
            return
        timer.stop()
        window.grab().save(str(output / "eegqc-v2-combined-erp.png"))
        dialog = ConditionsDialog(window.session.dataset, window.session.erp_conditions, window)
        dialog.names.setCurrentRow(0)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(output / "eegqc-v2-condition-editor.png"))
        dialog.close()
        window.session.mark([0], False)
        window.session.mark([4], True, "eye")

        def capture_contribution():
            dialog = app.activeModalWidget()
            if dialog is not None:
                dialog.grab().save(str(output / "eegqc-v2-contribution.png"))
                dialog.reject()

        QTimer.singleShot(200, capture_contribution)
        window.contribute()
        report_path = output / "iclabel-reference.json"
        if report_path.exists():
            window.set_dataset(load_set(output / "reference/eeglab_data_epochs_ica.set"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["decomposition_id"] = window.session.dataset.ica.decomposition_id

            def capture_iclabel():
                dialog = app.activeModalWidget()
                if dialog is not None:
                    dialog.grab().save(str(output / "eegqc-v2-iclabel.png"))
                    dialog.reject()

            QTimer.singleShot(200, capture_iclabel)
            window.show_iclabel(report)
        window.close()
        app.quit()
        print("Saved workflow views to output/")

    timer.timeout.connect(finish)
    timer.start(100)
    with threadpool_limits(limits=2, user_api="blas"):
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
