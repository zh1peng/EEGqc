"""Compare the default ASR workflow with direct EEGPrep on a public continuous recording."""

import argparse
import json
from unittest.mock import patch
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np
from scipy.linalg import pinv
from eegprep import pop_eegfiltnew, pop_loadset, pop_select
from eegprep.plugins.clean_rawdata.clean_asr import clean_asr
from threadpoolctl import threadpool_limits

from eegqc.adapters.algorithms import native_eeg
from eegqc.adapters.eeglab import from_eeg
from eegqc.core.preview import PreviewEngine
from eegqc.core.state import ASRConfig, Processing

REFERENCE = (
    "https://raw.githubusercontent.com/sccn/eegprep/f22111a576f1beacbb1ed44069ef16c871f77765/sample_data/"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    root = Path("output/reference")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("eeglab_data.set", "eeglab_data.fdt"):
        if args.download and not (root / name).exists():
            print("Downloading", name, flush=True)
            urlretrieve(REFERENCE + name, root / name)
    eeg = pop_select(pop_loadset(str(root / "eeglab_data.set")), time=[0.0, 60.0], gui=False)
    ds = from_eeg(eeg)
    config = ASRConfig(Processing(), calibration_stop=20.0)
    with threadpool_limits(limits=2, user_api="blas"):
        actual = PreviewEngine(ds).signal(Processing(asr=config))
        filtered = pop_eegfiltnew(native_eeg(ds), locutoff=0.5, plotfreqz=False)["data"]
        indices = [
            i for i, loc in enumerate(ds.chanlocs) if str(loc.get("type", "EEG")).upper() in ("", "EEG")
        ]
        selected = filtered[indices]

        # Independent algebraic reference: remove exactly zero rows before inversion,
        # then put the corresponding zero columns back. No ill-conditioned null rows.
        def reduced_row_inverse(matrix):
            active = np.any(matrix != 0, axis=1)
            inverse = np.zeros(matrix.T.shape)
            inverse[:, active] = pinv(matrix[active])
            return inverse

        with patch("eegprep.plugins.clean_rawdata.asr_process.finite_pinv", reduced_row_inverse):
            direct = clean_asr(
                {"data": selected, "nbchan": len(indices), "srate": ds.sfreq},
                cutoff=20.0,
                ref_maxbadchannels=selected[:, : round(20 * ds.sfreq)],
            )
        expected = filtered.copy()
        expected[indices] = direct["data"]
    np.testing.assert_allclose(actual[0], expected * 1e-6, atol=1e-10, rtol=1e-10)
    assert float(np.max(np.abs(actual))) < float(np.max(np.abs(filtered))) * 1e-6 * 10
    report = {
        "shape": list(actual.shape),
        "sfreq": ds.sfreq,
        "calibration_seconds": [0, 20],
        "cutoff": 20.0,
        "highpass_hz": 0.5,
        "max_abs_difference_uv": float(np.max(np.abs(actual[0] - expected * 1e-6)) * 1e6),
        "max_output_uv": float(np.max(np.abs(actual)) * 1e6),
        "implementation": config.implementation,
    }
    (root / "asr-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
