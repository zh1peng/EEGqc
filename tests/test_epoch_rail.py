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


def test_scrubbing_range_selection_and_keyboard_keep_exact_epoch_targets():
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
    assert [rail.row(i) for i in rail.selectedItems()] == list(range(70, 75))
    QTest.keyClick(rail, Qt.Key.Key_End)
    QTest.keyClick(rail, Qt.Key.Key_Right)
    assert rail.position == 99
    QTest.keyClick(rail, Qt.Key.Key_Home)
    QTest.keyClick(rail, Qt.Key.Key_Left)
    assert rail.position == 0
    assert [rail.row(i) for i in rail.selectedItems()] == [0]
    rail.close()


def test_preprocessing_first_and_navigation_never_marks_previous_selection():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    ds = make_demo(18, 20, 100.0, 1.0)
    window.set_dataset(ds)
    window.show()
    try:
        wait_for_view(app, window)
        assert window.stack.currentIndex() == 1
        window._propose(Processing(average_reference=True))
        window._next_workspace()
        assert window.stack.currentIndex() == 1
        window.adopt()
        window._next_workspace()
        assert window.stack.currentIndex() == 2
        window._next_workspace()
        assert window.stack.currentIndex() == 0
        window.navigator.choose(2)
        window.waveform.epoch_selected.emit(4)
        wait_for_view(app, window)
        window.mark_selected(True)
        assert window.session.review.rejected == {4}
        window.page(1)
        wait_for_view(app, window)
        window.mark_selected(True)
        assert window.session.review.rejected == {4, 5}
        window.set_dataset(replace(ds, ica=None, decompositions={}))
        window._next_workspace()
        assert window.stack.currentIndex() == 0  # No unavailable ICA stage to complete.
    finally:
        window.close()
        wait_for_view(app, window)
