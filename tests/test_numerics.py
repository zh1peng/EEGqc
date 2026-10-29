import numpy as np
import pytest

from eegqc.adapters.eeglab import export_set, from_eeg, load_set
from eegqc.application.persistence import export_labels, restore_session, save_session
from eegqc.application.session import Session
from eegqc.core.preview import PreviewEngine
from eegqc.core.state import Processing
from eegqc.demo import make_demo


@pytest.fixture
def ds():
    return make_demo(n_channels=18, n_epochs=18, sfreq=100.0, duration=1.0)


def as_eeg(ds):
    eeg = dict(ds.metadata)
    eeg["data"] = np.moveaxis(ds.data, 0, -1) * 1e6
    return eeg


@pytest.mark.parametrize("reduced", [False, True])
def test_ica_preview_matches_eegprep_including_rank_reduction(ds, reduced):
    from eegprep import pop_subcomp

    eeg = as_eeg(ds)
    if reduced:
        eeg["icaweights"] = eeg["icaweights"][:9]
        eeg["icawinv"] = np.linalg.pinv(eeg["icaweights"] @ eeg["icasphere"])
        ds = from_eeg(eeg)
    expected = pop_subcomp(eeg, [1, 3], gui=False)["data"]
    actual = PreviewEngine(ds).signal(Processing(excluded_ics=(0, 2)))
    np.testing.assert_allclose(actual, np.moveaxis(expected, -1, 0) * 1e-6, atol=2e-11, rtol=2e-6)
    np.testing.assert_allclose(PreviewEngine(ds).signal(), ds.data, atol=1e-15)


def test_interpolation_and_combined_order_match_backend(ds):
    from eegprep import eeg_interp, pop_subcomp

    eeg = pop_subcomp(as_eeg(ds), [1], gui=False)
    expected = eeg_interp(eeg, [0, 3], dtype="float64")["data"]
    actual = PreviewEngine(ds).signal(Processing(excluded_ics=(0,), interpolated=(0, 3)))
    np.testing.assert_allclose(actual, np.moveaxis(expected, -1, 0) * 1e-6, atol=3e-11, rtol=3e-6)


def test_erp_incremental_reject_restore_and_baseline_equal_direct_average(ds):
    engine = PreviewEngine(ds)
    proc = Processing(excluded_ics=(0,), interpolated=(0,))
    condition = ("condition", "target")
    group = ds.conditions["condition"] == "target"
    for rejected in (frozenset(), frozenset({0, 3}), frozenset({3}), frozenset()):
        included = group & ~np.isin(np.arange(ds.n_epochs), list(rejected))
        expected = engine.signal(proc, np.flatnonzero(included)).mean(axis=0)
        base = (ds.times >= -0.2) & (ds.times <= 0)
        expected -= expected[:, base].mean(axis=1, keepdims=True)
        result = engine.erp(proc, rejected, condition=condition, baseline=(-0.2, 0))
        assert result.count == included.sum()
        np.testing.assert_allclose(result.data, expected, atol=3e-12)
    empty = engine.erp(proc, frozenset(np.flatnonzero(group)), condition=condition)
    assert empty.count == 0 and empty.data is None


def test_adopt_and_undo_preserve_judgment_context(ds, tmp_path):
    session = Session(ds)
    session.propose(Processing(excluded_ics=(0,)))
    session.set_rating("可用", "rating must not discard a candidate")
    assert session.candidate.excluded_ics == (0,)
    assert session.review.processing == Processing()
    session.adopt()
    session.mark([4], True, "eye", "frontal transient")
    assert dict(session.review.decisions)[4].processing.excluded_ics == (0,)
    session.undo()
    assert not session.review.rejected
    session.redo()
    session.freeze_reference()
    save_session(session, tmp_path / "review.json")
    restored = Session(ds)
    restore_session(restored, tmp_path / "review.json")
    assert restored.review == session.review
    assert restored.reference == session.reference
    export_labels(restored, tmp_path / "labels.csv")
    assert "1005" in (tmp_path / "labels.csv").read_text(encoding="utf-8-sig")


