"""Render the real Qt application and an adopted preview for visual inspection."""

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

from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.ui.main_window import MainWindow
from eegqc.ui.app import configure_app


def main():
    output = Path("output")
    output.mkdir(exist_ok=True)
    app = QApplication(sys.argv)
    configure_app(app)
    window = MainWindow()
    errors = []
    window.report_error = errors.append
    window.jobs.failed.disconnect()
    window.jobs.failed.connect(errors.append)
    window.set_dataset(make_demo())
    window.resize(1560, 1060)
    window.show()
    window.session.propose(Processing(excluded_ics=(0,)))
    window.session.adopt()
    window.session.mark([4], True, "eye", "合成眨眼演示")
    window.select_epoch(4)
    window.erp_dock.show()
    window.compare.setChecked(True)
    window.condition_field.setCurrentIndex(window.condition_field.findData("condition"))
    window.split_conditions.setChecked(True)
    window.request_render()
    start = perf_counter()
    timer = QTimer()
    pages = [(0, "epoch-workspace"), (1, "channel-properties"), (2, "ica-workspace"), (3, "review-workspace")]
    stage = 0

    def capture():
        nonlocal stage
        if errors:
            print("UI errors:", errors)
            window.close()
            app.exit(1)
        elif window.jobs.active is None and not window.jobs.pending and not window.render_timer.isActive():
            target = output / f"eegqc-{pages[stage][1]}.png"
            window.grab().save(str(target))
            print("Saved", target)
            stage += 1
            if stage == len(pages):
                window.close()
                app.quit()
            else:
                window.set_workspace(pages[stage][0])
        elif perf_counter() - start > 45:
            print("Capture timed out while waiting for calculation")
            window.close()
            app.exit(1)

    timer.timeout.connect(capture)
    timer.start(100)
    with threadpool_limits(limits=2, user_api="blas"):
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
