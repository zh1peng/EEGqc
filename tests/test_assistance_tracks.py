import json
import os
from threading import Event
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.epochlabel.model import FEATURES, predict_epochs
from eegqc.ui.app import configure_app
from eegqc.ui.main_window import MainWindow
from test_ui_workflow import wait_for_view


def test_parallel_indicators_align_navigate_and_keep_independent_parameters(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    window = MainWindow()
    window.set_dataset(make_demo(18, 30, 100.0, 1.0))
    window.resize(1366, 860)
    window.show()
    try:
        panel = window.epochrank
        amplitude = panel.tracks[0]
        amplitude.limit.setValue(30)
        other_amplitude = panel.add_track("amplitude")
        other_amplitude.limit.setValue(150)
        window.session.mark([4], True)
        window.session.mark([0], False)
        window._changed()
        similarity = panel.add_track("similarity")
        classifier = panel.add_track("classifier")
        wait_for_view(app, window)
        QTest.qWait(30)
        expected = np.max(np.abs(window.session.dataset.data), axis=(1, 2)) * 1e6
        np.testing.assert_allclose(amplitude.rail.values, expected, rtol=1e-6)
        np.testing.assert_allclose(other_amplitude.rail.values, expected, rtol=1e-6)
        assert amplitude.rail.markers == set(np.flatnonzero(expected > 30)) - {0, 4}
        assert other_amplitude.rail.markers == set(np.flatnonzero(expected > 150)) - {0, 4}
        assert {0, 4}.isdisjoint(similarity.suggestions)
        np.testing.assert_allclose(
            similarity.rail.values[similarity.ranking.epochs], 1 / (1 + similarity.ranking.scores)
        )
        np.testing.assert_allclose(
            classifier.rail.values[classifier.ranking.epochs], classifier.ranking.scores
        )
        for rail in [t.rail for t in panel.tracks]:
            assert rail.viewport().width() == window.navigator.viewport().width()
            assert (
                rail.viewport().mapToGlobal(QPoint()).x()
                == window.navigator.viewport().mapToGlobal(QPoint()).x()
            )
        before = window.session.review.decisions
        rail = amplitude.rail
        x = round(rail.track().left() + 7.5 * rail.track().width() / rail.count())
        QTest.mouseClick(rail.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, 30))
        wait_for_view(app, window)
        assert window.current_epoch == 7 and window.session.review.decisions == before
        assert all(t.rail.position == 7 and t.rail.first_visible == 5 for t in panel.tracks)
        assert window.session.suggestions[7][0].startswith("amplitude_threshold:30")
        rank_before = similarity.rail.values.copy()
        amplitude.limit.setValue(50)
        wait_for_view(app, window)
        assert other_amplitude.limit.value() == 150
        np.testing.assert_array_equal(similarity.rail.values, rank_before)
        model_track = panel.add_track("epochlabel")
        assert "no pretrained" in model_track.status.text()
        model = {
            "schema_version": 1,
            "features": list(FEATURES),
            "model_id": "test-only",
            "mean": [0] * len(FEATURES),
            "scale": [1] * len(FEATURES),
            "coefficients": [0] * len(FEATURES),
            "intercept": 0,
        }
        path = tmp_path / "model.json"
        path.write_text(json.dumps(model), encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
        model_track.load_model()
        wait_for_view(app, window)
        np.testing.assert_allclose(model_track.rail.values, predict_epochs(window.session, model))
        assert model_track.rail.markers == set(range(30)) - {0, 4}
        window._propose(Processing(average_reference=True))
        assert all(not np.isfinite(t.rail.values).any() for t in panel.tracks)
        window.reset_preview()
        wait_for_view(app, window)
        assert all(np.isfinite(t.rail.values).any() for t in panel.tracks)
        for track in list(panel.tracks):
            panel.remove_track(track)
        assert not panel.isVisible() and window.epoch_view.isVisible()
        panel.add_track()
        wait_for_view(app, window)
        assert panel.isVisible()
    finally:
        window.close()
        wait_for_view(app, window)


def test_removing_inflight_track_does_not_cancel_another_indicator(monkeypatch):
    import eegqc.ui.epochrank as ui

    started, release = Event(), Event()
    original = ui.epoch_vectors

    def slow(engine, processing):
        started.set()
        assert release.wait(5)
        return original(engine, processing)

    monkeypatch.setattr(ui, "epoch_vectors", slow)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    window.show()
    try:
        window.session.mark([4], True)
        window._changed()
        panel = window.epochrank
        slow_track = panel.add_track("similarity")
        deadline = monotonic() + 5
        while not started.is_set() and monotonic() < deadline:
            app.processEvents()
            sleep(0.005)
        assert started.is_set()
        amplitude = panel.add_track("amplitude")
        panel.remove_track(slow_track)
        release.set()
        wait_for_view(app, window)
        assert slow_track not in panel.tracks
        assert np.isfinite(amplitude.rail.values).all()
        assert not window.session.review.rejected - {4}
    finally:
        release.set()
        window.close()
        wait_for_view(app, window)
