from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4
from eegqc.core.state import Processing

import numpy as np


@dataclass
class ICA:
    """EEGLAB-native activation scale, with mixing expressed in volts."""

    channels: np.ndarray
    unmixing: np.ndarray
    mixing: np.ndarray
    decomposition_id: str = field(default_factory=lambda: str(uuid4()))
    input_processing: Processing | None = None
    fit: dict = field(default_factory=dict)

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
    decompositions: dict[str, ICA] = field(default_factory=dict)

    def __post_init__(self):
        if self.data.ndim != 3 or self.data.shape[1] != len(self.labels):
            raise ValueError("Expected epochs × channels × samples, with matching channel labels")
        if self.data.shape[0] != len(self.trial_ids) or self.sfreq <= 0:
            raise ValueError("Trial IDs or sampling frequency do not match the signal")
        if any(len(v) != self.n_epochs for v in self.conditions.values()):
            raise ValueError("Condition columns must have one entry per epoch")
        if self.ica is not None:
            self.validate_ica(self.ica)
        self.data.flags.writeable = False

    def validate_ica(self, ica):
        channels = np.asarray(ica.channels)
        n = len(channels)
        if (
            channels.ndim != 1
            or not np.issubdtype(channels.dtype, np.integer)
            or len(set(channels.tolist())) != n
            or np.any(channels < 0)
            or np.any(channels >= self.n_channels)
        ):
            raise ValueError("ICA channels must be distinct dataset channel indices")
        if (
            ica.unmixing.ndim != 2
            or ica.unmixing.shape[1] != n
            or ica.mixing.shape != (n, ica.count)
            or not 0 < ica.count <= n
        ):
            raise ValueError("ICA dimensions do not match its channels")
        if not np.issubdtype(ica.unmixing.dtype, np.floating) or not np.issubdtype(
            ica.mixing.dtype, np.floating
        ):
            raise ValueError("ICA matrices must use real floating point values")
        if not np.isfinite(ica.unmixing).all() or not np.isfinite(ica.mixing).all():
            raise ValueError("ICA matrices must be finite")

    def ica_for(self, processing: Processing):
        if processing.asr is not None:
            return None
        if processing.decomposition_id is not None:
            try:
                return self.decompositions[processing.decomposition_id]
            except KeyError:
                raise ValueError(f"Missing ICA decomposition {processing.decomposition_id}") from None
        return self.ica

    @property
    def n_epochs(self):
        return self.data.shape[0]

    @property
    def n_channels(self):
        return self.data.shape[1]

    @property
    def times(self):
        return self.tmin + np.arange(self.data.shape[2]) / self.sfreq
