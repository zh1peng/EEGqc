from io import BytesIO
import json
from zipfile import ZipFile

import numpy as np
import pytest

from eegqc.application.identity import local_rater_id
from eegqc.application.session import Session
from eegqc.core.state import Processing
from eegqc.demo import make_demo
from eegqc.epochlabel.bundle import export_bundle, read_manifest
from eegqc.epochlabel.model import fit_baseline, predict_features, training_samples


def bundle(tmp_path, participant="p1"):
    session = Session(make_demo(18, 18, 100.0, 1.0))
    session.view_context = {"display_filter": {"low": 1.0, "high": 40.0}}
    session.mark([0, 1, 2], False, note="private note must stay local")
    session.propose(Processing(excluded_ics=(0,)))
    session.adopt()
    session.mark([4, 17], True, "eye")
    session.mark([3], True, "behavioral")
    groups = {"lab": "lab1", "study": "study1", "participant": participant, "recording": "r1"}
    path = export_bundle(
        session, tmp_path / f"{participant}.zip", rater_id="rater1", groups=groups, consent=True
    )
    return session, path


def test_bundle_preserves_judged_signal_state_and_excludes_private_notes(tmp_path):
    session, path = bundle(tmp_path)
    with ZipFile(path) as archive:
        payload = archive.read("manifest.json")
        manifest = json.loads(payload)
        assert b"private note" not in payload
        for sample in manifest["samples"]:
            decision = dict(session.review.decisions)[sample["epoch_index"]]
            signal = np.load(BytesIO(archive.read(sample["waveform"])), allow_pickle=False)
            expected = session.engine.signal(decision.processing, [sample["epoch_index"]])[0]
            np.testing.assert_allclose(signal, expected, atol=1e-11)
    assert manifest["samples"][0]["processing"]["excluded_ics"] == []
    assert "source_trial_id" not in manifest["samples"][0]
    assert next(s for s in manifest["samples"] if s["epoch_index"] == 3)["quality"] == "uncertain"
    assert len(manifest["samples"]) == 6
    assert read_manifest(path)["consent"] is True
    with pytest.raises(ValueError, match="consent"):
        export_bundle(session, tmp_path / "no.zip", rater_id="rater1", groups=manifest["groups"])


def test_rater_id_is_stable_without_an_account(tmp_path):
    assert local_rater_id(tmp_path) == local_rater_id(tmp_path)


def test_collection_receipt_authorization_and_withdrawal(tmp_path):
    from fastapi.testclient import TestClient
    from eegqc.epochlabel.server import create_app

    _, path = bundle(tmp_path)
    store = tmp_path / "received"
    client = TestClient(create_app(store, "project-token"))
    assert client.post("/contributions", content=path.read_bytes()).status_code == 401
    headers = {"Authorization": "Bearer project-token"}
    result = client.post("/contributions", content=path.read_bytes(), headers=headers)
    assert result.status_code == 201
    receipt = result.json()["receipt"]
    assert result.json()["samples"] == 6
    assert (store / f"{receipt}.zip").exists()
    assert client.delete(f"/contributions/{receipt}", headers=headers).status_code == 200
    assert not (store / f"{receipt}.zip").exists()


def test_baseline_split_is_participant_disjoint_and_json_probabilities_are_valid(tmp_path):
    paths = [bundle(tmp_path, f"p{i}")[1] for i in range(6)]
    model_path = tmp_path / "baseline.json"
    report = fit_baseline(paths, model_path)
    assert set(report["training_groups"]).isdisjoint(report["test_groups"])
    assert report["training_samples"] + report["test_samples"] == 30  # behavioral exclusions omitted
    matrix, labels, _ = training_samples(paths)
    assert len(training_samples([*paths, paths[0]])[0]) == len(matrix)
    probabilities = predict_features(json.loads(model_path.read_text()), matrix)
    assert probabilities.shape == labels.shape
    assert np.isfinite(probabilities).all() and np.all((probabilities >= 0) & (probabilities <= 1))


def test_upload_saves_a_receipt_without_the_token_and_withdraws_that_batch(tmp_path, monkeypatch):
    from eegqc.epochlabel import client

    _, path = bundle(tmp_path)
    requests = []

    def request(request, timeout):
        requests.append(request)
        if request.method == "POST":
            return BytesIO(json.dumps({"receipt": "a" * 24, "samples": 6}).encode())
        return BytesIO(json.dumps({"removed": "a" * 24}).encode())

    monkeypatch.setattr(client, "urlopen", request)
    result = client.upload_bundle(path, "http://localhost:8765", "private-token")
    receipt_file = result["receipt_file"]
    from pathlib import Path

    assert "private-token" not in Path(receipt_file).read_text()
    assert client.withdraw_bundle(receipt_file, "private-token")["removed"] == "a" * 24
    assert requests[-1].method == "DELETE"
