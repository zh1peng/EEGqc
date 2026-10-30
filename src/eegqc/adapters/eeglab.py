from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
from eegqc import __version__

from eegqc.core.dataset import ICA, Dataset


def _records(value):
    if isinstance(value, dict):
        return [value]
    return np.asarray(value, dtype=object).ravel().tolist()


def from_eeg(eeg: dict, source: Path | None = None) -> Dataset:
    """EEGPrep dictionaries use channel × sample × epoch and EEG amplitudes in μV."""
    source_data = np.asarray(eeg["data"])
    nchan, pnts, trials = int(eeg["nbchan"]), int(eeg["pnts"]), int(eeg["trials"])
    if source_data.size != nchan * pnts * trials:
        raise ValueError("EEGLAB data dimensions do not agree with nbchan, pnts and trials")
    data = np.moveaxis(source_data.reshape(nchan, pnts, trials, order="F"), -1, 0) * 1e-6
    locs = [dict(loc) for loc in _records(eeg.get("chanlocs", []))]
    if not locs:
        locs = [{"labels": f"Ch{i + 1}"} for i in range(nchan)]
    for loc in locs:
        channel_type = np.asarray(loc.get("type", ""), dtype=object).ravel()
        loc["type"] = str(channel_type[0]).strip().upper() if channel_type.size else ""
        for key in ("X", "Y", "Z", "theta", "radius"):
            value = np.asarray(loc.get(key, []))
            if value.size > 1:
                raise ValueError(f"Channel {loc.get('labels', '?')} has a nonscalar {key} coordinate")
            loc[key] = float(value.reshape(-1)[0]) if value.size else np.nan
    labels = tuple(str(ch.get("labels", f"Ch{i + 1}")) for i, ch in enumerate(locs))
    epochs = _records(eeg.get("epoch", []))
    conditions = {}
    if len(epochs) == trials:
        for key in sorted(set().union(*(e.keys() for e in epochs))):
            values = [np.asarray(e.get(key, ""), dtype=object).ravel() for e in epochs]
            if all(v.size == 1 for v in values):
                conditions[key] = np.asarray([str(v[0]) for v in values])
        event_types = []
        for ep in epochs:
            types = np.asarray(ep.get("eventtype", "unknown"), dtype=object).ravel()
            latencies = np.asarray(ep.get("eventlatency", []), dtype=float).ravel()
            idx = int(np.argmin(np.abs(latencies))) if latencies.size == types.size else 0
            event_types.append(str(types[idx]) if types.size else "unknown")
        conditions["condition"] = np.asarray(event_types)
    trial_ids = tuple(str(i + 1) for i in range(trials))
    source_ids = eeg.get("etc", {}).get("EEGdojo", {}).get("TrialID", [])
    if np.asarray(source_ids).size != trials:
        source_ids = eeg.get("etc", {}).get("eegqc", {}).get("source_trial_ids", [])
    if np.asarray(source_ids).size == trials:
        trial_ids = tuple(str(v).rstrip() for v in np.asarray(source_ids).ravel())
    ica = None
    weights = np.asarray(eeg.get("icaweights", []))
    sphere = np.asarray(eeg.get("icasphere", []))
    if bool(weights.size) != bool(sphere.size):
        raise ValueError("ICA import requires both icaweights and icasphere")
    if weights.size and sphere.size:
        indices = np.asarray(eeg.get("icachansind", []), dtype=int).ravel()
        if not indices.size:
            indices = np.arange(nchan)
        unmixing = weights @ sphere
        mixing = np.asarray(eeg.get("icawinv", []))
        if not mixing.size:
            mixing = np.linalg.pinv(unmixing)
        ica = ICA(indices, unmixing * 1e6, mixing * 1e-6)
    metadata = {k: v for k, v in eeg.items() if k not in ("data", "icaact", "specdata", "specicaact")}
    return Dataset(
        data,
        float(eeg["srate"]),
        float(eeg.get("xmin", 0)),
        labels,
        locs,
        trial_ids,
        conditions,
        metadata,
        str(eeg.get("setname") or "Untitled"),
        source=source,
        ica=ica,
        continuous=trials == 1 and not epochs,
    )


