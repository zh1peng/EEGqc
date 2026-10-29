from dataclasses import replace
import json

import numpy as np
import pytest

from eegqc.adapters.algorithms import filter_signal, iclabel_candidates
from eegqc.adapters.eeglab import export_set, from_eeg, load_set
from eegqc.application.persistence import restore_session, save_session
from eegqc.application.session import Session
from eegqc.core.conditions import Clause, Condition
from eegqc.core.preview import PreviewEngine
from eegqc.core.state import ASRConfig, DisplayFilter, Processing
from eegqc.demo import make_demo


@pytest.fixture
def ds():
    return make_demo(18, 30, 100.0, 1.0)


def continuous():
    rng = np.random.default_rng(2)
    sfreq, n = 128.0, 2048
    t = np.arange(n) / sfreq
    data = rng.normal(0, 4.0, (6, n)) + 3 * np.sin(2 * np.pi * 10 * t)
    data[0, 1400:1450] += 150 * np.sin(np.linspace(0, np.pi, 50))
    locs = make_demo(18, 3, 100.0, 1.0).chanlocs[:6]
    return from_eeg(
        {
            "data": data,
            "nbchan": 6,
            "pnts": n,
            "trials": 1,
            "srate": sfreq,
            "xmin": 0.0,
            "xmax": (n - 1) / sfreq,
            "chanlocs": locs,
            "epoch": [],
            "event": [{"type": "stim", "latency": 1501.0, "duration": 0.0}],
            "etc": {},
            "setname": "continuous numerical fixture",
        }
    )


def test_combined_conditions_and_incremental_erp_equal_direct_selection(ds):
    engine = PreviewEngine(ds)
    query = Condition((Clause("condition", "=", "target"), Clause("correct", "=", "true")))
    mask = (ds.conditions["condition"] == "target") & (ds.conditions["correct"] == "true")
    np.testing.assert_array_equal(query.mask(ds), mask)
    for rejected in (frozenset({3}), frozenset(), frozenset({0, 6})):
        keep = mask & ~np.isin(np.arange(ds.n_epochs), list(rejected))
        actual = engine.erp(rejected=rejected, condition=query)
        np.testing.assert_allclose(actual.data, ds.data[keep].mean(axis=0, dtype=np.float64), atol=1e-14)
        assert actual.count == keep.sum()
    any_query = replace(query, mode="any")
    assert any_query.mask(ds).sum() >= mask.sum()
    ds.conditions["rt"] = np.asarray([str(i * 10) for i in range(ds.n_epochs)])
    assert Condition((Clause("rt", ">=", "100"),)).mask(ds).sum() == 20


def test_full_record_display_filter_has_consistent_windows_and_does_not_change_export(ds, tmp_path):
    engine = PreviewEngine(ds)
    proc, setting = Processing(excluded_ics=(0,)), DisplayFilter(2.0, 30.0)
    full = engine.signal(proc, display_filter=setting)
    np.testing.assert_allclose(full, filter_signal(ds, engine.signal(proc), setting), atol=5e-11)
    window = engine.signal(proc, [2, 4], [0, 2], slice(10, 40), display_filter=setting)
    np.testing.assert_allclose(window, full[[2, 4]][:, [0, 2], 10:40])
    erp = engine.erp(proc, condition=("condition", "target"), display_filter=setting)
    mask = ds.conditions["condition"] == "target"
    np.testing.assert_allclose(erp.data, full[mask].mean(axis=0), atol=1e-12)
    session = Session(ds)
    session.propose(proc)
    session.adopt()
    exported = load_set(export_set(session, tmp_path / "unfiltered.set"))
    np.testing.assert_allclose(exported.data, engine.signal(proc), atol=1e-10)


def test_asr_candidate_matches_backend_and_restores_from_recipe(tmp_path):
    from eegprep.plugins.clean_rawdata.clean_asr import clean_asr

    ds = continuous()
    session = Session(ds)
    config = ASRConfig(Processing(), cutoff=8.0, calibration_start=0.0, calibration_stop=8.0, highpass=None)
    proc = Processing(asr=config)
    actual = session.engine.signal(proc)
    native = ds.data[0] * 1e6
    cleaned = clean_asr(
        {"data": native, "nbchan": ds.n_channels, "srate": ds.sfreq},
        cutoff=8.0,
        ref_maxbadchannels=native[:, :1024],
    )
    expected = cleaned["data"][None] * 1e-6
    np.testing.assert_allclose(actual, expected, atol=1e-14)
    np.testing.assert_array_equal(session.engine.signal(), ds.data)
    session.propose(proc)
    session.adopt()
    session.mark_interval(100, 200)
    session.mark_interval(150, 250)
    path = save_session(session, tmp_path / "asr.json")
    restored = Session(ds)
    restore_session(restored, path)
    assert restored.review == session.review
    np.testing.assert_allclose(restored.engine.signal(restored.review.processing), actual, atol=1e-14)
    exported = load_set(export_set(session, tmp_path / "asr.set"))
    np.testing.assert_allclose(exported.data, np.delete(actual, np.arange(100, 250), axis=2), atol=1e-10)
    assert exported.metadata["pnts"] == ds.data.shape[2] - 150
    event = next(e for e in exported.metadata["event"] if e["type"] == "stim")
    assert event["latency"] == 1351.0


