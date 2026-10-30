"""One ICA backend: EEGPrep 0.4 Extended Infomax, with explicit fit selection."""

from dataclasses import dataclass

import numpy as np

from eegqc.core.dataset import ICA
from eegqc.core.intervals import retained_segments, merged_intervals
from eegqc.core.state import Processing


@dataclass
class ICAFitInput:
    data: np.ndarray  # channels × selected samples, V
    channels: tuple[int, ...]
    rank: int
    input_processing: Processing
    epoch_indices: tuple[int, ...]
    sample_intervals: tuple[tuple[int, int], ...]
    params: dict

    @property
    def sample_count(self):
        return self.data.shape[1]


def prepare_ica(session, channels=None, sample_scope="retained", n_components=None, max_iter=512, seed=None):
    ds, review = session.dataset, session.review
    parent = review.processing
    if channels is None:
        channels = [
            i
            for i, loc in enumerate(ds.chanlocs)
            if str(loc.get("type", "EEG")).upper() in ("", "EEG") and i not in parent.dropped
        ]
    channels = tuple(int(i) for i in channels)
    if (
        len(channels) < 2
        or len(set(channels)) != len(channels)
        or any(i < 0 or i >= ds.n_channels or i in parent.dropped for i in channels)
    ):
        raise ValueError("ICA requires at least two distinct retained channels")
    if sample_scope not in ("retained", "all"):
        raise ValueError("ICA sample scope must be retained or all")
    if max_iter < 1 or (seed is not None and not 0 <= seed < 2**32):
        raise ValueError("ICA needs positive iterations and a seed in [0, 2**32)")
    epochs = tuple(i for i in range(ds.n_epochs) if sample_scope == "all" or i not in review.rejected)
    intervals = ()
    if ds.continuous:
        removed = merged_intervals(review.active_intervals) if sample_scope == "retained" else ()
        intervals = tuple((start, stop) for start, stop, _ in retained_segments(ds.data.shape[2], removed))
        signal = session.engine.signal(parent, [0], channels)[0]
        data = (
            np.concatenate([signal[:, start:stop] for start, stop in intervals], axis=1)
            if intervals
            else signal[:, :0]
        )
    else:
        signal = session.engine.signal(parent, list(epochs), channels)
        data = signal.transpose(1, 0, 2).reshape(len(channels), -1)
    if data.shape[1] < len(channels) + 1 or not np.isfinite(data).all():
        raise ValueError("ICA requires enough finite selected samples")
    # Fit rank is the centered signal's rank, matching runica's row-mean removal.
    centered = data - data.mean(axis=1, keepdims=True)
    singular = np.linalg.svd(centered, compute_uv=False)
    tolerance = max(centered.shape) * np.finfo(centered.dtype).eps * singular[0]
    rank = int(np.count_nonzero(singular > tolerance))
    if rank < 2:
        raise ValueError("ICA requires effective rank of at least two")
    count = rank if n_components is None else int(n_components)
    if not 2 <= count <= rank:
        raise ValueError(f"ICA component count must be between 2 and effective rank {rank}")
    params = {
        "data_id": ds.data_id,
        "backend": "eegprep-0.4.0",
        "algorithm": "extended-infomax",
        "channel_labels": [ds.labels[i] for i in channels],
        "sample_scope": sample_scope,
        "n_components": count,
        "max_iter": int(max_iter),
        "seed": 5489 if seed is None else int(seed),
        "centering": "backend training row means; EEGLAB uncentered activations/reconstruction",
    }
    return ICAFitInput(np.asarray(data, dtype=np.float64), channels, rank, parent, epochs, intervals, params)


def fit_ica(prepared: ICAFitInput) -> ICA:
    from eegprep import pop_runica

    values = prepared.data * 1e6
    n, samples = values.shape
    eeg = {
        "data": values,
        "nbchan": n,
        "pnts": samples,
        "trials": 1,
        "srate": 1.0,
        "xmin": 0.0,
        "xmax": float(samples - 1),
        "chanlocs": [],
        "etc": {},
    }
    options = {"extended": 1, "maxsteps": prepared.params["max_iter"], "verbose": "off"}
    if prepared.params["n_components"] < n:
        options["pca"] = prepared.params["n_components"]
    if prepared.params["seed"] is not None:
        options["seed"] = prepared.params["seed"]
    result = pop_runica(eeg, icatype="runica", options=options, gui=False)
    # runica centers its training copy. EEGPrep applies returned matrices to the
    # uncentered input, including pop_subcomp reconstruction: preserve that convention.
    unmixing = np.asarray(result["icaweights"]) @ np.asarray(result["icasphere"])
    fit = dict(
        prepared.params,
        rank=prepared.rank,
        epoch_indices=list(prepared.epoch_indices),
        sample_intervals=[list(p) for p in prepared.sample_intervals],
        sample_count=samples,
    )
    return ICA(
        np.asarray(prepared.channels),
        unmixing * 1e6,
        np.asarray(result["icawinv"]) * 1e-6,
        input_processing=prepared.input_processing,
        fit=fit,
    )
