import os
from dataclasses import replace
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from eegqc.core.conditions import Clause, Condition
from eegqc.core.state import DisplayFilter, Processing
from eegqc.demo import make_demo
from eegqc.ui.app import configure_app
from eegqc.ui.main_window import MainWindow


def wait_for_view(app, window):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        app.processEvents()
        if (
            not window.render_timer.isActive()
            and window.jobs.active is None
            and not window.jobs.pending
            and window.property_jobs.active is None
            and not window.property_jobs.pending
        ):
            return
        sleep(0.005)
    raise AssertionError("The requested scientific view did not finish")


def test_displayed_erp_and_label_context_follow_the_view_actually_seen():
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    window = MainWindow()
    ds = make_demo(18, 18, 100.0, 1.0)
    window.set_dataset(ds)
    window.show()
    query = Condition((Clause("condition", "=", "target"), Clause("correct", "=", "true")))
    window.session.erp_conditions = {"target_correct": query}
    window.use_custom_conditions.setChecked(True)
    window.erp_dock.show()
    window.request_render()
    wait_for_view(app, window)
    expected = window.session.engine.erp(
        condition=query, channels=[ds.labels.index("Pz")], baseline=(-0.2, 0)
    )
    x, y = window.erp_curve.getData()
    np.testing.assert_allclose(x, ds.times * 1000)
    np.testing.assert_allclose(y, expected.data[0] * 1e6, atol=1e-6)
    window.display_filter = DisplayFilter(2.0, 30.0)
    window.request_render()  # next filter has not been drawn yet
    window.mark_selected(True)
    decision = dict(window.session.review.decisions)[0]
    assert decision.quality == "artifact"
    assert decision.display_context["display_filter"] == {"low": None, "high": None}
    wait_for_view(app, window)
    assert window.session.view_context["display_filter"] == {"low": 2.0, "high": 30.0}
    window.session.propose(Processing(excluded_ics=(0,)))
    window.session.adopt()
    old_decision = dict(window.session.review.decisions)[0]
    window.mark_selected(False)  # adopted state is not drawn yet: cannot label an unseen preview
    assert dict(window.session.review.decisions)[0] == old_decision
    window.request_render()
    wait_for_view(app, window)
    window.mark_selected(False)
    assert dict(window.session.review.decisions)[0].processing.excluded_ics == (0,)
    window.close()


def test_continuous_marks_follow_the_displayed_sample_window_and_toggle():
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    source = make_demo(18, 32, 100.0, 1.0)
    ds = replace(
        source,
        data=np.concatenate(source.data, axis=1)[None],
        continuous=True,
        tmin=0.0,
        trial_ids=("1",),
        conditions={},
    )
    window = MainWindow()
    window.set_dataset(ds)
    window.show()
    wait_for_view(app, window)
    window.page(1)
    window.select_interval(2.5, 5.0)
    window.mark_selected(True)  # The new sample window has not been drawn yet.
    assert not window.session.review.intervals
    wait_for_view(app, window)
    window.toggle_current()
    (decision,) = window.session.review.intervals
    assert (decision.start, decision.stop) == (1250, 1500)
    window.toggle_current()
    assert not window.session.review.intervals
    window.close()


def test_new_decomposition_stays_candidate_and_old_decision_needs_review(monkeypatch):
    from uuid import uuid4

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    ds = make_demo(18, 12, 100.0, 1.0)
    window.set_dataset(ds)
    window.show()
    wait_for_view(app, window)
    window.waveform.setXRange(0.4, 1.2, padding=0)
    view_range = window.waveform.getViewBox().viewRange()[0]
    window.set_workspace(1)
    wait_for_view(app, window)
    window.set_workspace(0)
    wait_for_view(app, window)
    np.testing.assert_allclose(window.waveform.getViewBox().viewRange()[0], view_range)
    window.mark_selected(True)
    window.freeze_reference()
    parent = Processing(average_reference=True)
    window.session.propose(parent)
    window.session.adopt()
    new = replace(ds.ica, decomposition_id=str(uuid4()), input_processing=parent, fit={"data_id": ds.data_id})
    window.session.propose_ica(new)
    window._populate_ics()
    window._changed()
    wait_for_view(app, window)
    assert window.session.review.processing == parent
    assert window.session.candidate.decomposition_id == new.decomposition_id
    assert new.decomposition_id[:8] in window.ic_context.text()
    np.testing.assert_allclose(window.session.component_dataset().data, window.session.engine.signal(parent))
    seen = []

    def classify(dataset):
        seen.append(dataset.ica.decomposition_id)
        return {"algorithm": "test", "backend": "test", "decomposition_id": dataset.ica.decomposition_id}

    monkeypatch.setattr("eegqc.ui.workflows.classify_ics", classify)
    monkeypatch.setattr(window, "show_iclabel", lambda report: None)
    window.run_iclabel()
    wait_for_view(app, window)
    assert seen == [new.decomposition_id]
    window.adopt()
    wait_for_view(app, window)
    assert not window.session.review.rejected
    assert "旧判断" in window.current_status.text()
    assert "待复核" in window.navigator.item(0).toolTip()
    window.mark_selected(False)
    assert dict(window.session.review.decisions)[0].processing.decomposition_id == new.decomposition_id
    assert "已确认保留" in window.navigator.item(0).toolTip()
    window.restore_reference()
    assert window.session.review.rejected == {0}
    assert window.session.ica_for() is ds.ica
    assert "已拒绝" in window.navigator.item(0).toolTip()
    window._history(False)
    assert window.session.ica_for() is new
    assert "已确认保留" in window.navigator.item(0).toolTip()
    wait_for_view(app, window)
    window.close()


def test_completed_task_does_not_replace_later_processing_and_basic_qc_needs_no_ica():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    ds = replace(make_demo(18, 12, 100.0, 1.0), ica=None, chanlocs=[{} for _ in range(18)])
    window.set_dataset(ds)
    window.show()
    wait_for_view(app, window)
    assert window.session.ica_for() is None
    assert not (window.channel_table.item(0, 1).flags() & Qt.ItemFlag.ItemIsUserCheckable)
    s = window.session
    original = s.review.processing
    candidate = Processing(dropped=(1,))
    window._propose(candidate)
    window._receive_task_candidate(
        s, original, original, lambda: window._propose(Processing(average_reference=True))
    )
    assert s.candidate == candidate
    assert window.task_result.isVisible()
    window._show_task_candidate()
    assert s.candidate.average_reference
    window.reset_preview()
    wait_for_view(app, window)
    window.mark_selected(True)
    assert s.review.rejected == {0}
    window.set_workspace(1)
    wait_for_view(app, window)
    assert "已采用" in window.property_label.text()
    assert window.channel_location.text() == "缺少电极位置"
    window.close()
