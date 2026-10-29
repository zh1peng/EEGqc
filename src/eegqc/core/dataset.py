from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np


@dataclass
class ICA:
    """EEGLAB-native activation scale, with mixing expressed in volts."""

    channels: np.ndarray
    unmixing: np.ndarray
    mixing: np.ndarray
    decomposition_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def count(self) -> int:
        return self.unmixing.shape[0]


@dataclass
class Dataset:
    data: np.ndarray  # epochs × channels × samples, volts; continuous has one epoch
    sfreq: float
    tmin: float
    labels: tuple[str, ...]
    chanlocs: list[dict]
    trial_ids: tuple[str, ...]
    conditions: dict[str, np.ndarray]
    metadata: dict[str, Any]
    name: str
    source: Path | None = None
    ica: ICA | None = None
    continuous: bool = False
    data_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self):
        if self.data.ndim != 3 or self.data.shape[1] != len(self.labels):
            raise ValueError("Expected epochs × channels × samples, with matching channel labels")
        if self.data.shape[0] != len(self.trial_ids) or self.sfreq <= 0:
            raise ValueError("Trial IDs or sampling frequency do not match the signal")
        if any(len(v) != self.n_epochs for v in self.conditions.values()):
            raise ValueError("Condition columns must have one entry per epoch")
        if self.ica is not None:
            n = len(self.ica.channels)
            if self.ica.unmixing.shape[1] != n or self.ica.mixing.shape != (n, self.ica.count):
                raise ValueError("ICA dimensions do not match its channels")
        self.data.flags.writeable = False

    @property
    def n_epochs(self):
        return self.data.shape[0]

    @property
    def n_channels(self):
        return self.data.shape[1]

    @property
    def times(self):
        return self.tmin + np.arange(self.data.shape[2]) / self.sfreq
