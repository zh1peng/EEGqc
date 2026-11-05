import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from eegqc.core.state import DisplayFilter, Processing
from eegqc.demo import make_demo
from eegqc.ui.app import configure_app
from eegqc.ui.main_window import MainWindow
from test_ui_workflow import wait_for_view


def waveform_values(window, row=0):
    wave = window.waveform
    _, values = wave.curves[row].getData()
    return values[: wave.samples] - (len(wave.channels) - 1 - row) * wave.channel_spacing


def test_channel_label_popup_and_ic_removal_update_fixed_waveform_and_restore():
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    window.show()
    try:
        window.navigator.choose(5)
        window.channel_start.setValue(3)
        wait_for_view(app, window)
        QTest.qWait(30)
        wave = window.waveform
        before = waveform_values(window).copy()
        geometry = wave.geometry()
        view_range = wave.getViewBox().viewRange()
        y = wave.getViewBox().mapViewToScene(QPointF(0, 15 * wave.channel_spacing)).y()
        label = wave.mapFromScene(QPointF(25, y))
        QTest.mouseClick(wave.viewport(), Qt.MouseButton.LeftButton, pos=label)
        wait_for_view(app, window)
        assert window.channel_dialog.isVisible() and not window.channel_dialog.isModal()
        assert window.channel_table.currentRow() == 2
        assert wave.geometry() == geometry
        assert window.current_epoch == 5 and not window.session.review.decisions
        window.bad_channel.click()
        wait_for_view(app, window)
        assert window.session.candidate.interpolated == (2,)
        expected = (
            window.session.engine.signal(window.session.candidate, epochs=[5], channels=[2])[0, 0] * 1e6
        )
        np.testing.assert_allclose(waveform_values(window), expected, atol=1e-10)
        assert not np.allclose(waveform_values(window), before)
        assert "Interpolate F3" in window.preview_label.text()
        window.channel_dialog.close()
        assert window.session.candidate.interpolated == (2,)
        window.ic_tool.click()
        wait_for_view(app, window)
        card = window.ic_grid.itemAt(0).widget()
        card.click()
        wait_for_view(app, window)
        assert window.session.candidate.excluded_ics == (0,)
        expected = (
            window.session.engine.signal(window.session.candidate, epochs=[5], channels=[2])[0, 0] * 1e6
        )
        np.testing.assert_allclose(waveform_values(window), expected, atol=1e-10)
        card.click()
        window.show_channel_property(2)
        wait_for_view(app, window)
        window.bad_channel.click()
        wait_for_view(app, window)
        assert window.session.candidate == Processing()
        np.testing.assert_allclose(waveform_values(window), before, atol=1e-10)
        np.testing.assert_allclose(wave.getViewBox().viewRange(), view_range)
        assert window.current_epoch == 5 and window.epoch_view.isVisible()
        assert window.inspector.isAncestorOf(window.dataset_note)
        window.navigation.hide()
        app.processEvents()
        assert not window.navigation.isVisible() and window.epoch_view.isVisible()
    finally:
        window.close()
        wait_for_view(app, window)


def test_right_click_and_drag_map_displayed_samples_and_follow_processing(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.set_dataset(make_demo(18, 12, 100.0, 1.0))
    window.show()
    captured = []
    monkeypatch.setattr(
        "eegqc.ui.main_window.signal_topography",
        lambda ds, channels, values: (
            captured.append((channels, values)) or np.zeros((20, 20, 4), dtype=np.uint8)
        ),
    )
    try:
        window.navigator.choose(5)
        window.display_filter = DisplayFilter(low=1.0, high=30.0)
        window.request_render()
        wait_for_view(app, window)
        wave = window.waveform

        def point(x):
            return wave.mapFromScene(wave.getViewBox().mapViewToScene(QPointF(x, 350)))

        before = wave.getViewBox().viewRange()
        QTest.mouseClick(wave.viewport(), Qt.MouseButton.RightButton, pos=point(0.4))
        wait_for_view(app, window)
        epoch, start, stop = window.topography_selection
        assert epoch == 5 and stop == start + 1 and abs(start - 40) <= 1
        expected = (
            window.session.engine.signal(
                epochs=[epoch], samples=slice(start, stop), display_filter=window.display_filter
            )[0].mean(axis=1)
            * 1e6
        )
        np.testing.assert_allclose(captured[-1][1], expected)
        QTest.mousePress(wave.viewport(), Qt.MouseButton.RightButton, pos=point(1.2))
        QTest.mouseMove(wave.viewport(), point(1.6))
        QTest.mouseRelease(wave.viewport(), Qt.MouseButton.RightButton, pos=point(1.6))
        wait_for_view(app, window)
        epoch, start, stop = window.topography_selection
        assert epoch == 6 and abs(start - 20) <= 1 and abs(stop - 61) <= 1
        np.testing.assert_allclose(wave.getViewBox().viewRange(), before)
        proc = Processing(excluded_ics=(0,), interpolated=(2,), dropped=(3,))
        window._propose(proc)
        wait_for_view(app, window)
        channels, values = captured[-1]
        assert 3 not in channels and len(channels) == 17
        expected = (
            window.session.engine.signal(
                proc,
                epochs=[epoch],
                channels=channels,
                samples=slice(start, stop),
                display_filter=window.display_filter,
            )[0].mean(axis=1)
            * 1e6
        )
        np.testing.assert_allclose(values, expected)
        assert "Preview" in window.topography_caption.text()
        QTest.mousePress(wave.viewport(), Qt.MouseButton.RightButton, pos=point(1.8))
        QTest.mouseMove(wave.viewport(), point(2.4))
        QTest.mouseRelease(wave.viewport(), Qt.MouseButton.RightButton, pos=point(2.4))
        wait_for_view(app, window)
        assert window.topography_selection[0] == 6 and window.topography_selection[2] == 100
        np.testing.assert_allclose(wave.getViewBox().viewRange()[0], before[0])
        assert not window.session.review.decisions
        window.set_dataset(replace(window.session.dataset, chanlocs=[{} for _ in range(18)]))
        wait_for_view(app, window)
        assert not window.topography_dialog.isVisible()
        window.show_topography(0, 10, 11)
        assert "electrode locations" in window.topography_caption.text()
        assert window.topography_image.pixmap().isNull()
    finally:
        window.close()
        wait_for_view(app, window)
