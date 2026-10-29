# ASR numerical compatibility in EEGqc 0.2

EEGqc uses EEGPrep 0.4.0 calibration, spectral shaping, moving averages and ASR reconstruction. A local copy of its processing kernel changes the reconstruction pseudo-inverse from NumPy's default to `scipy.linalg.pinv`. This change is explicit in saved processing recipes as `eegprep-0.4.0-scipy-pinv-v1`.

## Why this change exists

ASR reconstructs signal from a matrix `Q` whose rejected subspace rows are exactly zero. Its mathematical rank is therefore smaller than the channel count. In the public 32-channel continuous EEGPrep example, NumPy's default relative cut-off of `1e-15` retained numerical null singular values in some windows. Reconstruction matrices reached norms around `7.5e16`, and output reached approximately `8.3e15 µV`. The calibration mixing matrix itself had condition number about 60; the problem arose during masked reconstruction.

SciPy's default relative cut-off is `max(Q.shape) * eps` (with zero absolute cut-off). This scales the numerical rank threshold with matrix size, following the same dimension-scaling principle used by MATLAB. This is not a claim of bitwise MATLAB equivalence. Calibration and other ASR parameters are unchanged. There is no amplitude clipping, silent algorithm substitution or retry with another method.

## Validation

`tools/validate_asr_reference.py` uses the first 60 seconds of EEGPrep's public `eeglab_data.set`: 32 channels at 128 Hz, high-pass 0.5 Hz, cutoff 20, calibration 0–20 seconds. Its independent reconstruction reference removes exactly zero rows of `Q`, inverts the remaining rectangular matrix, then restores the zero columns. This avoids the singular null rows by algebra rather than selecting another tolerance for the same square matrix.

The corrected output's maximum amplitude was **119.2455 µV**. Maximum absolute difference from that reference was **0.0000117 µV**. The script uses absolute tolerance `1e-10 V` and relative tolerance `1e-10`; it also checks a fixture-specific amplitude bound, so two exploding implementations cannot pass by relative agreement alone. The ordinary test suite checks masked-subspace inverses against the rectangular reference on a deterministic 32-channel case, plus calibration recipe restoration, discontinuity boundaries and export consistency.

These checks establish numerical agreement for the exercised cases. They do not establish optimal calibration intervals or artifact removal quality for every recording. Users choose a suitable calibration interval in one continuous segment. Epochs are not concatenated for ASR, and existing boundary events reset the processing state.

## Source and maintenance

The source is `eegprep/plugins/clean_rawdata/asr_process.py` from installed EEGPrep 0.4.0, retained as `src/eegqc/adapters/_asr_process.py`. Semantic changes are confined to import paths and the reconstruction pseudo-inverse solver. The EEGPrep BSD 2-Clause notice is in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and is included in the wheel. Future backend upgrades must rerun the numerical reference before removing this compatibility kernel.