def load_set(path: str | Path) -> Dataset:
    from eegprep import pop_loadset

    path = Path(path).resolve()
    try:
        eeg = pop_loadset(str(path))
    except np.linalg.LinAlgError:
        # EEGPrep 0.4's MAT loader squeezes a one-component ICA to vectors
        # before eeg_checkset's matrix inverse. Repair this specific layout.
        from scipy.io import loadmat
        from eegprep.functions.adminfunc.eeg_checkset import eeg_checkset
        from eegprep.functions.popfunc.pop_loadset import _load_sidecar_data
        from eegprep.functions.popfunc._file_io import normalize_icachansind, ur_indices_to_zero_based

        eeg = loadmat(path, simplify_cells=True)
        eeg = eeg.get("EEG", eeg)
        weights = np.asarray(eeg.get("icaweights", []))
        sphere = np.asarray(eeg.get("icasphere", []))
        mixing = np.asarray(eeg.get("icawinv", []))
        if weights.ndim != 1 or sphere.ndim != 2 or mixing.ndim != 1:
            raise
        eeg["icaweights"] = weights[np.newaxis]
        eeg["icawinv"] = mixing[:, np.newaxis]
        eeg["filepath"], eeg["filename"] = str(path.parent), path.name
        eeg["icachansind"] = normalize_icachansind(eeg["icachansind"], matlab_one_based=True)
        _load_sidecar_data(eeg, path, use_memmap=False)
        for key in ("chanlocs", "event"):
            if isinstance(eeg.get(key), dict):
                eeg[key] = [eeg[key]]
        ur_indices_to_zero_based(eeg)
        eeg = eeg_checkset(eeg)
    return from_eeg(eeg, path)


def interpolation_operator(dataset: Dataset, bad: tuple[int, ...]) -> np.ndarray:
    """Obtain EEGPrep's exact linear interpolation operator using a small identity signal."""
    from eegprep.functions.popfunc.eeg_interp import eeg_interp

    eeg_channels = [
        i for i, loc in enumerate(dataset.chanlocs) if str(loc.get("type", "EEG")).upper() in ("", "EEG")
    ]
    if any(i not in eeg_channels for i in bad):
        raise ValueError("Spherical interpolation is restricted to EEG channels")
    positions = np.asarray(
        [[loc.get(k, np.nan) for k in ("X", "Y", "Z")] for loc in dataset.chanlocs], dtype=float
    )
    valid = np.isfinite(positions).all(axis=1) & (np.linalg.norm(positions, axis=1) > 0)
    if not all(valid[i] for i in bad):
        raise ValueError("The selected channels need valid XYZ positions for interpolation")
    selected = [i for i in eeg_channels if valid[i]]
    if len(selected) - len(bad) < 3:
        raise ValueError("Interpolation needs at least three positioned good EEG channels")
    n = len(selected)
    local_bad = [selected.index(i) for i in bad]
    tiny = {
        "data": np.eye(n),
        "nbchan": n,
        "pnts": n,
        "trials": 1,
        "srate": 1.0,
        "xmin": 0.0,
        "xmax": float(n - 1),
        "chanlocs": [dataset.chanlocs[i] for i in selected],
    }
    local = eeg_interp(tiny, local_bad, method="spherical", dtype="float64")["data"]
    result = np.eye(dataset.n_channels)
    result[np.ix_(selected, selected)] = local
    return result


def channel_spectrum(dataset: Dataset, signal: np.ndarray):
    from eegprep.functions.sigprocfunc.spectopo import spectopo

    # Input epochs × samples in V; backend plotting/PSD convention is μV.
    values = signal.T[np.newaxis] * 1e6
    spectra, freqs, *_ = spectopo(values, values.shape[1], dataset.sfreq, plot="off")
    return freqs, spectra[0]


def component_thumbnail(dataset: Dataset, component: int):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from eegprep.functions.sigprocfunc.topoplot import topoplot

    if dataset.ica is None:
        raise ValueError("This dataset has no ICA decomposition")
    locs = [dataset.chanlocs[i] for i in dataset.ica.channels]
    figure = Figure(figsize=(2, 2), dpi=100, facecolor="#303943")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_axes([0.06, 0.04, 0.88, 0.9])
    topoplot(dataset.ica.mixing[:, component], locs, axes=axes, colormap="RdBu_r", maplimits="absmax")
    axes.set_axis_off()
    canvas.draw()
    return np.asarray(canvas.buffer_rgba()).copy()


def component_properties(dataset: Dataset, component: int):
    from eegprep.functions.sigprocfunc.spectopo import compute_spectra

    ica = dataset.ica
    activations = np.einsum(
        "c,ect->et", ica.unmixing[component], dataset.data[:, ica.channels], optimize=True
    )
    native = activations.T[np.newaxis]
    spectra, freqs, _ = compute_spectra(
        native, native.shape[1], dataset.sfreq, mapnorm=ica.mixing[:, component] * 1e6
    )
    return activations, freqs, spectra[0]


