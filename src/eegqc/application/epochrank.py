"""Within-recording feedback ranking, independent of a pretrained EpochLabel model."""

from dataclasses import dataclass

import numpy as np
from scipy.spatial.distance import cdist
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from eegqc.core.state import Review
from eegqc.epochlabel.model import FEATURES, features

FEATURE_VERSION = "epochrank-v1"


@dataclass(frozen=True)
class Feedback:
    bad: tuple[int, ...]
    good: tuple[int, ...]
    reviewed: tuple[int, ...]


@dataclass
class Ranking:
    epochs: np.ndarray
    scores: np.ndarray
    nearest_bad: np.ndarray


def feedback(review: Review) -> Feedback:
    current = [(i, d) for i, d in review.decisions if d.processing == review.processing]
    return Feedback(
        bad=tuple(i for i, d in current if d.origin == "human" and d.quality == "artifact"),
        good=tuple(i for i, d in current if d.origin == "human" and d.quality == "acceptable"),
        reviewed=tuple(i for i, _ in current),
    )


def epoch_vectors(engine, processing):
    """Fixed, unsupervised scaling over this recording; never apply the display filter."""
    ds = engine.dataset
    if ds.continuous:
        raise ValueError("EpochRank requires epoched data")
    channels = [
        i
        for i, loc in enumerate(ds.chanlocs)
        if i not in processing.dropped and str(loc.get("type", "EEG")).upper() in ("", "EEG")
    ]
    if not channels:
        raise ValueError("EpochRank requires retained EEG channels")
    source = engine.source(processing)
    operator = engine.operator(processing)[channels]
    rows = []
    for epoch in source:
        signal = operator @ epoch
        centered = signal - signal.mean(axis=1, keepdims=True)
        spatial = np.log10(np.maximum(np.sqrt(np.mean(centered**2, axis=1)), 1e-12))
        rows.append(np.concatenate((features(signal, ds.sfreq), spatial)))
    vectors = StandardScaler().fit_transform(rows)
    # Channel count must not multiply the weight of the spatial amplitude block.
    vectors[:, len(FEATURES) :] /= np.sqrt(len(channels))
    return vectors


def rank_epochs(vectors, labels: Feedback, method="similarity") -> Ranking:
    if method not in ("similarity", "classifier"):
        raise ValueError(f"Unknown EpochRank method: {method}")
    if not labels.bad:
        raise ValueError("EpochRank needs at least one human artifact label in the current state")
    if method == "classifier" and not labels.good:
        raise ValueError("Feedback classification also needs a human acceptable label")
    pool = np.setdiff1d(np.arange(len(vectors)), labels.reviewed)
    if not len(pool):
        return Ranking(pool, np.empty(0), np.empty(0, dtype=int))
    distances = cdist(vectors[pool], vectors[list(labels.bad)])
    nearest = distances.argmin(axis=1)
    if method == "similarity":
        scores = distances[np.arange(len(pool)), nearest]
        order = np.argsort(scores, kind="stable")
    else:
        training = [*labels.bad, *labels.good]
        targets = [1] * len(labels.bad) + [0] * len(labels.good)
        model = LogisticRegression(class_weight="balanced", max_iter=1000)
        model.fit(vectors[training], targets)
        scores = model.decision_function(vectors[pool])
        order = np.argsort(-scores, kind="stable")
    return Ranking(pool[order], scores[order], np.asarray(labels.bad)[nearest[order]])
