from dataclasses import replace
from copy import deepcopy

from eegqc.core.dataset import Dataset
from eegqc.core.preview import PreviewEngine
from eegqc.core.state import EpochDecision, IntervalDecision, Processing, Review


class Session:
    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.engine = PreviewEngine(dataset)
        self.review = Review()
        self.candidate = self.review.processing
        self.reference = self.review
        self.revision = 0
        self.saved_revision = 0
        self.suggestions: dict[int, tuple[str, ...]] = {}
        self.view_context = {}
        self.iclabel = None
        self.contribution_groups = {}
        self.erp_conditions = {}
        self._undo: list[Review] = []
        self._redo: list[Review] = []

    def _commit(self, review: Review):
        if review == self.review:
            return
        self._undo.append(self.review)
        self._redo.clear()
        self.review = review
        self.candidate = review.processing
        self.revision += 1

    def propose(self, processing: Processing):
        self.candidate = processing
        self.revision += 1

    def adopt(self):
        self.engine.operator(self.candidate)  # validate the operation before recording it
        self._commit(replace(self.review, processing=self.candidate))

    def mark(self, epochs, rejected: bool, reason="signal_artifact", note="", origin="human"):
        epochs = tuple(epochs)
        decisions = dict(self.review.decisions)
        for epoch in epochs:
            decisions[int(epoch)] = EpochDecision(
                rejected,
                reason,
                note,
                self.review.processing,
                origin=origin,
                suggestions_seen=self.suggestions.get(int(epoch), ()),
                display_context=deepcopy(self.view_context),
                review_mode="batch" if len(epochs) > 1 else "individual",
            )
        self._commit(replace(self.review, decisions=tuple(sorted(decisions.items()))))

    def mark_interval(self, start, stop, rejected=True, reason="signal_artifact", note=""):
        if not self.dataset.continuous:
            raise ValueError("Time interval annotations require continuous data")
        if not 0 <= start < stop <= self.dataset.data.shape[2]:
            raise ValueError("The marked sample interval must be inside the recording")
        intervals = self.review.intervals
        if rejected:
            intervals = (*intervals, IntervalDecision(start, stop, self.review.processing, reason, note))
        else:
            intervals = tuple(d for d in intervals if not (d.start < stop and d.stop > start))
        self._commit(replace(self.review, intervals=intervals))

    def set_rating(self, rating: str, note: str):
        candidate = self.candidate
        self._commit(replace(self.review, rating=rating, note=note))
        self.candidate = candidate

    def undo(self):
        if self.candidate != self.review.processing:
            self.candidate = self.review.processing
            self.revision += 1
        elif self._undo:
            self._redo.append(self.review)
            self.review = self._undo.pop()
            self.candidate = self.review.processing
            self.revision += 1

    def redo(self):
        if self._redo:
            self._undo.append(self.review)
            self.review = self._redo.pop()
            self.candidate = self.review.processing
            self.revision += 1

    def freeze_reference(self):
        self.reference = self.review
        self.revision += 1
