"""Validate the real EEGPrep tutorial recording, including its external .fdt file."""

import argparse
import json
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np
from eegprep import eeg_interp, pop_loadset, pop_subcomp

from eegqc.adapters.eeglab import component_thumbnail, export_set, load_set
from eegqc.application.persistence import restore_session, save_session
from eegqc.application.session import Session
from eegqc.core.state import Processing

REFERENCE = (
    "https://raw.githubusercontent.com/sccn/eegprep/f22111a576f1beacbb1ed44069ef16c871f77765/sample_data/"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--hdf5", action="store_true", help="Also compare the official MAT v7.3/HDF5 example")
    parser.add_argument("--directory", type=Path, default=Path("output/reference"))
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for name in ("eeglab_data_epochs_ica.set", "eeglab_data_epochs_ica.fdt"):
        path = args.directory / name
        if args.download and not path.exists():
            print("Downloading", name, flush=True)
            urlretrieve(REFERENCE + name, path)
    source = args.directory / "eeglab_data_epochs_ica.set"
    ds = load_set(source)
    session = Session(ds)
    proc = Processing(excluded_ics=(0,), interpolated=(0,))
    session.propose(proc)
    session.adopt()
    session.mark([0], True, "eye")
    actual = session.engine.signal(proc)
    expected_eeg = eeg_interp(pop_subcomp(pop_loadset(str(source)), [1], gui=False), [0], dtype="float64")
    expected = np.moveaxis(expected_eeg["data"], -1, 0) * 1e-6
    np.testing.assert_allclose(actual, expected, atol=1e-10, rtol=2e-6)
    erp = session.engine.erp(proc, session.review.rejected)
    np.testing.assert_allclose(erp.data, actual[1:].mean(axis=0), atol=1e-12)
    exported = load_set(export_set(session, args.directory / "qced_reference.set"))
    np.testing.assert_allclose(exported.data, actual[1:], atol=1e-10, rtol=2e-6)
    assert exported.trial_ids == ds.trial_ids[1:]
    save_session(session, args.directory / "reference.eegqc.json")
    restored = Session(load_set(source))
    restore_session(restored, args.directory / "reference.eegqc.json")
    assert restored.review == session.review
    image = component_thumbnail(ds, 0)
    assert image.shape == (200, 200, 4)
    result = {
        "source": REFERENCE,
        "shape": list(ds.data.shape),
        "sfreq": ds.sfreq,
        "components": ds.ica.count,
        "conditions": list(ds.conditions),
        "max_abs_difference_uv": float(np.max(np.abs(actual - expected)) * 1e6),
        "export_shape": list(exported.data.shape),
        "erp_count": erp.count,
        "trial_mapping_and_session_restore": "passed",
    }
    if args.hdf5:
        filename = "eeglab_data_epochs_ica_hdf5.set"
        target = args.directory / filename
        if args.download and not target.exists():
            print("Downloading", filename, flush=True)
            urlretrieve(REFERENCE + filename, target)
        hdf = load_set(target)
        assert hdf.data.shape == ds.data.shape
        assert hdf.ica.count == ds.ica.count
        np.testing.assert_allclose(hdf.data, ds.data, atol=1e-10, rtol=2e-6)
        result["hdf5_shape"] = list(hdf.data.shape)
        result["hdf5_max_abs_difference_uv"] = float(np.max(np.abs(hdf.data - ds.data)) * 1e6)
    (args.directory / "validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
