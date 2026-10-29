"""Calibration remains EEGPrep; reconstruction uses its pinned numerical compatibility patch."""

import math
import numpy as np
from eegprep.plugins.clean_rawdata.asr_calibrate import asr_calibrate
from eegprep.functions.miscfunc.misc import round_mat

from eegqc.adapters._asr_process import asr_process


def clean_segment(data, sfreq, reference, cutoff):
    state = asr_calibrate(reference.copy(), sfreq, cutoff=cutoff, maxmem=64)
    window = max(0.5, 1.5 * data.shape[0] / sfreq)
    extension = min(int(round_mat(window / 2 * sfreq)), data.shape[1] - 1)
    indices = np.arange(data.shape[1] - 2, data.shape[1] - extension - 2, -1)
    signal = np.concatenate((data, 2 * data[:, [-1]] - data[:, indices]), axis=1)
    cleaned, _ = asr_process(
        signal,
        sfreq,
        state,
        window_len=window,
        lookahead=window / 2,
        step_size=int(math.floor(sfreq * window / 2)),
        max_dims=0.66,
        max_mem=64,
    )
    return cleaned[:, : data.shape[1]]
