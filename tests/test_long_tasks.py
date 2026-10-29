from pathlib import Path
from time import monotonic, sleep

import numpy as np
import pytest

from eegqc.application.long_tasks import LongTaskRunner


def slow_export(context, destination):
    output = context.output(destination)
    output.source.write_text("incomplete", encoding="utf-8")
    context.report("writing")
    sleep(30)  # represents a third-party call without cancellation callbacks
    return output


def fail_task(context):
    raise ValueError("controlled failure")


def array_task(context, array):
    context.report("matrix result")
    return array * 2


def wait_until(runner, predicate, timeout=20):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        record = runner.poll()
        if predicate(record):
            return record
        sleep(0.01)
    raise AssertionError(f"Long task timed out: {runner.record}")


def start(runner, function, *args, **kwargs):
    return runner.start("test", function, *args, data_id="dataset-a", input_state="input-a", **kwargs)


def test_real_cancel_preserves_destination_discards_late_result_and_next_task_works(tmp_path):
    runner = LongTaskRunner()
    target = tmp_path / "existing.set"
    target.write_text("previous valid output", encoding="utf-8")
    try:
        cancelled = start(runner, slow_export, target, output_path=target)
        work_dir = runner._work_dir
        wait_until(runner, lambda r: r.stage == "writing")
        assert (work_dir / target.name).exists()
        with pytest.raises(RuntimeError, match="already running"):
            start(runner, fail_task)
        before = monotonic()
        runner.cancel()
        wait_until(runner, lambda r: not runner.busy, timeout=5)
        assert monotonic() - before < 5
        assert cancelled.status == "cancelled" and cancelled.result is None
        assert target.read_text(encoding="utf-8") == "previous valid output"
        assert not work_dir.exists()
        runner._messages.put((cancelled.task_id, "result", "late success"))
        assert runner.poll().status == "cancelled"

        failed = start(runner, fail_task)
        wait_until(runner, lambda r: r.status == "failed" and not runner.busy)
        assert "controlled failure" in failed.error
        values = np.arange(24, dtype=np.float64).reshape(4, 6)
        result = start(runner, array_task, values)
        values[:] = -100  # submitted inputs were frozen before this mutation
        # An old task result also cannot overwrite a newly started record.
        runner._messages.put((cancelled.task_id, "result", "wrong dataset"))
        wait_until(runner, lambda r: r.status == "succeeded" and not runner.busy)
        np.testing.assert_array_equal(result.result, np.arange(24).reshape(4, 6) * 2)
        assert result.timings["input_bytes"] > 0
        assert result.timings["spawn_transfer_seconds"] > 0
        assert result.timings["serialize_seconds"] >= 0
    finally:
        runner.close()


def test_export_publishes_only_completed_set_and_roundtrips_current_signal(tmp_path):
    from eegqc.adapters.eeglab import load_set
    from eegqc.application.long_tasks import export_task
    from eegqc.application.session import Session
    from eegqc.core.state import Processing
    from eegqc.demo import make_demo

    session = Session(make_demo(18, 3, 100.0, 0.5))
    session.propose(Processing(excluded_ics=(0,)))
    session.adopt()
    expected = session.engine.signal(session.review.processing)
    target = tmp_path / "中文 output.set"
    target.write_bytes(b"previous output")
    runner = LongTaskRunner()
    try:
        result = start(runner, export_task, session, target, output_path=target)
        work_dir = runner._work_dir
        wait_until(runner, lambda r: r.status != "running" and not runner.busy, timeout=45)
        assert result.status == "succeeded", result.error
        assert result.result == target.resolve()
        np.testing.assert_allclose(load_set(target).data, expected, atol=1e-10)
        assert not work_dir.exists()
        assert not list(Path(tmp_path).glob(".eegqc-task-*"))
    finally:
        runner.close()


def test_qt_result_validity_and_preflight_callback_can_start_the_fit(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from eegqc.application.long_tasks import TaskRecord
    from eegqc.ui.long_tasks import LongTasks

    app = QApplication.instance() or QApplication([])
    tasks = LongTasks()
    displayed = []

    def fake_start(kind, function, *args, **kwargs):
        record = TaskRecord(kind, kwargs["data_id"], kwargs["input_state"], {})
        tasks.runner.record = record
        return record

    monkeypatch.setattr(tasks.runner, "start", fake_start)

    def fit_after_preflight(result):
        tasks.submit("fit", array_task, data_id="a", input_state="input", callback=displayed.append)

    preflight = tasks.submit(
        "prepare", array_task, data_id="a", input_state="input", callback=fit_after_preflight
    )
    tasks.runner._messages.put((preflight.task_id, "result", "prepared"))
    tasks._poll()
    fitting = tasks.runner.record
    assert fitting.kind == "fit" and tasks.timer.isActive()
    tasks.runner._messages.put((fitting.task_id, "result", "fitted"))
    tasks._poll()
    assert displayed == ["fitted"]

    old_dataset = tasks.submit(
        "old", array_task, data_id="a", input_state="input", callback=displayed.append, valid=lambda: False
    )
    tasks.runner._messages.put((old_dataset.task_id, "result", "wrong dataset"))
    tasks._poll()
    assert displayed == ["fitted"]
    assert not tasks.timer.isActive()
    app.processEvents()
