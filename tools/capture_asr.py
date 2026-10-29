"""Render the real continuous-data ASR/interval workflow for visual review."""

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

from eegqc.adapters.eeglab import load_set
from eegqc.core.state import ASRConfig, Processing
from eegqc.ui.app import configure_app
from eegqc.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    configure_app(app)
    dataset = load_set(Path("output/reference/eeglab_data.set"))
    window = MainWindow()
    window.resize(1600, 1100)
    window.set_dataset(dataset)
    window.session_path = None  # Visual review must not create QC decisions alongside the example.
    session = window.session
    processing = Processing(asr=ASRConfig(Processing(), calibration_stop=20.0))
    with threadpool_limits(limits=2, user_api="blas"):
        session.engine.source(processing)
    session.propose(processing)
    session.adopt()
    session.mark_interval(round(1.5 * dataset.sfreq), round(2.5 * dataset.sfreq))
    window._sync_state()
    window.compare.setChecked(True)
    window.show()
    window.request_render()
    timer = QTimer()
    start = perf_counter()

    def finish():
        if window.jobs.active is not None or window.jobs.pending or window.render_timer.isActive():
            if perf_counter() - start > 30:
                timer.stop()
                window.close()
                app.exit(1)
            return
        timer.stop()
        output = Path("output/eegqc-v2-asr.png")
        window.grab().save(str(output))
        window.close()
        app.quit()
        print(f"Saved {output}")

    timer.timeout.connect(finish)
    timer.start(100)
    with threadpool_limits(limits=2, user_api="blas"):
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
