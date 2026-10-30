"""Qt polling adapter for the isolated scientific subprocess."""

from PySide6.QtCore import QObject, QTimer, Signal

from eegqc.application.long_tasks import LongTaskRunner


class LongTasks(QObject):
    changed = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runner = LongTaskRunner()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        self._callback = None
        self._valid = None
        self._delivered = None

    @property
    def busy(self):
        record = self.runner.record
        return self.runner.busy or (record is not None and record.status == "running")

    def submit(
        self,
        kind,
        function,
        args=(),
        kwargs=None,
        *,
        data_id,
        input_state,
        parameters=None,
        callback,
        valid=None,
        output_path=None,
    ):
        record = self.runner.start(
            kind,
            function,
            *args,
            data_id=data_id,
            input_state=input_state,
            parameters=parameters,
            output_path=output_path,
            **(kwargs or {}),
        )
        self._callback, self._valid = callback, valid
        self.changed.emit(record)
        self.timer.start()
        return record

    def _poll(self):
        record = self.runner.poll()
        if record is None:
            return
        self.changed.emit(record)
        if record.status == "running" or self.runner.busy:
            return
        if self._delivered != record.task_id:
            self._delivered = record.task_id
            callback, valid = self._callback, self._valid
            self._callback = self._valid = None
            self.timer.stop()
            if record.status == "succeeded" and (valid is None or valid()):
                callback(record.result)
            elif record.status == "failed":
                self.failed.emit(record.error)

    def cancel(self):
        self.runner.cancel()
        self._poll()

    def invalidate(self):
        self.cancel()

    def close(self):
        self.timer.stop()
        self.runner.close()
