import os
from threading import Event
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from eegqc.ui.jobs import Jobs


def test_late_previous_calculation_cannot_replace_the_latest_view():
    app = QApplication.instance() or QApplication([])
    jobs = Jobs()
    started, release = Event(), Event()
    displayed = []

    def previous():
        started.set()
        release.wait(3)
        return "previous"

    jobs.submit("waveform", previous, displayed.append)
    assert started.wait(3)
    jobs.submit("waveform", lambda: "latest", displayed.append)
    release.set()
    deadline = monotonic() + 5
    while (jobs.active is not None or jobs.pending) and monotonic() < deadline:
        app.processEvents()
        sleep(0.005)
    jobs.pool.waitForDone()
    app.processEvents()
    assert displayed == ["latest"]