def test_export_roundtrip_matches_preview_and_preserves_trial_ids(ds, tmp_path):
    session = Session(ds)
    session.propose(Processing(excluded_ics=(0,), interpolated=(0,), dropped=(17,)))
    session.adopt()
    session.mark([1, 3], True, "eye")
    path = export_set(session, tmp_path / "clean.set")
    reread = load_set(path)
    kept = [i for i in range(ds.n_epochs) if i not in session.review.rejected]
    channels = list(range(17))
    expected = session.engine.signal(session.review.processing, kept, channels)
    np.testing.assert_allclose(reread.data, expected, atol=2e-11, rtol=2e-6)
    assert reread.trial_ids == tuple(ds.trial_ids[i] for i in kept)
    assert reread.labels == ds.labels[:-1]
    assert reread.ica is None


def test_conditions_and_input_not_changed_by_previews(ds):
    original = ds.data.copy()
    engine = PreviewEngine(ds)
    engine.erp(Processing(average_reference=True), condition=("correct", "false"))
    engine.signal(Processing(excluded_ics=(0,)))
    np.testing.assert_array_equal(ds.data, original)
    assert not ds.data.flags.writeable


def test_behavioral_exclusion_is_not_a_signal_artifact_label(ds):
    session = Session(ds)
    session.mark([0], True, "behavioral")
    session.mark([1], False, "signal_artifact")
    records = dict(session.review.decisions)
    assert records[0].quality == "uncertain"
    assert records[1].quality == "acceptable"


def test_partial_channel_ica_preserves_nonparticipating_channels(ds):
    from eegprep import pop_subcomp

    eeg = as_eeg(ds)
    selected = np.array([5, 2, 10, 0, 7, 11])
    rng = np.random.default_rng(3)
    mixing = np.eye(6) + rng.normal(0, 0.03, (6, 6))
    eeg.update(icachansind=selected, icaweights=np.linalg.inv(mixing), icasphere=np.eye(6), icawinv=mixing)
    subset = from_eeg(eeg)
    result = PreviewEngine(subset).signal(Processing(excluded_ics=(1,)))
    expected = pop_subcomp(eeg, [2], gui=False)["data"]
    np.testing.assert_allclose(result, np.moveaxis(expected, -1, 0) * 1e-6, atol=2e-11, rtol=2e-6)
    other = np.setdiff1d(np.arange(ds.n_channels), selected)
    np.testing.assert_array_equal(result[:, other], subset.data[:, other])


def test_empty_matlab_channel_coordinates_are_normalized_at_import(ds):
    from copy import deepcopy
    from eegprep import eeg_interp

    eeg = as_eeg(ds)
    eeg["chanlocs"] = deepcopy(eeg["chanlocs"])
    for coordinate in ("X", "Y", "Z"):
        eeg["chanlocs"][-1][coordinate] = np.array([])
    imported = from_eeg(eeg)
    assert np.isnan(imported.chanlocs[-1]["X"])
    actual = PreviewEngine(imported).signal(Processing(interpolated=(0,)))
    expected = eeg_interp(eeg, [0], dtype="float64")["data"]
    np.testing.assert_allclose(actual, np.moveaxis(expected, -1, 0) * 1e-6, atol=2e-11, rtol=2e-6)


def test_export_keeps_source_indices_when_external_trial_ids_are_absent(ds, tmp_path):
    eeg = as_eeg(ds)
    eeg["etc"] = {}
    imported = from_eeg(eeg)
    session = Session(imported)
    session.mark([0, 2], True, "eye")
    exported = load_set(export_set(session, tmp_path / "no_external_ids.set"))
    assert exported.trial_ids == tuple(
        imported.trial_ids[i] for i in range(imported.n_epochs) if i not in (0, 2)
    )
