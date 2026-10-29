"""An explicit binary research baseline; model artifacts are JSON, not executable pickles."""

from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import numpy as np
from scipy.special import expit
from scipy.signal import welch

FEATURES = (
    "log_rms_median",
    "log_rms_max",
    "log_ptp_median",
    "log_ptp_max",
    "log_diff_rms",
    "flat_fraction",
    "kurtosis_median",
    "high_power_fraction",
    "low_power_fraction",
    "line_power_fraction",
)


def features(signal, sfreq):
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 2 or not signal.size or not np.isfinite(signal).all():
        raise ValueError("Epoch features require finite channel × sample data")
    centered = signal - signal.mean(axis=1, keepdims=True)
    variance = np.mean(centered**2, axis=1)
    rms = np.sqrt(variance)
    ptp = np.ptp(signal, axis=1)
    differences = np.diff(signal, axis=1)
    freqs, power = welch(centered, fs=sfreq, nperseg=min(signal.shape[1], max(8, round(sfreq))))
    total = max(float(power.sum()), 1e-24)

    def fraction(lo, hi):
        return float(power[:, (freqs >= lo) & (freqs <= hi)].sum() / total)

    def log(x):
        return np.log10(max(float(x), 1e-12))  # fixed V-unit feature floor for zero/flat signals

    return np.asarray(
        [
            log(np.median(rms)),
            log(np.max(rms)),
            log(np.median(ptp)),
            log(np.max(ptp)),
            log(np.sqrt(np.mean(differences**2))),
            float(np.mean(np.abs(differences) < 1e-9)),
            float(np.median(np.mean(centered**4, axis=1) / np.maximum(variance**2, 1e-48))),
            fraction(30, min(100, sfreq / 2)),
            fraction(0.1, 3),
            fraction(49, 51) + fraction(59, 61),
        ]
    )


def training_samples(paths, group_by="participant"):
    x, y, groups = [], [], []
    latest = {}
    for path in paths:
        with ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest["units"] != "V":
                raise ValueError("EpochLabel waveforms must declare volt units")
            for sample in manifest["samples"]:
                grouping = manifest["groups"]
                key = (
                    grouping["lab"],
                    grouping["study"],
                    grouping["participant"],
                    grouping["recording"],
                    manifest["rater_id"],
                    sample["epoch_index"],
                    json.dumps(sample["processing"], sort_keys=True),
                )
                if key not in latest or sample["timestamp"] >= latest[key][2]["timestamp"]:
                    latest[key] = (path, manifest, sample)
    # Repeated uploads do not multiply votes. Corrections replace the same rater's earlier decision.
    selected_by_path = {}
    for path, manifest, sample in latest.values():
        selected_by_path.setdefault(path, []).append((manifest, sample))
    for path, samples in selected_by_path.items():
        with ZipFile(path) as archive:
            for manifest, sample in samples:
                grouping = manifest["groups"]
                group = grouping["lab"] if group_by == "lab" else grouping["lab"] + ":" + grouping[group_by]
                if sample["quality"] not in ("acceptable", "artifact"):
                    continue
                if "waveform" not in sample:
                    raise ValueError("Training requires contributed waveforms")
                signal = np.load(BytesIO(archive.read(sample["waveform"])), allow_pickle=False)
                signal = np.delete(signal, sample.get("excluded_channels", []), axis=0)
                x.append(features(signal, manifest["sfreq"]))
                y.append(int(sample["quality"] == "artifact"))
                groups.append(group)
    return np.asarray(x), np.asarray(y), np.asarray(groups)


def fit_baseline(paths, output, group_by="participant"):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, classification_report
    from sklearn.model_selection import GroupShuffleSplit
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x, y, groups = training_samples(paths, group_by)
    if len(np.unique(groups)) < 2 or len(np.unique(y)) < 2:
        raise ValueError(
            "Training needs acceptable/artifact labels and at least two independent participant groups"
        )
    split = None
    for train, test in GroupShuffleSplit(n_splits=20, test_size=0.25, random_state=17).split(x, y, groups):
        if len(np.unique(y[train])) == 2 and len(np.unique(y[test])) == 2:
            split = train, test
            break
    if split is None:
        raise ValueError(
            "Cannot form a participant-disjoint split containing both classes; collect more groups"
        )
    train, test = split
    pipeline = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000))
    pipeline.fit(x[train], y[train])
    probabilities = pipeline.predict_proba(x[test])[:, 1]
    scaler, classifier = pipeline.steps[0][1], pipeline.steps[1][1]
    result = {
        "schema_version": 1,
        "model_id": str(uuid4()),
        "algorithm": "EpochLabel-logistic-baseline",
        "group_by": group_by,
        "features": list(FEATURES),
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coefficients": classifier.coef_[0].tolist(),
        "intercept": float(classifier.intercept_[0]),
        "training_groups": sorted(set(groups[train].tolist())),
        "test_groups": sorted(set(groups[test].tolist())),
        "training_samples": len(train),
        "test_samples": len(test),
        "metrics": {
            "classification": classification_report(
                y[test], probabilities >= 0.5, output_dict=True, zero_division=0
            ),
            "pr_auc": float(average_precision_score(y[test], probabilities)),
            "brier": float(brier_score_loss(y[test], probabilities)),
        },
    }
    Path(output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def predict_features(model, values):
    if model["schema_version"] != 1 or model["features"] != list(FEATURES):
        raise ValueError("Unsupported EpochLabel feature/model version")
    normalized = (values - np.asarray(model["mean"])) / np.asarray(model["scale"])
    return expit(normalized @ np.asarray(model["coefficients"]) + model["intercept"])


def predict_epochs(session, model):
    processing = session.review.processing
    retained = [i for i in range(session.dataset.n_channels) if i not in processing.dropped]
    matrix = []
    for epoch in range(session.dataset.n_epochs):
        signal = session.engine.signal(processing, [epoch], retained)[0]
        matrix.append(features(signal, session.dataset.sfreq))
    return predict_features(model, np.asarray(matrix))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Train a participant-disjoint EpochLabel research baseline")
    parser.add_argument("bundles", nargs="+")
    parser.add_argument("--output", default="epochlabel-baseline.json")
    parser.add_argument("--group-by", choices=("participant", "study", "lab"), default="participant")
    args = parser.parse_args()
    report = fit_baseline(args.bundles, args.output, args.group_by)
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
