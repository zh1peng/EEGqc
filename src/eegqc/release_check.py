"""Release acceptance on actual public files, including a fresh-process restore.

Invoked by the frozen launcher and the release workflow, not on normal startup.
It checks scientific results and state transitions rather than merely starting Qt.
"""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import numpy as np
import scipy.linalg  # noqa: F401 -- initialize the SciPy BLAS thread pool
from PySide6.QtWidgets import QApplication
from threadpoolctl import threadpool_limits

from eegqc.adapters.algorithms import classify_ics
from eegqc.adapters.eeglab import load_set
from eegqc.application.long_tasks import asr_task, export_task, fit_ica_task, prepare_ica_task
from eegqc.application.persistence import restore_session, save_session
from eegqc.core.state import ASRConfig, Processing
from eegqc.ui.app import configure_app
from eegqc.ui.main_window import MainWindow


def wait(app, predicate, window, seconds=180):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        if window.statusBar().currentMessage().startswith("操作未完成"):
            raise RuntimeError(window.statusBar().currentMessage())
        sleep(0.005)
    record = window.long_tasks.runner.record
    detail = record.error if record is not None else window.statusBar().currentMessage()
    raise RuntimeError(f"Acceptance operation timed out: {detail}")


def draw(app, window):
    window.request_render()
    revision = window.view_revision
    wait(
        app, lambda: window.rendered_view_revision >= revision and not window.render_timer.isActive(), window
    )
    app.processEvents()


def task(app, window, kind, function, *args, output_path=None, **kwargs):
    results = []
    s = window.session
    window.long_tasks.submit(
        kind,
        function,
        args=args,
        kwargs=kwargs,
        data_id=s.dataset.data_id,
        input_state=s.review.processing,
        output_path=output_path,
        callback=results.append,
        valid=lambda: window.session is s,
    )
    wait(app, lambda: not window.long_tasks.busy and not window.long_tasks.timer.isActive(), window)
    record = window.long_tasks.runner.record
    if record.status != "succeeded" or len(results) != 1:
        raise RuntimeError(f"{kind} failed: {record.error or record.status}")
    return results[0]


def session_window(app, dataset):
    window = MainWindow()
    window.resize(1366, 900)
    window.set_dataset(load_set(dataset))
    window.session_path = None
    window.show()
    draw(app, window)
    return window


def reopen(app, args):
    window = session_window(app, args.dataset)
    try:
        restore_session(window.session, args.output / "session.eegqc.json")
        window._populate_ics()
        window._sync_state()
        draw(app, window)
        expected = np.load(args.output / "expected-current.npy", allow_pickle=False)
        np.testing.assert_allclose(
            window.session.engine.signal(window.session.review.processing), expected, atol=1e-13, rtol=1e-10
        )
        assert window.session.ica_for(window.session.review.processing).fit["algorithm"] == "extended-infomax"
        assert window.session.review.rejected == frozenset({1})
        exported = task(
            app,
            window,
            "export",
            export_task,
            window.session,
            str(args.output / "reopened-cleaned.set"),
            output_path=args.output / "reopened-cleaned.set",
        )
        actual = load_set(exported)
        np.testing.assert_allclose(
            actual.data, expected[[i for i in range(len(expected)) if i != 1]], atol=1e-10, rtol=1e-6
        )
        assert actual.n_epochs == len(expected) - 1
        window.grab().save(str(args.output / "restored-workflow.png"))
        return {
            "status": "passed",
            "fresh_process_restore": True,
            "exported_epochs": actual.n_epochs,
            "max_export_difference_v": float(np.max(np.abs(actual.data - np.delete(expected, 1, axis=0)))),
        }
    finally:
        window.close()
        app.processEvents()


