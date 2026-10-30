from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from eegqc.core.dataset import Dataset
from eegqc.core.conditions import normalize_condition
from eegqc.core.state import DisplayFilter, Processing


@dataclass
class ERPResult:
    data: np.ndarray | None
    count: int
    times: np.ndarray


@dataclass
class _Summary:
    members: np.ndarray
    total: np.ndarray
    count: int
    rejected: frozenset[int]


class PreviewEngine:
    """A small linear-processing engine; no Qt objects or mutable signal snapshots."""

    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self._operators: OrderedDict[Processing, np.ndarray] = OrderedDict()
        self._summaries: OrderedDict[tuple, _Summary] = OrderedDict()
        self._interpolation: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._base_signals: OrderedDict[object, np.ndarray] = OrderedDict()
        self._filtered: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._stage_files = {}

    def install_asr(self, config, signal):
        if signal.shape != self.dataset.data.shape or not np.isfinite(signal).all():
            raise ValueError("ASR result does not match the dataset signal")
        signal.flags.writeable = False
        self._base_signals[config] = signal
        if len(self._base_signals) > 2:
            self._base_signals.popitem(last=False)

    def source(self, processing: Processing, display_filter: DisplayFilter | None = None):
        data = self.dataset.data
        if processing.decomposition_id is not None and processing.asr is None:
            key = processing.decomposition_id
            if key not in self._base_signals:
                ica = self.dataset.ica_for(processing)
                data = self.signal(ica.input_processing)
                data.flags.writeable = False
                self._base_signals[key] = data
                if len(self._base_signals) > 2:
                    self._base_signals.popitem(last=False)
            self._base_signals.move_to_end(key)
            data = self._base_signals[key]
        elif processing.asr is not None:
            config = processing.asr
            if config not in self._base_signals:
                if config in self._stage_files:
                    path, key = self._stage_files[config]
                    with np.load(path, allow_pickle=False) as arrays:
                        data = arrays[key]
                else:
                    from eegqc.adapters.algorithms import asr_signal

                    data = asr_signal(self.dataset, self.signal(config.parent), config)
                data.flags.writeable = False
                self._base_signals[config] = data
                if len(self._base_signals) > 2:
                    self._base_signals.popitem(last=False)
            self._base_signals.move_to_end(config)
            data = self._base_signals[config]
        if display_filter is not None and display_filter != DisplayFilter():
            key = (processing.decomposition_id, processing.asr, display_filter)
            if key not in self._filtered:
                from eegqc.adapters.algorithms import filter_signal

                self._filtered[key] = filter_signal(self.dataset, data, display_filter)
                if len(self._filtered) > 2:
                    self._filtered.popitem(last=False)
            self._filtered.move_to_end(key)
            data = self._filtered[key]
        return data

    def operator(self, processing: Processing) -> np.ndarray:
        if processing in self._operators:
            self._operators.move_to_end(processing)
            return self._operators[processing]
        ds = self.dataset
        transform = np.eye(ds.n_channels)
        if processing.excluded_ics:
            ica = ds.ica_for(processing)
            if ica is None:
                raise ValueError("This dataset has no ICA decomposition")
            if any(i < 0 or i >= ica.count for i in processing.excluded_ics):
                raise ValueError("Excluded IC index is outside this decomposition")
            keep = np.ones(ica.count, dtype=bool)
            keep[list(processing.excluded_ics)] = False
            # EEGLAB pop_subcomp rebuilds from retained components, including reduced-rank ICA.
            projection = ica.mixing[:, keep] @ ica.unmixing[keep]
            transform[np.ix_(ica.channels, ica.channels)] = projection
        if processing.interpolated:
            from eegqc.adapters.eeglab import interpolation_operator

            if processing.interpolated not in self._interpolation:
                self._interpolation[processing.interpolated] = interpolation_operator(
                    ds, processing.interpolated
                )
                if len(self._interpolation) > 16:
                    self._interpolation.popitem(last=False)
            self._interpolation.move_to_end(processing.interpolated)
            transform = self._interpolation[processing.interpolated] @ transform
        if processing.average_reference:
            eeg = [
                i
                for i, loc in enumerate(ds.chanlocs)
                if str(loc.get("type", "EEG")).upper() in ("", "EEG") and i not in processing.dropped
            ]
            if not eeg:
                raise ValueError("Average reference requires retained EEG channels")
            ref = transform[eeg].mean(axis=0)
            transform[eeg] -= ref
        self._operators[processing] = transform
        if len(self._operators) > 24:
            self._operators.popitem(last=False)
        return transform

    def signal(
        self,
        processing=Processing(),
        epochs=slice(None),
        channels=None,
        samples=slice(None),
        *,
        display_filter=None,
    ):
        source = self.source(processing, display_filter)[epochs, :, samples]
        if source.ndim == 2:
            source = source[np.newaxis]
        transform = self.operator(processing)
        if channels is not None:
            transform = transform[np.asarray(channels, dtype=int)]
        return np.einsum("ij,ejt->eit", transform, source, optimize=True)

    def erp(
        self,
        processing=Processing(),
        rejected=frozenset(),
        *,
        condition=None,
        channels=None,
        baseline: tuple[float, float] | None = None,
        display_filter: DisplayFilter | None = None,
    ) -> ERPResult:
        ds = self.dataset
        if ds.continuous:
            raise ValueError("ERP requires epoched data")
        condition = normalize_condition(condition)
        key = (processing.decomposition_id, processing.asr, display_filter, condition)
        summary = self._summaries.get(key)
        if summary is None:
            members = condition.mask(ds)
            data = self.source(processing, display_filter)
            # Accumulate in float64, without making a full float64 copy of the recording.
            total = np.zeros(ds.data.shape[1:], dtype=np.float64)
            for start in range(0, ds.n_epochs, 32):
                chunk = data[start : start + 32]
                mask = members[start : start + 32]
                total += np.sum(chunk[mask], axis=0, dtype=np.float64)
            summary = _Summary(members, total, int(members.sum()), frozenset())
            self._summaries[key] = summary
            if len(self._summaries) > 8:
                self._summaries.popitem(last=False)
        self._summaries.move_to_end(key)
        for epoch in summary.rejected.symmetric_difference(rejected):
            if summary.members[epoch]:
                sign = -1 if epoch in rejected else 1
                summary.total += sign * self.source(processing, display_filter)[epoch]
                summary.count += sign
        summary.rejected = frozenset(rejected)
        if not summary.count:
            return ERPResult(None, 0, ds.times)
        transform = self.operator(processing)
        if channels is not None:
            transform = transform[np.asarray(channels, dtype=int)]
        mean = transform @ (summary.total / summary.count)
        if baseline is not None:
            mask = (ds.times >= baseline[0] - 1e-12) & (ds.times <= baseline[1] + 1e-12)
            if not mask.any():
                raise ValueError("Baseline interval contains no samples")
            mean -= mean[:, mask].mean(axis=1, keepdims=True)
        return ERPResult(mean, summary.count, ds.times)

    def amplitude_candidates(self, processing: Processing, threshold_uv: float) -> np.ndarray:
        result = []
        channels = [i for i in range(self.dataset.n_channels) if i not in processing.dropped]
        for start in range(0, self.dataset.n_epochs, 16):
            block = self.signal(processing, slice(start, start + 16), channels)
            result.extend(
                (start + np.flatnonzero(np.max(np.abs(block), axis=(1, 2)) > threshold_uv * 1e-6)).tolist()
            )
        return np.asarray(result, dtype=int)
