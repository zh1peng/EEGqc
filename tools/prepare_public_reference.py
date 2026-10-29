"""Prepare one public 64-channel recording for compatibility/performance checks.

Run with the project's Python environment. Network access is opt-in:
    python tools/prepare_public_reference.py --download
The fixed one-second windows are validation segments, not experimental trials
or a scientifically specified ERP analysis. No filtering/re-referencing/ICA is
applied while converting the reference input.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import urllib.request

import mne
import numpy as np
from eegprep import pop_saveset
from threadpoolctl import threadpool_limits

from eegqc.adapters.eeglab import _records, export_set, load_set
from eegqc.application.session import Session
from eegqc.core.state import Processing


SOURCE_PAGE = "https://physionet.org/content/eegmmidb/1.0.0/"
SOURCE_FILE = "https://physionet.org/files/eegmmidb/1.0.0/S001/S001R03.edf"
LICENSE_URL = "https://physionet.org/content/eegmmidb/view-license/1.0.0/"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def locations(raw):
    positions = raw.get_montage().get_positions()
    result = []
    for name in raw.ch_names:
        x, y, z = positions["ch_pos"][name]
        result.append(
            {
                "labels": name,
                "type": "EEG",
                "X": float(y),
                "Y": float(-x),
                "Z": float(z),
                "theta": float(np.degrees(np.arctan2(x, y))),
                "radius": float(np.arccos(z / np.linalg.norm((x, y, z))) / np.pi),
            }
        )
    return result


def base_eeg(raw, chanlocs, annotations):
    values = raw.get_data()
    pnts = values.shape[1]
    events = [
        {
            "type": a["description"],
            "latency": float(a["sample_start"] + 1),
            "duration": float(a["duration_seconds"] * raw.info["sfreq"]),
            "urevent": i,
        }
        for i, a in enumerate(annotations)
    ]
    return {
        "data": values * 1e6,
        "nbchan": len(raw.ch_names),
        "trials": 1,
        "pnts": pnts,
        "srate": float(raw.info["sfreq"]),
        "xmin": 0.0,
        "xmax": float((pnts - 1) / raw.info["sfreq"]),
        "times": np.arange(pnts) / raw.info["sfreq"] * 1000,
        "chanlocs": chanlocs,
        "event": events,
        "urevent": [{k: v for k, v in e.items() if k != "urevent"} for e in events],
        "epoch": [],
        "reject": {},
        "setname": "PhysioNet S001R03 continuous reference",
        "subject": "PhysioNet S001",
        "comments": "Public real EEG; conversion/validation reference.",
        "history": "MNE EDF import; eegbci.standardize; standard_1005 template montage; no signal processing",
        "etc": {
            "public_reference": {
                "source": SOURCE_FILE,
                "license": "Open Data Commons Attribution License v1.0",
                "license_url": LICENSE_URL,
                "annotations_json": json.dumps(annotations),
                "positions": "MNE standard_1005 template, transformed to head coordinates; not measured positions",
            }
        },
    }


def fixed_windows(eeg, annotations):
    eeg = deepcopy(eeg)
    samples = round(eeg["srate"])
    count = eeg["pnts"] // samples
    eeg["data"] = eeg["data"][:, : count * samples].reshape(eeg["nbchan"], count, samples).transpose(0, 2, 1)
    eeg.update(pnts=samples, trials=count, xmax=(samples - 1) / eeg["srate"])
    eeg["times"] = np.arange(samples) / eeg["srate"] * 1000
    eeg["setname"] = "PhysioNet S001R03 fixed 1s validation windows (not experimental trials)"
    eeg["history"] += "; fixed non-overlapping 1s validation windows; no trial/ERP interpretation"
    events, epochs = [], []
    for i in range(count):
        left, right = i * samples, (i + 1) * samples
        event_index = len(events)
        events.append(
            {
                "type": "fixed-validation-window",
                "latency": float(left + 1),
                "duration": 0.0,
                "epoch": i + 1,
                "urevent": event_index,
                "source_duration_seconds": 0.0,
            }
        )
        onsets = [a for a in annotations if left <= a["sample_start"] < right]
        for annotation in onsets:
            event = {
                "type": annotation["description"],
                "latency": float(annotation["sample_start"] + 1),
                "duration": min(
                    annotation["duration_seconds"] * eeg["srate"], right - annotation["sample_start"]
                ),
                "epoch": i + 1,
                "urevent": len(events),
                "source_duration_seconds": annotation["duration_seconds"],
            }
            events.append(event)
        member_events = events[event_index:]
        epochs.append(
            {
                "event": list(range(event_index, len(events))),
                "eventtype": [event["type"] for event in member_events],
                "eventlatency": [
                    (event["latency"] - 1 - left) / eeg["srate"] * 1000 for event in member_events
                ],
                "eventduration": [event["duration"] / eeg["srate"] * 1000 for event in member_events],
                "segment_kind": "fixed_1s_validation_only",
                "source_start_sample": left,
            }
        )
    eeg["event"], eeg["epoch"] = events, epochs
    eeg["urevent"] = [{k: v for k, v in event.items() if k not in ("epoch", "urevent")} for event in events]
    eeg["etc"]["EEGdojo"] = {"TrialID": [f"fixed-window-{i + 1:04d}" for i in range(count)]}
    return eeg


def validate(path, expected, annotations, output_dir):
    dataset = load_set(path)
    np.testing.assert_allclose(dataset.data, expected, rtol=0, atol=1e-14)
    assert dataset.n_channels == 64 and dataset.sfreq == 160
    assert dataset.ica is None
    assert all(
        np.isfinite([loc[k] for k in ("X", "Y", "Z", "theta", "radius")]).all() for loc in dataset.chanlocs
    )
    imported_events = _records(dataset.metadata["event"])
    actual_annotations = [event for event in imported_events if event["type"] in ("T0", "T1", "T2")]
    assert [event["type"] for event in actual_annotations] == [a["description"] for a in annotations]
    np.testing.assert_array_equal(
        [event["latency"] for event in actual_annotations], [a["sample_start"] + 1 for a in annotations]
    )
    session = Session(dataset)
    channel = dataset.labels.index("Fp1")
    session.propose(Processing(interpolated=(channel,)))
    session.adopt()
    signal = session.engine.signal(session.review.processing)
    if dataset.continuous:
        session.mark_interval(160, 320, note="Compatibility validation only; not an actual quality finding")
        expected_export = np.concatenate((signal[:, :, :160], signal[:, :, 320:]), axis=2)
        retained = None
    else:
        session.mark([1, 7], True, note="Compatibility validation only; not actual quality findings")
        session.mark([2], False, note="Compatibility validation retained-label exercise")
        retained = [i for i in range(dataset.n_epochs) if i not in (1, 7)]
        expected_export = signal[retained]
        assert set(dataset.conditions["condition"]) == {"fixed-validation-window"}
    saved = output_dir / (path.stem + "_validation_qced.set")
    export_set(session, saved)
    restored = load_set(saved)
    np.testing.assert_allclose(restored.data, expected_export, rtol=0, atol=1e-14)
    assert restored.labels == dataset.labels
    if retained is not None:
        assert restored.trial_ids == tuple(dataset.trial_ids[i] for i in retained)
        assert set(restored.conditions["segment_kind"]) == {"fixed_1s_validation_only"}
        np.testing.assert_array_equal(
            np.asarray(restored.metadata["etc"]["eegqc"]["source_epoch_indices"]).ravel(),
            np.asarray(retained) + 1,
        )
        surviving_annotations = [a for a in annotations if a["sample_start"] // 160 in retained]
        restored_annotations = [
            event for event in _records(restored.metadata["event"]) if event["type"] in ("T0", "T1", "T2")
        ]
        assert [event["type"] for event in restored_annotations] == [
            a["description"] for a in surviving_annotations
        ]
        np.testing.assert_array_equal(
            [event["latency"] for event in restored_annotations],
            [
                retained.index(a["sample_start"] // 160) * 160 + a["sample_start"] % 160 + 1
                for a in surviving_annotations
            ],
        )
        np.testing.assert_array_equal(
            [event["source_duration_seconds"] for event in restored_annotations],
            [a["duration_seconds"] for a in surviving_annotations],
        )
    else:
        restored_events = _records(restored.metadata["event"])
        expected_events = [a for a in annotations if not 160 <= a["sample_start"] < 320]
        actual_events = [event for event in restored_events if event["type"] in ("T0", "T1", "T2")]
        assert [e["type"] for e in actual_events] == [e["description"] for e in expected_events]
        np.testing.assert_array_equal(
            [e["latency"] for e in actual_events],
            [a["sample_start"] + 1 - (160 if a["sample_start"] >= 320 else 0) for a in expected_events],
        )
        np.testing.assert_array_equal(
            [event["duration"] for event in actual_events],
            [a["duration_seconds"] * dataset.sfreq for a in expected_events],
        )
        np.testing.assert_array_equal(
            np.asarray(restored.metadata["etc"]["eegqc"]["retained_sample_segments"]),
            [[0, 160, 0], [320, 20000, 160]],
        )
    maximum_error = float(np.max(np.abs(restored.data - expected_export)))
    return {
        "input": str(path.resolve()),
        "qced_output": str(saved.resolve()),
        "shape": list(dataset.data.shape),
        "export_shape": list(restored.data.shape),
        "sfreq": dataset.sfreq,
        "max_abs_export_error_V": maximum_error,
        "interpolated_channel": "Fp1",
        "marking": "remove samples [160,320)"
        if dataset.continuous
        else "reject validation windows 2 and 8; keep window 3",
        "source_events_verified": len(actual_annotations),
        "passed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download only S001R03.edf if absent")
    parser.add_argument("--output-dir", type=Path, default=Path("output/public-reference"))
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    edf = output_dir / "S001R03.edf"
    if not edf.exists():
        if not args.download:
            parser.error(f"{edf} is absent; use --download to retrieve the one public recording")
        partial = edf.with_suffix(".edf.partial")
        try:
            urllib.request.urlretrieve(SOURCE_FILE, partial)
            partial.replace(edf)
        finally:
            partial.unlink(missing_ok=True)
    raw = mne.io.read_raw_edf(edf, preload=True, verbose="ERROR")
    original_labels = list(raw.ch_names)
    mne.datasets.eegbci.standardize(raw)
    raw.set_montage("standard_1005", on_missing="raise")
    annotation_samples = raw.time_as_index(raw.annotations.onset, use_rounding=True)
    annotations = [
        {
            "description": str(a["description"]),
            "onset_seconds": float(a["onset"]),
            "duration_seconds": float(a["duration"]),
            "sample_start": int(sample),
        }
        for a, sample in zip(raw.annotations, annotation_samples)
    ]
    chanlocs = locations(raw)
    continuous = base_eeg(raw, chanlocs, annotations)
    windows = fixed_windows(continuous, annotations)
    paths = [output_dir / "S001R03_continuous_64.set", output_dir / "S001R03_fixed1s_64.set"]
    for eeg, path in zip((continuous, windows), paths):
        pop_saveset(eeg, str(path), savemode="onefile")
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_page": SOURCE_PAGE,
        "source_file": SOURCE_FILE,
        "source_edf_bytes": edf.stat().st_size,
        "citation": "Schalk, G. (2009). EEG Motor Movement/Imagery Dataset (version 1.0.0). PhysioNet. doi:10.13026/C28G6P",
        "original_publication": "Schalk et al. BCI2000. IEEE Trans Biomed Eng 51(6):1034-1043 (2004). doi:10.1109/TBME.2004.827072",
        "license": "Open Data Commons Attribution License v1.0 (ODC-By)",
        "license_url": LICENSE_URL,
        "versions": {"python": platform.python_version(), "mne": mne.__version__, "numpy": np.__version__},
        "conversion": [
            "MNE read_raw_edf (volts)",
            "mne.datasets.eegbci.standardize channel labels",
            "MNE standard_1005 template montage in head coordinates; EEGLAB X=y,Y=-x,Z=z",
            "multiply volts by 1e6 for EEGPrep; save one-file EEGLAB .set",
            "additional fixed 1s windows for compatibility/performance only; no filtering, referencing, ICA or baseline",
        ],
        "limitation": "Template positions are not individual digitization; fixed windows are not experimental trials or validated ERP conditions. Deliberate QC edits below exercise software, not scientific quality judgments.",
        "original_channel_labels": original_labels,
        "standardized_channel_labels": raw.ch_names,
        "annotations": annotations,
        "validation": [],
    }
    write_json(
        output_dir / "layout.json",
        {
            "montage": "standard_1005",
            "coordinate_units": "metres",
            "eeglab_axis_conversion": "X=head_y,Y=-head_x,Z=head_z",
            "chanlocs": chanlocs,
        },
    )
    with threadpool_limits(limits=2, user_api="blas"):
        report["validation"].append(validate(paths[0], raw.get_data()[None], annotations, output_dir))
        expected_windows = (
            raw.get_data()[:, : windows["trials"] * windows["pnts"]]
            .reshape(64, windows["trials"], windows["pnts"])
            .transpose(1, 0, 2)
        )
        report["validation"].append(validate(paths[1], expected_windows, annotations, output_dir))
    write_json(output_dir / "provenance-validation.json", report)
    print(
        json.dumps(
            {"manifest": str(output_dir / "provenance-validation.json"), "validation": report["validation"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
