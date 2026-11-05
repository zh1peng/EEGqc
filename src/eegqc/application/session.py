from dataclasses import replace
from copy import deepcopy
import numpy as np

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
        notes = dict(self.review.epoch_notes)
        for epoch in epochs:
            previous = decisions.get(int(epoch))
            text = note if note is not None else notes.get(int(epoch), previous.note if previous else "")
            decisions[int(epoch)] = EpochDecision(
                rejected,
                reason,
                text,
                self.review.processing,
                origin=origin,
                suggestions_seen=self.suggestions.get(int(epoch), ()),
                display_context=deepcopy(self.view_context),
                review_mode="batch" if len(epochs) > 1 else "individual",
            )
            notes.pop(int(epoch), None)
        self._commit(
            replace(
                self.review,
                decisions=tuple(sorted(decisions.items())),
                epoch_notes=tuple(sorted(notes.items())),
            )
        )

    def set_epoch_notes(self, epochs, note):
        """Editing a note does not create or renew a quality judgment."""
        decisions, notes = dict(self.review.decisions), dict(self.review.epoch_notes)
        for epoch in epochs:
            if epoch in decisions:
                decisions[epoch] = replace(decisions[epoch], note=note)
            elif note:
                notes[epoch] = note
            else:
                notes.pop(epoch, None)
        candidate = self.candidate
        self._commit(
            replace(
                self.review,
                decisions=tuple(sorted(decisions.items())),
                epoch_notes=tuple(sorted(notes.items())),
            )
        )
        self.candidate = candidate

    def clear_epoch_decisions(self, epochs):
        decisions, notes = dict(self.review.decisions), dict(self.review.epoch_notes)
        for epoch in epochs:
            decision = decisions.pop(epoch, None)
            if decision is not None and decision.note:
                notes[epoch] = decision.note
        self._commit(
            replace(
                self.review,
                decisions=tuple(sorted(decisions.items())),
                epoch_notes=tuple(sorted(notes.items())),
            )
        )

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

    def restore_reference(self):
        candidate_changed = self.candidate != self.reference.processing
        review_changed = self.review != self.reference
        self._commit(self.reference)
        self.candidate = self.review.processing
        if candidate_changed and not review_changed:
            self.revision += 1

    @property
    def pending_review_count(self):
        return self.review.pending_review_count

    def ica_for(self, processing=None):
        return self.dataset.ica_for(self.candidate if processing is None else processing)

    def component_dataset(self, processing=None):
        """The decomposition's scientific input, before current component exclusions."""
        processing = self.candidate if processing is None else processing
        ica = self.ica_for(processing)
        if ica is None:
            raise ValueError("This processing state has no ICA decomposition")
        data = (
            self.dataset.data
            if ica.input_processing is None
            else self.engine.source(Processing(decomposition_id=ica.decomposition_id))
        )
        metadata = deepcopy(self.dataset.metadata)
        metadata.update(
            icaweights=ica.unmixing * 1e-6,
            icasphere=np.eye(len(ica.channels)),
            icawinv=ica.mixing * 1e6,
            icachansind=ica.channels.copy(),
        )
        return replace(self.dataset, data=data, ica=ica, metadata=metadata)

    def propose_ica(self, ica):
        if ica.fit.get("data_id") != self.dataset.data_id:
            raise ValueError("ICA result belongs to another dataset")
        self.dataset.validate_ica(ica)
        self.dataset.decompositions[ica.decomposition_id] = ica
        self.propose(Processing(decomposition_id=ica.decomposition_id, dropped=ica.input_processing.dropped))
        self.iclabel = None