def workflow(app, args):
    window = session_window(app, args.dataset)
    timings = {}
    try:
        s, ds = window.session, window.session.dataset
        assert ds.ica is not None and not ds.continuous
        s.freeze_reference()
        window.mark_selected(True)
        processing = Processing(excluded_ics=(0,), interpolated=(0,))
        window._propose(processing)
        draw(app, window)
        window.adopt()
        draw(app, window)
        assert s.pending_review_count == 1 and not s.review.rejected
        window.mark_selected(False)
        draw(app, window)
        assert dict(s.review.decisions)[0].processing == processing
        fit_channels = tuple(
            i
            for i, loc in enumerate(ds.chanlocs)
            if str(loc.get("type", "EEG")).upper() in ("", "EEG") and i != 0
        )[:8]
        prepared = task(
            app, window, "ica-input", prepare_ica_task, s, channels=fit_channels, max_iter=32, seed=17
        )
        ica = task(app, window, "ica", fit_ica_task, prepared)
        timings["ica"] = dict(window.long_tasks.runner.record.timings)
        s.propose_ica(ica)
        window._populate_ics()
        window._sync_state()
        draw(app, window)
        assert s.review.processing == processing and s.candidate.decomposition_id == ica.decomposition_id
        window._propose(replace(s.candidate, excluded_ics=(0,)))
        draw(app, window)
        window.adopt()
        draw(app, window)
        window.mark_selected(False)
        window.select_epoch(1)
        draw(app, window)
        window.mark_selected(True)
        draw(app, window)
        assert s.review.rejected == frozenset({1})
        report = classify_ics(s.component_dataset(s.review.processing))
        assert report["decomposition_id"] == ica.decomposition_id
        probabilities = np.asarray(report["probabilities"])
        assert probabilities.shape == (ica.count, 7) and np.isfinite(probabilities).all()
        np.testing.assert_allclose(probabilities.sum(axis=1), 1.0, atol=2e-6)
        s.iclabel = report
        window.erp_dock.show()
        draw(app, window)
        current = s.engine.signal(s.review.processing)
        direct = np.delete(current, 1, axis=0).mean(axis=0)
        baseline = (ds.tmin, 0.0) if window.erp_baseline.isChecked() else None
        if baseline:
            mask = (ds.times >= baseline[0]) & (ds.times <= baseline[1])
            direct = direct - direct[:, mask].mean(axis=1, keepdims=True)
        _, displayed = window.erp_curve.getData()
        np.testing.assert_allclose(displayed, direct[window.erp_channel.currentIndex()] * 1e6, atol=1e-7)
        save_session(s, args.output / "session.eegqc.json")
        np.save(args.output / "expected-current.npy", current, allow_pickle=False)
        exported = task(
            app,
            window,
            "export",
            export_task,
            s,
            str(args.output / "cleaned.set"),
            output_path=args.output / "cleaned.set",
        )
        np.testing.assert_allclose(
            load_set(exported).data, np.delete(current, 1, axis=0), atol=1e-10, rtol=1e-6
        )
        window.grab().save(str(args.output / "ica-workflow.png"))
        result = {
            "input_shape": list(ds.data.shape),
            "new_components": ica.count,
            "new_decomposition_id": ica.decomposition_id,
            "iclabel_rows": len(probabilities),
            "source_and_reconstruction": "passed",
            "displayed_erp": "passed",
            "task_timings": timings,
        }
        if args.continuous:
            window.set_dataset(load_set(args.continuous))
            window.session_path = None
            draw(app, window)
            s = window.session
            parent = s.review
            candidate = Processing(asr=ASRConfig(Processing(), calibration_stop=20.0))
            delivered = []
            window.long_tasks.submit(
                "asr",
                asr_task,
                args=(s, candidate),
                data_id=s.dataset.data_id,
                input_state=Processing(),
                callback=delivered.append,
            )
            wait(
                app,
                lambda: (
                    window.long_tasks.runner.record.stage == "ASR 校准与重建"
                    or window.long_tasks.runner.record.status != "running"
                ),
                window,
            )
            window.page(1)
            draw(app, window)
            window.long_tasks.cancel()
            wait(app, lambda: not window.long_tasks.busy, window)
            assert not delivered and s.review == parent
            assert window.long_tasks.runner.record.status == "cancelled"
            cleaned_processing, signal = task(app, window, "asr", asr_task, s, candidate)
            s.engine.install_asr(cleaned_processing.asr, signal)
            window._propose(cleaned_processing)
            draw(app, window)
            window.adopt()
            draw(app, window)
            assert np.isfinite(signal).all() and np.max(np.abs(signal)) < 0.01
            window.select_interval(2.0, 3.0)
            window.mark_selected(True)
            draw(app, window)
            assert len(s.review.active_intervals) == 1
            output = args.output / "continuous-cleaned.set"
            task(app, window, "export", export_task, s, str(output), output_path=output)
            actual = load_set(output)
            (interval,) = s.review.active_intervals
            expected = np.delete(signal, np.arange(interval.start, interval.stop), axis=2)
            np.testing.assert_allclose(actual.data, expected, atol=1e-10, rtol=1e-6)
            window.grab().save(str(args.output / "continuous-workflow.png"))
            result["continuous"] = {
                "cancel_and_browse": "passed",
                "retry_asr": "passed",
                "interval_export": "passed",
                "remaining_samples": actual.data.shape[2],
            }
        return result
    finally:
        window.close()
        app.processEvents()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--continuous", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--phase", choices=("workflow", "reopen"), default="workflow")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    configure_app(app)
    report_path = args.output / ("reopen.json" if args.phase == "reopen" else "acceptance.json")
    report = {
        "status": "failed",
        "frozen": bool(getattr(sys, "frozen", False)),
        "version": __import__("eegqc").__version__,
    }
    try:
        with threadpool_limits(limits=2, user_api="blas"):
            if args.phase == "reopen":
                report.update(reopen(app, args))
            else:
                report.update(workflow(app, args))
                command = [sys.executable]
                command += ["--verify-release"] if report["frozen"] else ["-m", "eegqc.release_check"]
                command += [
                    "--phase",
                    "reopen",
                    "--dataset",
                    str(args.dataset.resolve()),
                    "--output",
                    str(args.output.resolve()),
                ]
                completed = subprocess.run(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if completed.returncode:
                    raise RuntimeError("Fresh-process session restore failed; see reopen.json")
                report["reopen"] = json.loads((args.output / "reopen.json").read_text(encoding="utf-8"))
                report["status"] = "passed"
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
