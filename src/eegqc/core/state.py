from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Processing:
    excluded_ics: tuple[int, ...] = ()  # all project indices are zero-based
    interpolated: tuple[int, ...] = ()
    dropped: tuple[int, ...] = ()
    average_reference: bool = False
    asr: ASRConfig | None = None
    decomposition_id: str | None = None


@dataclass(frozen=True)
class ASRConfig:
    parent: Processing
    cutoff: float = 20.0
    calibration_start: float = 0.0
    calibration_stop: float = 30.0
    highpass: float | None = 0.5
    implementation: str = "eegprep-0.4.0-scipy-pinv-v1"


@dataclass(frozen=True)
class DisplayFilter:
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class IntervalDecision:
    start: int  # source samples, zero-based, half-open
    stop: int
    processing: Processing
    reason: str = "signal_artifact"
    note: str = ""


@dataclass(frozen=True)
class EpochDecision:
    rejected: bool
    reason: str
    note: str
    processing: Processing
    origin: str = "human"
    suggestions_seen: tuple[str, ...] = ()
    display_context: dict = field(default_factory=dict)
    review_mode: str = "individual"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def quality(self):
        if self.reason in ("behavioral", "condition_selection", "uncertain"):
            return "uncertain"
        return "artifact" if self.rejected else "acceptable"


@dataclass(frozen=True)
class Review:
    processing: Processing = Processing()
    decisions: tuple[tuple[int, EpochDecision], ...] = ()
    rating: str = "待复核"
    note: str = ""
    intervals: tuple[IntervalDecision, ...] = ()

    @property
    def rejected(self) -> frozenset[int]:
        return frozenset(i for i, d in self.decisions if d.rejected and d.processing == self.processing)

    @property
    def active_intervals(self):
        return tuple(d for d in self.intervals if d.processing == self.processing)

    @property
    def pending_review_count(self):
        return sum(d.processing != self.processing for _, d in self.decisions) + sum(
            d.processing != self.processing for d in self.intervals
        )

    def record(self):
        return asdict(self)
