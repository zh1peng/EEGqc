"""Measure application actions through the first paint containing their new data.

This measures Qt widget painting, not the operating system compositor or monitor.
Run manually after relevant changes; it is deliberately not a timing assertion in CI.
"""

import argparse
from dataclasses import replace
import json
from importlib.metadata import version
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import numpy as np
import psutil
import scipy.linalg  # noqa: F401 -- load both BLAS runtimes before limiting threads
from PySide6.QtCore import QObject, QEvent, QEventLoop, QTimer, Signal
from PySide6.QtWidgets import QApplication
from threadpoolctl import threadpool_info, threadpool_limits

from eegqc.adapters.eeglab import load_set
from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.ui.app import configure_app
from eegqc.ui.main_window import MainWindow


class PaintProbe(QObject):
    finished = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.waveform = window.waveform.viewport()
        self.erp = window.erp_plot.viewport()
        for widget in (self.waveform, self.erp):
            widget.installEventFilter(self)
        self.target = None
        self.required = set()
        self.painted = set()
        self.completed = None

    def begin(self):
        self.target = None
        self.completed = None
        self.painted.clear()
        self.required = {self.waveform}
        if self.window.erp_dock.isVisible() and not self.window.session.dataset.continuous:
            self.required.add(self.erp)
        self.started = perf_counter()

    def eventFilter(self, widget, event):
        if (
            event.type() == QEvent.Type.Paint
            and self.target is not None
            and self.window.rendered_view_revision >= self.target
        ):
            # The event filter runs before QWidget.paintEvent. The queued callback
            # observes its completion without changing the application drawing path.
            revision = self.target
            QTimer.singleShot(0, lambda w=widget, r=revision: self._paint_finished(w, r))
        return False

    def _paint_finished(self, widget, revision):
        if self.target != revision or self.completed is not None:
            return
        self.painted.add(widget)
        if self.required <= self.painted:
            self.completed = (perf_counter() - self.started) * 1000
            self.finished.emit()


def wait(app, predicate, window, seconds=30):
    loop = QEventLoop()
    check, deadline = QTimer(), QTimer()

    def poll():
        if predicate():
            loop.quit()

    check.timeout.connect(poll)
    check.start(1)
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(round(seconds * 1000))
    loop.exec()
    check.stop()
    deadline.stop()
    if not predicate():
        raise RuntimeError(f"The requested view did not finish: {window.statusBar().currentMessage()}")


def wait_for_paint(probe, window):
    # Run Qt's normal dispatcher. Repeated processEvents()+sleep() would insert
    # artificial timer/scheduling delays into the operation being measured.
    loop, deadline = QEventLoop(), QTimer()
    probe.finished.connect(loop.quit)
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(30_000)
    loop.exec()
    deadline.stop()
    probe.finished.disconnect(loop.quit)
    if probe.completed is None:
        raise RuntimeError(f"The requested paint did not finish: {window.statusBar().currentMessage()}")


def measure(app, window, probe, operation, repeats):
    resident = []
    process = psutil.Process()

    def one():
        probe.begin()
        operation()
        probe.target = window.view_revision
        wait_for_paint(probe, window)
        return probe.completed

    one()
    values = []
    for index in range(repeats):
        values.append(one())
        if index + 1 in (5, 15, repeats):
            resident.append({"repeat": index + 1, "resident_mb": round(process.memory_info().rss / 1e6, 2)})
    return {
        "p50_ms": round(float(np.percentile(values, 50)), 3),
        "p95_ms": round(float(np.percentile(values, 95)), 3),
        "max_ms": round(max(values), 3),
        "samples": len(values),
        "resident_samples": resident,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--channels", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--sfreq", type=float, default=500.0)
    parser.add_argument("--duration", type=float, default=1.2)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--visible-channels", type=int, choices=(16, 32, 64), default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    configure_app(app)
    with threadpool_limits(limits=2, user_api="blas"):
        preparation_started = perf_counter()
        ds = (
            load_set(args.dataset)
            if args.dataset
            else make_demo(args.channels, args.epochs, args.sfreq, args.duration)
        )
        preparation_ms = (perf_counter() - preparation_started) * 1000
        first_view_started = perf_counter()
        window = MainWindow()
        window.resize(1500, 1000)
        window.set_dataset(ds)
        window.session_path = None  # A benchmark never saves experimental QC decisions.
        window.channel_count.setCurrentText(f"{args.visible_channels} 通道")
        window.show()
        if not ds.continuous:
            window.erp_dock.show()
        probe = PaintProbe(window)
        probe.begin()
        probe.started = first_view_started
        window.request_render()
        probe.target = window.view_revision
        wait_for_paint(probe, window)
        first_view_ms = probe.completed
        baseline = Processing()
        process = psutil.Process()
        initial_memory = process.memory_info().rss
        forwards = False

        def page():
            nonlocal forwards
            forwards = not forwards
            window.page(1 if forwards else -1)

        results = {"page": measure(app, window, probe, page, args.repeats)}
        if ds.ica is not None:
            selected = False

            def toggle_ic():
                nonlocal selected
                selected = not selected
                window._propose(replace(baseline, excluded_ics=(0,) if selected else ()))

            results["ic_waveform_and_erp"] = measure(app, window, probe, toggle_ic, args.repeats)
        selected = False

        def toggle_interpolation():
            nonlocal selected
            selected = not selected
            window._propose(replace(baseline, interpolated=(0,) if selected else ()))

        positioned = sum(
            np.isfinite([loc.get(k, np.nan) for k in ("X", "Y", "Z")]).all() for loc in ds.chanlocs
        )
        if positioned >= 4 and all(np.isfinite(ds.chanlocs[0].get(k, np.nan)) for k in ("X", "Y", "Z")):
            results["interpolation_waveform_and_erp"] = measure(
                app, window, probe, toggle_interpolation, args.repeats
            )
        window._propose(baseline)
        wait(
            app,
            lambda: (
                window.jobs.active is None and not window.jobs.pending and not window.render_timer.isActive()
            ),
            window,
        )
        if not ds.continuous:
            reject = False

            def toggle_epoch():
                nonlocal reject
                reject = not reject
                window.mark_selected(reject)

            results["epoch_and_erp"] = measure(app, window, probe, toggle_epoch, args.repeats)
        report = {
            "application_version": __import__("eegqc").__version__,
            "source": ds.source.name if ds.source else "explicitly synthetic",
            "shape": list(ds.data.shape),
            "sfreq": ds.sfreq,
            "visible_channels": int(window.channel_count.currentText().split()[0]),
            "window_size": [window.width(), window.height()],
            "platform": platform.platform(),
            "dependency_versions": {
                name: version(name) for name in ("numpy", "scipy", "threadpoolctl", "PySide6-Essentials")
            },
            "processor": platform.processor(),
            "qt_platform": app.platformName(),
            "measurement": "application action to completed Qt widget paints; compositor excluded",
            "dataset_preparation_ms": round(preparation_ms, 3),
            "first_view_ms": round(first_view_ms, 3),
            "blas": [
                {k: b[k] for k in ("internal_api", "num_threads", "version")} for b in threadpool_info()
            ],
            "results": results,
            "initial_resident_mb": round(initial_memory / 1e6, 2),
            "final_resident_mb": round(process.memory_info().rss / 1e6, 2),
        }
        window.close()
        app.processEvents()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
