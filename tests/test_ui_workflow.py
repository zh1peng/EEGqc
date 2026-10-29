import os
from dataclasses import replace
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
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
        if not window.render_timer.isActive() and window.jobs.active is None and not window.jobs.pending:
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
