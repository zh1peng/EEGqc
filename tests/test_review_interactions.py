import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from eegqc.adapters.eeglab import component_properties
from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.ui.epoch_rail import EpochRail
from eegqc.ui.main_window import MainWindow
from test_ui_workflow import wait_for_view


def test_waveform_click_toggles_only_shown_epoch_and_drag_never_pans():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    window.show()
    window.set_workspace(0)
    try:
        wait_for_view(app, window)
        wave = window.waveform
        view = wave.getViewBox()
        before = view.viewRange()
        point = wave.mapFromScene(view.mapViewToScene(QPointF(0.4, np.mean(before[1]))))
        QTest.mousePress(wave.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(wave.viewport(), point + QPoint(80, 0))
        QTest.mouseRelease(wave.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(80, 0))
        np.testing.assert_allclose(view.viewRange(), before)
        assert not window.session.review.decisions
        QTest.mouseClick(wave.viewport(), Qt.MouseButton.LeftButton, pos=point)
        wait_for_view(app, window)
        assert window.session.review.rejected == {0}
        QTest.mouseClick(wave.viewport(), Qt.MouseButton.LeftButton, pos=point)
        wait_for_view(app, window)
        assert not window.session.review.rejected
        assert dict(window.session.review.decisions)[0].quality == "acceptable"
        window._propose(Processing(average_reference=True))
        QTest.mouseClick(wave.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert not window.session.review.rejected  # Unapplied previews cannot receive labels.
    finally:
        window.close()
        wait_for_view(app, window)


def test_context_properties_use_correct_signal_and_ic_cards_toggle_preview():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    window.show()
    window.channel_tool.click()
    try:
        wait_for_view(app, window)
        assert window.channel_dialog.isVisible() and window.current_label.isVisible()
        assert not window.channel_dialog.isModal()
        _, mean = window.channel_properties.average_curve.getData()
        np.testing.assert_allclose(mean, window.session.dataset.data[:, 0].mean(axis=0) * 1e6, rtol=1e-5)
        window.set_workspace(2)
        wait_for_view(app, window)
        card = window.ic_grid.itemAt(0).widget()
        QTest.mouseClick(card, Qt.MouseButton.LeftButton)
        wait_for_view(app, window)
        assert card.isChecked() and window.session.candidate.excluded_ics == (0,)
        assert window.session.review.processing.excluded_ics == ()
        activations, _, _ = component_properties(window.session.component_dataset(), 0)
        np.testing.assert_allclose(window.ic_properties.average_curve.getData()[1], activations.mean(axis=0))
        assert window.inspector_tabs.tabText(0) == "Epoch details"
        assert window.current_label.isVisible() and window.epoch_view.isVisible()
        QTest.mouseClick(card, Qt.MouseButton.LeftButton)
        wait_for_view(app, window)
        assert not card.isChecked() and not window.session.candidate.excluded_ics
        window.set_workspace(0)
        assert window.current_label.isVisible()
    finally:
        window.close()
        wait_for_view(app, window)


def test_hover_waits_for_dwell_and_cancels_on_drag_or_leave(monkeypatch):
    app = QApplication.instance() or QApplication([])
    rail = EpochRail()
    rail.addItems([str(i) for i in range(100)])
    rail.resize(816, 48)
    rail.show()
    app.processEvents()
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: shown.append(args))
    try:
        QTest.mouseMove(rail.viewport(), QPoint(100, 24))
        QTest.qWait(150)
        assert not shown
        QTest.qWait(600)
        assert len(shown) == 1
        app.sendEvent(rail.viewport(), QHelpEvent(QEvent.Type.ToolTip, QPoint(100, 24), QPoint(100, 24)))
        assert len(shown) == 1  # Native list tooltips must not bypass the rail's dwell timer.
        QTest.mouseMove(rail.viewport(), QPoint(300, 24))
        QTest.mousePress(rail.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(300, 24))
        QTest.qWait(700)
        assert len(shown) == 1
        QTest.mouseRelease(rail.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(300, 24))
        QTest.mouseMove(rail.viewport(), QPoint(400, 24))
        rail.leaveEvent(None)
        assert not rail.hover_timer.isActive()
    finally:
        rail.close()


def test_amplitude_indicator_is_visible_and_never_auto_labels():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    window.show()
    try:
        session = window.session
        track = window.epochrank.tracks[0]
        track.limit.setValue(1)
        wait_for_view(app, window)
        assert window.epoch_view.isVisible() and track.rail.markers
        chosen = min(track.rail.markers)
        track.select_epoch(chosen)
        assert not session.review.decisions
        session.mark([chosen], True)
        window._changed()
        wait_for_view(app, window)
        assert chosen not in track.rail.markers
        window._propose(Processing(average_reference=True))
        assert not np.isfinite(track.rail.values).any()
        window.adopt()
        wait_for_view(app, window)
        expected = np.max(np.abs(session.engine.signal(session.review.processing)), axis=(1, 2)) * 1e6
        np.testing.assert_allclose(track.rail.values, expected)
    finally:
        window.close()
        wait_for_view(app, window)
