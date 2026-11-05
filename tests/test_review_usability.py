import os
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from eegqc.application.persistence import restore_session
from eegqc.application.session import Session
from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.ui.main_window import MainWindow


def ready(app, window):
    end = monotonic() + 20
    while monotonic() < end:
        app.processEvents()
        queues = (window.jobs, window.property_jobs, window.epochrank.jobs)
        if not window.render_timer.isActive() and all(q.active is None and not q.pending for q in queues):
            app.processEvents()
            return
        sleep(0.005)
    raise AssertionError("Requested review did not finish")


@pytest.fixture
def desktop():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 16, 100.0, 1.0))
    window.set_workspace(0)
    window.show()
    ready(app, window)
    yield app, window
    window.close()
    ready(app, window)


def test_notes_survive_navigation_save_restore_without_inventing_labels(desktop, tmp_path):
    app, w = desktop
    w.epoch_note.setPlainText("needs a closer look")
    w.navigator.choose(1)
    ready(app, w)
    assert not w.session.review.decisions
    w.navigator.choose(0)
    ready(app, w)
    assert w.epoch_note.toPlainText() == "needs a closer look"
    w.session_path = tmp_path / "notes.json"
    w.epoch_note.setPlainText("saved before debounce")
    assert w.save()
    restored = Session(make_demo(18, 16, 100.0, 1.0))
    restore_session(restored, w.session_path)
    assert dict(restored.review.epoch_notes)[0] == "saved before debounce"
    assert not restored.review.decisions
    w.mark_selected(True)
    w.session.propose(Processing(average_reference=True))
    w.session.adopt()
    old = dict(w.session.review.decisions)[0]
    w.session.set_epoch_notes([0], "old judgment annotation")
    assert dict(w.session.review.decisions)[0].processing == old.processing
    assert w.session.pending_review_count == 1
    w.session.undo()
    assert dict(w.session.review.decisions)[0] == old


def test_batch_defer_clear_and_processing_counts_match_export_intent(desktop):
    app, w = desktop
    w.navigator.choose(1)
    w.navigator.choose(2, extend=True)
    ready(app, w)
    assert "2" in w.current_label.text() and "2" in w.mark_button.text()
    w.epoch_note.setPlainText("batch uncertainty")
    w.mark_selected(False, defer=True)
    assert not w.session.review.rejected
    assert w.session.review.epoch_counts(16)["deferred"] == 2
    assert all(d.note == "batch uncertainty" for _, d in w.session.review.decisions)
    w.mark_selected(False)
    assert w.session.review.epoch_counts(16)["retained"] == 2
    w.clear_selected_decisions()
    assert not w.session.review.decisions
    assert dict(w.session.review.epoch_notes)[1] == "batch uncertainty"
    w._history(False)
    ready(app, w)
    w.mark_selected(True)
    w._propose(Processing(excluded_ics=(0,)))
    assert w.candidate_bar.isVisible() and not w.batch_mark.isEnabled()
    w.adopt()
    ready(app, w)
    assert w.inspector_tabs.currentIndex() == 0
    w.set_workspace(3)
    ready(app, w)
    assert "当前已检查 0" in w.review_counts.text()
    assert "旧判断待复核 2" in w.review_counts.text()
    assert "16 epochs" in w.review_export.text()
    w.next_unreviewed(pending=True)
    assert w.current_epoch in (1, 2)


def test_rank_next_skip_auto_advance_and_actual_comparison(desktop):
    app, w = desktop
    w.session.mark([0], True)
    w.session.mark([1], False)
    w._changed()
    dock = w.epochrank
    dock.show()
    ready(app, w)
    order = list(map(int, dock.ranking.epochs))
    dock._select(dock.items.item(0))
    ready(app, w)
    dock.advance_when_ready()
    ready(app, w)
    assert w.current_epoch == order[1]
    dock.advance_when_ready()
    ready(app, w)
    assert w.current_epoch == order[2]
    before = w.session.review.decisions
    dock.compare_nearest()
    ready(app, w)
    assert dock.comparison_dialog.isVisible()
    assert w.session.review.decisions == before
    dock.comparison_dialog.close()
    chosen = w.current_epoch
    w.auto_advance.setChecked(True)
    w.mark_selected(True)
    ready(app, w)
    assert chosen in w.session.review.rejected
    assert w.current_epoch != chosen and w.current_epoch == int(dock.ranking.epochs[0])
    assert w.current_epoch not in w.session.review.rejected


def test_contextual_inspector_and_small_window_with_erp(desktop):
    app, w = desktop
    w.set_workspace(1)
    ready(app, w)
    assert w.object_stack.currentIndex() == 1 and not w.mark_button.isVisible()
    assert w.session.dataset.labels[0] in w.object_title.text()
    w.set_workspace(2)
    w.show_ic_property(0)
    ready(app, w)
    assert w.object_title.text() == "IC 01"
    curves = w.object_activity.listDataItems()
    assert len(curves) == 1 and np.isfinite(curves[0].yData).all()
    assert w.object_spectrum.y() >= w.object_activity.geometry().bottom()
    assert w.object_compare.y() >= w.object_spectrum.geometry().bottom()
    w.object_exclude.setChecked(True)
    assert w.ic_checkboxes[0].isChecked()
    w.ic_checkboxes[0].setChecked(False)
    assert not w.object_exclude.isChecked()
    w.set_workspace(0)
    w.erp_dock.show()
    w.resize(1260, 643)
    ready(app, w)
    assert w.height() <= 643 and w.width() <= 1260
    assert w.central_scroll.verticalScrollBar().maximum() > 0
    assert w.waveform.height() >= 220 and w.erp_plot.height() >= 160
