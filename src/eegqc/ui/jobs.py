import traceback
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)


class _Job(QRunnable):
    def __init__(self, key, ticket, function):
        super().__init__()
        self.key, self.ticket, self.function = key, ticket, function
        self.signals = _Signals()

    def run(self):
        try:
            result = self.function()
            payload = (self.key, self.ticket, result, None)
        except Exception as error:  # application boundary: report once, keep existing work
            traceback.print_exc()
            payload = (self.key, self.ticket, None, str(error))
        self.signals.done.emit(payload)


class Jobs(QObject):
    """One numeric worker; repeated view requests coalesce instead of building a queue."""

    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.tickets = {}
        self.pending = OrderedDict()
        self.active = None
        self.callbacks = {}

    def submit(self, key, function, callback):
        ticket = self.tickets.get(key, 0) + 1
        self.tickets[key] = ticket
        self.pending[key] = (ticket, function, callback)
        self._next()

    def invalidate(self):
        self.pending.clear()
        for key in self.tickets:
            self.tickets[key] += 1

    def _next(self):
        if self.active is not None or not self.pending:
            return
        key, (ticket, function, callback) = self.pending.popitem(last=False)
        self.callbacks[(key, ticket)] = callback
        job = _Job(key, ticket, function)
        job.signals.done.connect(self._finished)
        self.active = job
        self.pool.start(job)

    def _finished(self, payload):
        key, ticket, result, error = payload
        callback = self.callbacks.pop((key, ticket))
        self.active = None
        if self.tickets[key] == ticket:
            if error:
                self.failed.emit(error)
            else:
                callback(result)
        self._next()
