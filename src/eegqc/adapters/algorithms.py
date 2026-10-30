"""Scientific operations reuse EEGPrep and return data, never windows."""

from copy import deepcopy

import numpy as np

from eegqc.core.state import ASRConfig, DisplayFilter


def native_eeg(dataset, data=None):
    eeg = deepcopy(dataset.metadata)
    values = dataset.data if data is None else data
    eeg["data"] = np.moveaxis(values, 0, -1) * 1e6
    if dataset.continuous:
        eeg["data"] = eeg["data"][:, :, 0]
    eeg["chanlocs"] = deepcopy(dataset.chanlocs)
    eeg["icaact"] = np.array([])
    eeg.setdefault("etc", {})
    return eeg


def filter_signal(dataset, data, setting: DisplayFilter):
    from eegprep import pop_eegfiltnew

    if setting.low is None and setting.high is None:
        return data
    if setting.high is not None and setting.high >= dataset.sfreq / 2:
        raise ValueError("Low-pass frequency must be below Nyquist")
    if setting.low is not None and setting.high is not None and setting.low >= setting.high:
        raise ValueError("High-pass must be lower than low-pass frequency")
    result = pop_eegfiltnew(
        native_eeg(dataset, data), locutoff=setting.low, hicutoff=setting.high, plotfreqz=False
    )
    if isinstance(result, tuple):
        result = result[0]
    values = np.asarray(result["data"])
    return np.moveaxis(values.reshape(dataset.n_channels, -1, dataset.n_epochs, order="F"), -1, 0) * 1e-6


def asr_signal(dataset, parent_signal, config: ASRConfig):
    from eegqc.adapters.asr import clean_segment
    from eegprep.functions.miscfunc.event_utils import boundary_event_indices
    from eegqc.adapters.eeglab import _records

    if not dataset.continuous:
        raise ValueError(
            "ASR requires continuous data; epochs are not concatenated into artificial continuity"
        )
    if config.parent.asr is not None:
        raise ValueError("Re-run ASR from its input stage instead of applying it twice")
    n = dataset.data.shape[2]
    start, stop = (
        round(config.calibration_start * dataset.sfreq),
        round(config.calibration_stop * dataset.sfreq),
    )
    if not 0 <= start < stop <= n:
        raise ValueError("ASR calibration interval must be within the recording")
    filtered = filter_signal(dataset, parent_signal, DisplayFilter(config.highpass))
    channels = [
        i
        for i, loc in enumerate(dataset.chanlocs)
        if str(loc.get("type", "EEG")).upper() in ("", "EEG") and i not in config.parent.dropped
    ]
    if not channels:
        raise ValueError("ASR requires retained EEG channels")
    values = filtered[0, channels] * 1e6
    events = _records(dataset.metadata.get("event", []))
    raw_cuts = {int(np.floor(events[i]["latency"])) for i in boundary_event_indices(events)}
    cuts = sorted(cut for cut in raw_cuts if 0 < cut < n)
    if any(start < cut < stop for cut in cuts):
        raise ValueError(
            "Choose an ASR calibration interval within a continuous segment, away from boundary events"
        )
    result = filtered.copy()
    edges = [0, *cuts, n]
    for left, right in zip(edges[:-1], edges[1:]):
        result[0, channels, left:right] = (
            clean_segment(values[:, left:right], dataset.sfreq, values[:, start:stop], config.cutoff) * 1e-6
        )
    return result


def classify_ics(dataset):
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("ICLabel needs PyTorch. Run: uv sync --extra iclabel") from error
    from eegprep import iclabel

    if dataset.ica is None:
        raise ValueError("ICLabel requires an existing ICA decomposition")
    eeg = native_eeg(dataset)
    channels = dataset.ica.channels
    # ICLabel's feature extractor re-references this object. EEGPrep requires
    # its data/channel layout and ICA layout to agree for that conversion.
    eeg["data"] = eeg["data"][channels]
    eeg["chanlocs"] = [eeg["chanlocs"][i] for i in channels]
    eeg["nbchan"] = len(channels)
    eeg["icachansind"] = np.arange(len(channels))
    if len(channels) != dataset.n_channels and eeg.get("ref") in ("average", "averef"):
        # An average across the full recording is not the subset's average.
        # Let the backend apply its actual reference conversion to this copy.
        eeg["ref"] = "unknown"
    eeg["icaact"] = np.einsum("ic,ect->ite", dataset.ica.unmixing, dataset.data[:, channels], optimize=True)
    torch.set_num_threads(2)
    with torch.inference_mode():
        result = iclabel(eeg)
    report = result["etc"]["ic_classification"]["ICLabel"]
    return {
        "decomposition_id": dataset.ica.decomposition_id,
        "backend": "eegprep-0.4.0",
        "algorithm": "ICLabel-default",
        "classes": np.asarray(report["classes"]).tolist(),
        "probabilities": np.asarray(report["classifications"]).tolist(),
    }


def iclabel_candidates(report, categories, threshold):
    columns = [report["classes"].index(c) for c in categories]
    if not columns:
        return ()
    probabilities = np.asarray(report["probabilities"])
    return tuple(np.flatnonzero(np.max(probabilities[:, columns], axis=1) >= threshold).tolist())