def channel_location_thumbnail(dataset, channel):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from eegprep.functions.sigprocfunc.topoplot import plot_channel_location

    figure = Figure(figsize=(1.5, 1.5), dpi=100, facecolor="#303943")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_axes([0.05, 0.05, 0.9, 0.9])
    plot_channel_location(axes, dataset.chanlocs, channel + 1, markersize=40, color="#ef736d")
    axes.set_axis_off()
    canvas.draw()
    return np.asarray(canvas.buffer_rgba()).copy()


def export_set(session, path: str | Path):
    from eegprep import pop_rejepoch, pop_saveset, pop_select
    from eegprep.functions.popfunc.eeg_eegrej import eeg_eegrej
    from eegqc.core.intervals import merged_intervals, retained_segments

    ds, review = session.dataset, session.review
    path = Path(path).resolve()
    if ds.source is not None and path == ds.source:
        raise ValueError("Choose a different output file; the source dataset is preserved")
    if len(review.rejected) == ds.n_epochs:
        raise ValueError("No epochs remain to export")
    if len(review.processing.dropped) == ds.n_channels:
        raise ValueError("No channels remain to export")
    eeg = deepcopy(ds.metadata)
    eeg["times"] = ds.times * 1000
    # Export and preview use the same operator and signal path.
    eeg["data"] = np.moveaxis(session.engine.signal(review.processing), 0, -1) * 1e6
    if ds.continuous:
        eeg["data"] = eeg["data"][:, :, 0]
    eeg["icaact"] = np.array([])
    eeg["specdata"] = np.array([])
    eeg["specicaact"] = np.array([])
    proc = review.processing
    ica = ds.ica_for(proc)
    compatible = (
        ica is not None
        and not proc.interpolated
        and not proc.average_reference
        and proc.asr is None
        and not set(proc.dropped).intersection(ica.channels)
    )
    if ica is not None and len(proc.excluded_ics) == ica.count:
        compatible = False
    if not compatible:
        for key in ("icaweights", "icasphere", "icawinv", "icachansind"):
            eeg[key] = np.array([], dtype=int if key == "icachansind" else float)
        eeg.get("etc", {}).pop("ic_classification", None)
        eeg.pop("dipfit", None)
    else:
        keep = np.ones(ica.count, dtype=bool)
        keep[list(proc.excluded_ics)] = False
        eeg["icaweights"] = ica.unmixing[keep] * 1e-6
        eeg["icasphere"] = np.eye(len(ica.channels))
        eeg["icawinv"] = ica.mixing[:, keep] * 1e6
        eeg["icachansind"] = ica.channels.copy()
        if proc.excluded_ics or proc.decomposition_id is not None:
            eeg.get("etc", {}).pop("ic_classification", None)
            eeg.pop("dipfit", None)
    eeg["reject"] = {}
    removed = merged_intervals(review.active_intervals)
    if ds.continuous and removed:
        if sum(stop - start for start, stop in removed) >= ds.data.shape[2]:
            raise ValueError("No samples remain to export")
        eeg = eeg_eegrej(eeg, np.asarray([(start + 1, stop) for start, stop in removed]))
    if review.rejected:
        eeg = pop_rejepoch(eeg, [i + 1 for i in sorted(review.rejected)], confirm=0)
    if proc.dropped:
        eeg = pop_select(eeg, rmchannel=[ds.labels[i] for i in proc.dropped], gui=False)
    retained = [i for i in range(ds.n_epochs) if i not in review.rejected]
    eeg.setdefault("etc", {})["eegqc"] = {
        "version": __version__,
        "source_epoch_indices": np.asarray(retained) + 1,
        "source_trial_ids": [ds.trial_ids[i] for i in retained],
        "rejected_source_epochs": np.asarray(sorted(review.rejected)) + 1,
        "excluded_source_ics": np.asarray(proc.excluded_ics, dtype=int) + 1,
        "interpolated_channels": [ds.labels[i] for i in proc.interpolated],
        "rating": review.rating,
        "note": review.note,
        "processing_json": json.dumps(asdict(proc)),
        "decomposition_id": ica.decomposition_id if compatible else "",
    }
    if ds.continuous:
        eeg["etc"]["eegqc"]["removed_sample_intervals"] = np.asarray(removed, dtype=int).reshape(-1, 2)
        eeg["etc"]["eegqc"]["retained_sample_segments"] = np.asarray(
            retained_segments(ds.data.shape[2], removed)
        )
    if "EEGdojo" in eeg.get("etc", {}) and "TrialID" in eeg["etc"]["EEGdojo"]:
        eeg["etc"]["EEGdojo"]["TrialID"] = np.asarray(ds.trial_ids)[retained].tolist()
    pop_saveset(eeg, str(path), savemode="onefile")
    return path
