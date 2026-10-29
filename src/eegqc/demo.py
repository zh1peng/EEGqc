"""Deterministic, explicitly synthetic data for interaction and numerical examples."""

import numpy as np

from eegqc.adapters.eeglab import from_eeg


def make_demo(n_channels=64, n_epochs=240, sfreq=250.0, duration=1.0, seed=17):
    import mne

    rng = np.random.default_rng(seed)
    montage = mne.channels.make_standard_montage("colin27_1005" if n_channels > 94 else "colin27_1020")
    preferred = [
        "Fp1",
        "Fp2",
        "F3",
        "F4",
        "F7",
        "F8",
        "Fz",
        "C3",
        "C4",
        "Cz",
        "P3",
        "P4",
        "Pz",
        "O1",
        "O2",
        "Oz",
        "T7",
        "T8",
    ]
    names = (preferred + [n for n in montage.ch_names if n not in preferred])[:n_channels]
    if len(names) != n_channels:
        raise ValueError("Demo montage does not have the requested number of channels")
    xyz = montage.get_positions()["ch_pos"]
    locs = []
    for name in names:
        x, y, z = xyz[name]
        radius = np.arccos(z / np.linalg.norm([x, y, z])) / np.pi
        locs.append(
            {
                "labels": name,
                "X": y,
                "Y": -x,
                "Z": z,
                "theta": float(np.rad2deg(np.arctan2(x, y))),
                "radius": float(radius),
                "type": "EEG",
            }
        )
    pnts = round(sfreq * duration)
    times = np.arange(pnts) / sfreq - 0.2
    sources = rng.normal(0, 2.0, (n_channels, pnts, n_epochs)).astype(np.float32)
    frequencies = rng.uniform(4.0, 25.0, n_channels)
    phases = rng.uniform(0, 2 * np.pi, (n_channels, 1, n_epochs))
    sources += (3 * np.sin(2 * np.pi * frequencies[:, None, None] * times[None, :, None] + phases)).astype(
        np.float32
    )
    conditions = np.where(np.arange(n_epochs) % 3 == 0, "target", "standard")
    blink_epochs = np.arange(4, n_epochs, 13)
    sources[0] = rng.normal(0, 0.5, (pnts, n_epochs))
    sources[0, :, blink_epochs] += (110 * np.exp(-(((times - 0.15) / 0.055) ** 2)))[None, :]
    sources[1] += (
        np.exp(-(((times - 0.32) / 0.07) ** 2))[:, None] * np.where(conditions == "target", 9.0, 2.0)[None, :]
    ).astype(np.float32)
    mixing = np.eye(n_channels) + rng.normal(0, 0.035, (n_channels, n_channels))
    mixing[:, 0] = np.array(
        [np.exp(-(((loc["radius"] - 0.45) / 0.2) ** 2)) * np.exp(-((loc["theta"] / 65) ** 2)) for loc in locs]
    )
    mixing[0, 0] += 0.3
    pz = np.array([locs[names.index("Pz")][key] for key in ("X", "Y", "Z")])
    mixing[:, 1] = [
        np.exp(-np.sum((np.array([loc[key] for key in ("X", "Y", "Z")]) - pz) ** 2) / 0.0064) for loc in locs
    ]
    mixing[1, 1] += 0.2
    values = np.einsum("ij,jte->ite", mixing, sources, optimize=True).astype(np.float32)
    events = [
        {
            "type": str(conditions[i]),
            "latency": float(i * pnts + round(0.2 * sfreq) + 1),
            "epoch": i + 1,
            "urevent": i,
            "duration": 0.0,
        }
        for i in range(n_epochs)
    ]
    eeg = {
        "data": values,
        "nbchan": n_channels,
        "pnts": pnts,
        "trials": n_epochs,
        "srate": sfreq,
        "xmin": -0.2,
        "xmax": float(times[-1]),
        "chanlocs": locs,
        "setname": "EEGqc · 合成演示数据",
        "icaweights": np.linalg.inv(mixing),
        "icasphere": np.eye(n_channels),
        "icawinv": mixing,
        "icachansind": np.arange(n_channels),
        "event": events,
        "urevent": [{k: v for k, v in e.items() if k not in ("epoch", "urevent")} for e in events],
        "epoch": [
            {
                "eventtype": str(conditions[i]),
                "eventlatency": 0.0,
                "event": i,
                "correct": "true" if i % 11 else "false",
            }
            for i in range(n_epochs)
        ],
        "etc": {"EEGdojo": {"TrialID": np.arange(1001, 1001 + n_epochs)}},
        "reject": {},
        "history": "EEGqc synthetic demonstration; not research data",
    }
    return from_eeg(eeg)
