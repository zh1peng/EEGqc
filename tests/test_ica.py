from dataclasses import replace
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile
from contextlib import nullcontext
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from eegqc.adapters.algorithms import classify_ics, native_eeg
from eegqc.adapters.eeglab import export_set, from_eeg, load_set
from eegqc.adapters.ica import fit_ica, prepare_ica
from eegqc.application.persistence import restore_session, save_session
from eegqc.application.session import Session
from eegqc.core.state import ASRConfig, Processing
from eegqc.epochlabel.bundle import export_bundle


def dataset(continuous=False):
    rng = np.random.default_rng(7)
    epochs, samples = (1, 1600) if continuous else (8, 200)
    sources = np.stack([rng.laplace(size=(epochs, samples)), rng.uniform(-2, 2, (epochs, samples))])
    mixing = np.array([[2, 0.2], [0.5, 1.5], [-0.7, 0.8]])
    data = np.einsum("cs,set->ect", mixing, sources) * 12
    data += np.array([7, -11, 3])[None, :, None]
    data = np.concatenate([data, rng.normal(size=(epochs, 1, samples)) * 4], axis=1)
    return from_eeg(
        {
            "data": data.transpose(1, 2, 0),
            "nbchan": 4,
            "pnts": samples,
            "trials": epochs,
            "srate": 100.0,
            "xmin": 0.0,
            "xmax": (samples - 1) / 100,
            "setname": "rank two synthetic ICA",
            "chanlocs": [{"labels": name, "type": "EEG"} for name in ("A", "B", "C", "D")],
            "epoch": [] if continuous else [{} for _ in range(epochs)],
            "event": [],
            "icaweights": np.eye(4),
            "icasphere": np.eye(4),
            "icawinv": np.eye(4),
            "icachansind": np.arange(4),
            "etc": {},
        }
    )


@pytest.fixture
def fitted():
    session = Session(dataset())
    # The fourth source IC is removed before fitting: a scientific input stage.
    session.propose(Processing(excluded_ics=(3,)))
    session.adopt()
    session.mark([1, 6], True)
    prepared = prepare_ica(session, channels=(2, 0, 1), max_iter=64, seed=9)
    return session, prepared, fit_ica(prepared)


def test_extended_infomax_rank_selection_and_eegprep_reconstruction(fitted):
    from eegprep import pop_subcomp

    session, prepared, ica = fitted
    assert prepared.rank == 2 and prepared.sample_count == 1200
    assert prepared.epoch_indices == (0, 2, 3, 4, 5, 7)
    assert ica.count == 2 and tuple(ica.channels) == (2, 0, 1)
    parent = session.engine.signal(session.review.processing)
    session.propose_ica(ica)
    # A reduced-rank fit must not itself reconstruct/project away input offsets.
    np.testing.assert_array_equal(session.engine.signal(session.candidate), parent)
    component_ds = session.component_dataset()
    expected = pop_subcomp(native_eeg(component_ds), [1], gui=False)["data"]
    candidate = replace(session.candidate, excluded_ics=(0,))
    actual = session.engine.signal(candidate)
    np.testing.assert_allclose(actual, np.moveaxis(expected, -1, 0) * 1e-6, atol=2e-12)
    np.testing.assert_array_equal(actual[:, 3], parent[:, 3])
    # The fitted subspace recovers the centered selected signals.
    selected = prepared.data - prepared.data.mean(axis=1, keepdims=True)
    np.testing.assert_allclose(ica.mixing @ ica.unmixing @ selected, selected, atol=2e-12)