def test_iclabel_threshold_uses_selected_categories_only():
    report = {
        "classes": ["Brain", "Eye", "Muscle"],
        "probabilities": [[0.1, 0.85, 0.05], [0.1, 0.1, 0.8], [0.9, 0.05, 0.05]],
    }
    assert iclabel_candidates(report, ["Eye"], 0.8) == (0,)
    assert iclabel_candidates(report, ["Eye", "Muscle"], 0.8) == (0, 1)
    assert iclabel_candidates(report, [], 0.8) == ()


def test_condition_presets_and_view_context_survive_save_restore(ds, tmp_path):
    session = Session(ds)
    query = Condition((Clause("correct", "=", "true"),))
    session.erp_conditions["correct"] = query
    session.view_context = {"display_filter": {"low": 1.0, "high": 40.0}, "visible_channels": [0, 1]}
    session.mark([0], False)
    path = save_session(session, tmp_path / "conditions.json")
    restored = Session(ds)
    restore_session(restored, path)
    assert restored.erp_conditions == session.erp_conditions
    assert dict(restored.review.decisions)[0].display_context == session.view_context
    assert json.loads(path.read_text(encoding="utf-8"))["erp_conditions"]["correct"]["mode"] == "all"


def test_asr_resets_at_source_boundaries_and_keeps_one_calibration_reference():
    from eegprep.plugins.clean_rawdata.clean_asr import clean_asr

    ds = continuous()
    ds.metadata["event"].append({"type": "boundary", "latency": 1024.5, "duration": 10.0})
    ds.metadata["event"].extend(
        [{"type": "boundary", "latency": 0.5}, {"type": "boundary", "latency": 2048.5}]
    )
    config = ASRConfig(Processing(), cutoff=8.0, calibration_stop=8.0, highpass=None)
    actual = PreviewEngine(ds).signal(Processing(asr=config))
    native = ds.data[0] * 1e6
    expected = []
    for left, right in ((0, 1024), (1024, 2048)):
        result = clean_asr(
            {"data": native[:, left:right], "nbchan": 6, "srate": ds.sfreq},
            cutoff=8.0,
            ref_maxbadchannels=native[:, :1024],
        )
        expected.append(result["data"])
    np.testing.assert_allclose(actual[0], np.concatenate(expected, axis=1) * 1e-6, atol=1e-14)


def test_empty_matlab_channel_types_use_the_eeglab_unspecified_type():
    ds = make_demo(18, 3, 100.0, 1.0)
    eeg = dict(ds.metadata)
    eeg["data"] = np.moveaxis(ds.data, 0, -1) * 1e6
    eeg["chanlocs"] = [dict(loc, type=np.array([])) for loc in ds.chanlocs]
    imported = from_eeg(eeg)
    assert all(loc["type"] == "" for loc in imported.chanlocs)
    average = PreviewEngine(imported).signal(Processing(average_reference=True))
    np.testing.assert_allclose(average.mean(axis=1), 0, atol=1e-14)


def test_asr_masked_subspaces_match_reduced_row_inverse(monkeypatch):
    from scipy.linalg import pinv
    from eegprep.functions.miscfunc.misc import finite_pinv
    from eegqc.adapters import _asr_process

    rng = np.random.default_rng(24)
    rotation, _ = np.linalg.qr(rng.normal(size=(32, 32)))
    mixing = (rotation * np.geomspace(1, 60, 32)) @ rotation.T
    state = {
        "M": mixing,
        "T": np.eye(32) * 0.1,
        "sos": np.array([[1.0, 0, 0, 1, 0, 0]]),
        "iir_state": np.zeros((1, 32, 2)),
    }
    reconstructions = []

    def check_inverse(matrix, solver=None):
        result = finite_pinv(matrix, solver=solver)
        active = np.any(matrix != 0, axis=1)
        expected = np.zeros(matrix.T.shape)
        expected[:, active] = pinv(matrix[active])
        np.testing.assert_allclose(result, expected, atol=1e-12, rtol=1e-12)
        reconstructions.append(result)
        return result

    monkeypatch.setattr(_asr_process, "finite_pinv", check_inverse)
    signal = rng.normal(size=(32, 1024))
    cleaned, _ = _asr_process.asr_process(signal, 128.0, state, max_mem=64)
    assert reconstructions  # The fixture actually exercises rejected subspaces.
    assert np.max(np.abs(cleaned)) < 10 * np.max(np.abs(signal))
