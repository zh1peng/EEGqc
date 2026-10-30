import os
from dataclasses import replace
from threading import Event
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from eegqc.application.epochrank import Feedback, epoch_vectors, feedback, rank_epochs
from eegqc.application.session import Session
from eegqc.core.preview import PreviewEngine
from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.ui.main_window import MainWindow


def test_similarity_keeps_distinct_bad_examples_and_never_labels_the_pool():
    vectors = np.array([[0.0], [0.25], [8.0], [7.75], [4.0], [0.125]])
    labels = Feedback(bad=(0, 2), good=(5,), reviewed=(0, 2, 5))
    result = rank_epochs(vectors, labels)
    assert result.epochs.tolist() == [1, 3, 4]  # ties retain epoch order
    assert result.nearest_bad.tolist() == [0, 2, 0]
    np.testing.assert_allclose(result.scores, [0.25, 0.25, 4.0])
    with pytest.raises(ValueError, match="at least one"):
        rank_epochs(vectors, Feedback((), (5,), (5,)))
    assert not len(rank_epochs(vectors, Feedback((0,), (), tuple(range(6)))).epochs)


def test_good_counterexample_changes_classifier_scores_but_not_similarity_geometry():
    vectors = np.array([[0.0, 0.0], [0.5, 0.0], [0.0, 0.5], [4.0, 4.0], [1.0, 0.0]])
    initial = Feedback((0,), (3,), (0, 3))
    corrected = Feedback((0,), (3, 4), (0, 3, 4))
    before = rank_epochs(vectors, initial, "classifier")
    after = rank_epochs(vectors, corrected, "classifier")
    before_scores = dict(zip(before.epochs, before.scores))
    after_scores = dict(zip(after.epochs, after.scores))
    assert before_scores[1] == pytest.approx(before_scores[2])
    assert after_scores[2] > after_scores[1]  # good example on x-axis changes the learned direction
    assert 4 not in after.epochs
    distances = rank_epochs(vectors, corrected)
    np.testing.assert_allclose(distances.scores, [0.5, 0.5])
    with pytest.raises(ValueError, match="acceptable"):
        rank_epochs(vectors, Feedback((0,), (), (0,)), "classifier")


def test_feedback_uses_only_current_human_signal_judgments_and_tracks_corrections():
    session = Session(make_demo(18, 8, 100.0, 1.0))
    session.mark([0], True)
    session.mark([1], False)
    session.mark([2], True, "behavioral")
    session.mark([3], True, "uncertain")
    session.mark([4], True, origin="model")
    assert feedback(session.review) == Feedback((0,), (1,), (0, 1, 2, 3, 4))
    session.mark([0], False)
    assert feedback(session.review).bad == ()
    session.undo()
    assert feedback(session.review).bad == (0,)
    session.propose(Processing(average_reference=True))
    session.adopt()
    assert feedback(session.review) == Feedback((), (), ())
    session.undo()
    assert feedback(session.review).bad == (0,)


def test_vectors_follow_processing_and_ignore_dropped_and_non_eeg_channels():
    ds = make_demo(18, 8, 100.0, 1.0)
    locs = [dict(loc) for loc in ds.chanlocs]
    locs[1]["type"] = "EOG"
    ds = replace(ds, chanlocs=locs)
    processing = Processing(dropped=(0,), average_reference=True)
    actual = epoch_vectors(PreviewEngine(ds), processing)
    # With no IC removal, changing an excluded/EOG input cannot change retained EEG features.
    altered = ds.data.copy()
    altered[:, :2] *= 100
    expected = epoch_vectors(PreviewEngine(replace(ds, data=altered)), processing)
    np.testing.assert_allclose(actual, expected, atol=1e-12)
    assert actual.shape == (8, 10 + 16)
    assert np.isfinite(actual).all()
    assert not np.allclose(actual, epoch_vectors(PreviewEngine(ds), Processing(dropped=(0,))))


def wait_for(app, window, ready):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        app.processEvents()
        if (
            ready()
            and not window.render_timer.isActive()
            and window.jobs.active is None
            and not window.jobs.pending
            and window.property_jobs.active is None
            and not window.property_jobs.pending
            and window.epochrank.jobs.active is None
            and not window.epochrank.jobs.pending
        ):
            return
        sleep(0.005)
    raise AssertionError("EpochRank/view did not reach the requested state")


def test_recommendation_navigation_feedback_and_undo_preserve_human_labels():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    session = window.session
    session.mark([0], True)
    session.mark([1], False)
    window.show()
    dock = window.epochrank
    dock.show()
    try:
        wait_for(app, window, lambda: dock.ranking is not None)
        before = session.review.decisions
        chosen = int(dock.ranking.epochs[0])
        dock._select(dock.items.item(0))
        wait_for(app, window, lambda: window.current_epoch == chosen)
        assert session.review.decisions == before
        assert [window.navigator.row(i) for i in window.navigator.selectedItems()] == [chosen]
        window.mark_selected(True)
        wait_for(app, window, lambda: dock.ranking is not None)
        assert chosen not in dock.ranking.epochs
        decision = dict(session.review.decisions)[chosen]
        assert decision.origin == "human" and decision.suggestions_seen[0].startswith("EpochRank:")
        window._history(False)
        wait_for(app, window, lambda: dock.ranking is not None)
        assert chosen in dock.ranking.epochs
        assert session.review.decisions == before
        window._propose(Processing(average_reference=True))
        assert dock.ranking is None and not dock.items.count()
        window.adopt()
        assert dock.ranking is None and "先标记" in dock.status.text()
    finally:
        window.close()
        wait_for(app, window, lambda: True)


def test_dataset_switch_discards_inflight_ranking(monkeypatch):
    import eegqc.ui.epochrank as ui_epochrank

    started, release = Event(), Event()
    calls = []

    def delayed_vectors(engine, processing):
        calls.append(engine.dataset.data_id)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return epoch_vectors(engine, processing)

    monkeypatch.setattr(ui_epochrank, "epoch_vectors", delayed_vectors)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 8, 100.0, 1.0))
    window.session.mark([0], True)
    window.show()
    window.epochrank.show()
    try:
        assert started.wait(5)
        first = window.session.dataset.data_id
        window.set_dataset(make_demo(18, 10, 100.0, 1.0, seed=19))
        session = window.session
        session.mark([3], True)
        window._changed()
        release.set()
        wait_for(app, window, lambda: window.epochrank.ranking is not None)
        expected = rank_epochs(
            epoch_vectors(session.engine, session.review.processing), feedback(session.review)
        )
        np.testing.assert_array_equal(window.epochrank.ranking.epochs, expected.epochs)
        assert calls == [first, session.dataset.data_id]
        assert 0 in window.epochrank.ranking.epochs and 3 not in window.epochrank.ranking.epochs
    finally:
        release.set()
        window.close()
        wait_for(app, window, lambda: True)