def test_refit_save_restore_export_and_decision_basis(fitted, tmp_path, monkeypatch):
    session, _, ica = fitted
    session.freeze_reference()
    old_review = session.review
    session.propose_ica(ica)
    session.adopt()
    assert session.review.rejected == frozenset()
    assert session.pending_review_count == 2
    session.mark([2], False)
    session.propose(replace(session.review.processing, excluded_ics=(0,)))
    session.adopt()
    session.mark([3], True)
    expected = session.engine.signal(session.review.processing)
    path = save_session(session, tmp_path / "审查 ICA.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert [d["id"] for d in record["decompositions"]] == [ica.decomposition_id]
    with np.load(tmp_path / record["array_file"], allow_pickle=False) as arrays:
        assert all(arr.dtype != object for arr in arrays.values())
        assert all(arr.ndim <= 2 for arr in arrays.values())  # no duplicate EEG recording
    monkeypatch.setattr("eegqc.adapters.ica.fit_ica", lambda _: pytest.fail("Restore must load matrices"))
    restored = Session(dataset())
    restore_session(restored, path)
    np.testing.assert_allclose(restored.engine.signal(restored.review.processing), expected, atol=1e-15)
    assert restored.review == session.review
    restored.restore_reference()
    assert restored.review == old_review
    restored.undo()
    assert restored.review == session.review
    reread = load_set(export_set(restored, tmp_path / "new ICA.set"))
    kept = [i for i in range(8) if i != 3]
    np.testing.assert_allclose(reread.data, expected[kept], atol=2e-11)
    assert reread.ica.count == 1 and tuple(reread.ica.channels) == (2, 0, 1)
    groups = dict(lab="lab", study="study", participant="person", recording="record")
    bundle = export_bundle(restored, tmp_path / "labels.zip", rater_id="r", groups=groups, consent=True)
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        old = next(s for s in manifest["samples"] if s["epoch_index"] == 1)
        new = next(s for s in manifest["samples"] if s["epoch_index"] == 3)
        assert old["decomposition_id"] == restored.dataset.ica.decomposition_id
        assert new["decomposition_id"] == ica.decomposition_id
        actual = np.load(BytesIO(archive.read(old["waveform"])), allow_pickle=False)
        np.testing.assert_allclose(actual, restored.engine.signal(old_review.processing, [1])[0], atol=1e-11)


def test_current_reference_and_old_decisions_keep_decompositions_but_candidate_is_temporary(fitted, tmp_path):
    session, _, first = fitted
    session.propose_ica(first)
    session.adopt()
    session.mark([0], True)
    second = replace(first, decomposition_id=str(uuid4()), input_processing=session.review.processing)
    session.propose_ica(second)
    session.adopt()
    third = replace(second, decomposition_id=str(uuid4()), input_processing=session.review.processing)
    session.propose_ica(third)
    path = save_session(session, tmp_path / "stages.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert {d["id"] for d in record["decompositions"]} == {first.decomposition_id, second.decomposition_id}
    restored = Session(dataset())
    restore_session(restored, path)
    np.testing.assert_allclose(
        restored.engine.signal(restored.review.processing), session.engine.signal(session.review.processing)
    )


def test_continuous_selection_and_saved_asr_stage_are_restored_without_rerunning(tmp_path, monkeypatch):
    session = Session(dataset(continuous=True))
    session.mark_interval(100, 200)
    session.mark_interval(150, 250)
    prepared = prepare_ica(session, channels=(0, 1, 2), max_iter=4)
    assert prepared.sample_intervals == ((0, 100), (250, 1600))
    assert prepared.sample_count == 1450
    assert prepare_ica(session, channels=(0, 1, 2), sample_scope="all").sample_count == 1600
    config = ASRConfig(Processing(), calibration_stop=5, highpass=None)
    processed = session.dataset.data * 0.9
    session.engine.install_asr(config, processed)
    session.propose(Processing(asr=config))
    session.adopt()
    assert not session.review.active_intervals and session.pending_review_count == 2
    path = save_session(session, tmp_path / "asr stage.json")
    monkeypatch.setattr(
        "eegqc.adapters.algorithms.asr_signal", lambda *_: pytest.fail("Do not rerun saved stage")
    )
    restored = Session(dataset(continuous=True))
    restore_session(restored, path)
    np.testing.assert_array_equal(restored.engine.signal(restored.review.processing), processed)


def test_v1_migration_and_failed_save_preserve_previous_session(fitted, tmp_path, monkeypatch):
    session, _, ica = fitted
    path = save_session(session, tmp_path / "legacy.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["schema_version"] = 1
    for key in ("array_file", "decompositions", "stages"):
        record.pop(key)

    # v1 Processing did not have decomposition_id.
    def remove_id(value):
        if isinstance(value, dict):
            value.pop("decomposition_id", None)
            for child in value.values():
                remove_id(child)
        elif isinstance(value, list):
            for child in value:
                remove_id(child)

    for key in ("review", "reference"):
        remove_id(record[key])
    path.write_text(json.dumps(record), encoding="utf-8")
    original = path.read_bytes()
    restored = Session(dataset())
    restore_session(restored, path)
    assert restored.review == session.review and path.read_bytes() == original
    session.propose_ica(ica)
    session.adopt()
    monkeypatch.setattr(np, "savez", lambda *_, **__: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        save_session(session, path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.npz.tmp"))


def test_prepare_rejects_component_count_above_effective_rank(fitted):
    session, _, _ = fitted
    with pytest.raises(ValueError, match="effective rank 2"):
        prepare_ica(session, channels=(0, 1, 2), n_components=3)


def test_corrupt_saved_matrix_fails_without_changing_current_work(fitted, tmp_path):
    session, _, ica = fitted
    session.propose_ica(ica)
    session.adopt()
    path = save_session(session, tmp_path / "invalid.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    arrays_path = tmp_path / record["array_file"]
    with np.load(arrays_path, allow_pickle=False) as arrays:
        values = dict(arrays)
    values["ica_0_mixing"] = np.zeros((2, 2))
    np.savez(arrays_path, **values)
    target = Session(dataset())
    target.mark([4], True)
    original_review, original_id = target.review, target.dataset.data_id
    with pytest.raises(ValueError, match="dimensions"):
        restore_session(target, path)
    assert target.review == original_review and target.dataset.data_id == original_id


def test_export_ica_retains_channel_order_when_dropping_nonparticipating_channel(fitted, tmp_path):
    session, _, ica = fitted
    session.propose_ica(ica)
    session.propose(replace(session.candidate, dropped=(3,)))
    session.adopt()
    exported = load_set(export_set(session, tmp_path / "partial channels.set"))
    assert exported.labels == ("A", "B", "C")
    assert tuple(exported.ica.channels) == (2, 0, 1)
    expected = session.engine.signal(session.review.processing, channels=[0, 1, 2])
    np.testing.assert_allclose(exported.data, expected, atol=2e-11)


@pytest.mark.parametrize("partial", [False, True])
def test_iclabel_input_matches_decomposition_channels_and_preserves_scientific_input(
    fitted, monkeypatch, partial
):
    import eegprep

    session, _, ica = fitted
    if partial:
        session.propose_ica(ica)
        ds = session.component_dataset()
        # The full recording's average reference is not the subset's average.
        ds.metadata["ref"] = "average"
    else:
        ds = session.dataset
        ds.metadata["ref"] = "common"
    ica = ds.ica
    original_data, original_mixing = ds.data.copy(), ica.mixing.copy()
    original_weights = ds.metadata["icaweights"].copy()
    expected_native = native_eeg(ds)

    def classifier(eeg):
        assert eeg["nbchan"] == len(ica.channels)
        assert [loc["labels"] for loc in eeg["chanlocs"]] == [ds.labels[i] for i in ica.channels]
        np.testing.assert_array_equal(eeg["icachansind"], np.arange(len(ica.channels)))
        np.testing.assert_array_equal(eeg["data"], expected_native["data"][ica.channels])
        for key in ("icaweights", "icasphere", "icawinv"):
            np.testing.assert_array_equal(eeg[key], expected_native[key])
        activations = np.einsum("ic,ect->ite", ica.unmixing, ds.data[:, ica.channels])
        np.testing.assert_allclose(eeg["icaact"], activations, atol=1e-12)
        assert eeg["ref"] not in ("average", "averef")
        rereferenced = eegprep.pop_reref(eeg, [])
        assert rereferenced["icawinv"].shape == ica.mixing.shape
        np.testing.assert_allclose(rereferenced["data"].mean(axis=0), 0, atol=1e-12)
        assert rereferenced["icaact"].shape == activations.shape
        assert np.isfinite(rereferenced["icaact"]).all()
        eeg["etc"]["ic_classification"] = {
            "ICLabel": {"classes": ["Brain", "Other"], "classifications": np.tile([0.7, 0.3], (ica.count, 1))}
        }
        return eeg

    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None, inference_mode=nullcontext)
    )
    monkeypatch.setattr(eegprep, "iclabel", classifier)
    report = classify_ics(ds)
    assert report["decomposition_id"] == ica.decomposition_id
    assert len(report["probabilities"]) == ica.count
    np.testing.assert_array_equal(ds.data, original_data)
    np.testing.assert_array_equal(ica.mixing, original_mixing)
    np.testing.assert_array_equal(ds.metadata["icaweights"], original_weights)


def test_label_autosaves_reuse_stage_arrays_after_restore_and_new_stage_preserves_backup(
    tmp_path, monkeypatch
):
    session = Session(dataset(continuous=True))
    config = ASRConfig(Processing(), cutoff=8, calibration_stop=5, highpass=None)
    session.engine.install_asr(config, session.dataset.data * 0.9)
    session.propose(Processing(asr=config))
    session.adopt()
    writes, sources = [], []
    original_savez, original_source = np.savez, session.engine.source

    def save_arrays(*args, **kwargs):
        writes.append(1)
        return original_savez(*args, **kwargs)

    def source(*args, **kwargs):
        sources.append(1)
        return original_source(*args, **kwargs)

    monkeypatch.setattr(np, "savez", save_arrays)
    monkeypatch.setattr(session.engine, "source", source)
    path = save_session(session, tmp_path / "autosave.json")
    first = json.loads(path.read_text(encoding="utf-8"))
    session.mark_interval(100, 150)
    session.set_rating("usable", "a note without scientific changes")
    save_session(session, path)
    assert len(writes) == len(sources) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["array_file"] == first["array_file"]
    restored = Session(dataset(continuous=True))
    restore_session(restored, path)
    restored.set_rating("usable", "restored note")
    save_session(restored, path)
    assert len(writes) == 1
    assert len(list(tmp_path.glob("*.npz"))) == 1
    next_config = replace(config, cutoff=12)
    restored.engine.install_asr(next_config, restored.dataset.data * 0.8)
    restored.propose(Processing(asr=next_config))
    restored.adopt()
    save_session(restored, path)
    latest = json.loads(path.read_text(encoding="utf-8"))
    assert len(writes) == 2 and latest["array_file"] != first["array_file"]
    backup = Session(dataset(continuous=True))
    restore_session(backup, path.with_suffix(".json.bak"))
    np.testing.assert_array_equal(backup.engine.signal(backup.review.processing), backup.dataset.data * 0.9)
    np.testing.assert_array_equal(
        restored.engine.signal(restored.review.processing), restored.dataset.data * 0.8
    )
