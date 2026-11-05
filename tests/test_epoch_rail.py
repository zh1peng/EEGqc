import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.ui.epoch_rail import EpochRail
from eegqc.ui.main_window import MainWindow
from test_ui_workflow import wait_for_view


def test_scrubbing_and_keyboard_keep_exact_epoch_targets():
    app = QApplication.instance() or QApplication([])
    rail = EpochRail()
    rail.addItems([str(i) for i in range(100)])
    rail.resize(816, 48)
    rail.show()
    app.processEvents()
    chosen = []
    rail.itemClicked.connect(lambda item: chosen.append(rail.row(item)))

    def point(index):
        track = rail.track()
        return QPoint(round(track.left() + (index + 0.5) * track.width() / rail.count()), 24)

    QTest.mousePress(rail.viewport(), Qt.MouseButton.LeftButton, pos=point(10))
    QTest.mouseMove(rail.viewport(), point(70))
    QTest.mouseRelease(rail.viewport(), Qt.MouseButton.LeftButton, pos=point(70))
    assert chosen[-1] == 70
    assert [rail.row(i) for i in rail.selectedItems()] == [70]
    QTest.mouseClick(rail.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point(74))
    assert [rail.row(i) for i in rail.selectedItems()] == [74]
    QTest.keyClick(rail, Qt.Key.Key_End)
    QTest.keyClick(rail, Qt.Key.Key_Right)
    assert rail.position == 99
    QTest.keyClick(rail, Qt.Key.Key_Home)
    QTest.keyClick(rail, Qt.Key.Key_Left)
    assert rail.position == 0
    assert [rail.row(i) for i in rail.selectedItems()] == [0]
    rail.close()


def test_epoch_first_with_optional_tools_preserves_position_and_label_targets():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    ds = make_demo(18, 20, 100.0, 1.0)
    window.set_dataset(ds)
    window.show()
    try:
        wait_for_view(app, window)
        assert window.epoch_view.isVisible()
        window.navigator.choose(5)
        window.channel_tool.click()
        assert window.channel_dialog.isVisible() and window.epoch_view.isVisible()
        window._propose(Processing(average_reference=True))
        window.channel_dialog.close()
        assert window.epoch_view.isVisible()
        assert window.current_epoch == 5
        assert window.session.candidate != window.session.review.processing
        window.channel_tool.click()
        window.adopt()
        assert window.epoch_view.isVisible()
        assert window.current_epoch == 5
        window.ic_tool.click()
        assert window.ic_dialog.isVisible() and window.epoch_view.isVisible()
        window.ic_dialog.close()
        assert window.epoch_view.isVisible()
        window.navigator.choose(2)
        wait_for_view(app, window)
        window.waveform.epoch_selected.emit(4)
        wait_for_view(app, window)
        window.mark_selected(True)
        assert window.session.review.rejected == {4}
        window.page(1)
        wait_for_view(app, window)
        window.mark_selected(True)
        assert window.session.review.rejected == {4, 5}
        window.set_dataset(replace(ds, ica=None, decompositions={}))
        assert window.epoch_view.isVisible()
        assert not window.ic_tool.isEnabled()
    finally:
        window.close()
        wait_for_view(app, window)
