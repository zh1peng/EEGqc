import csv
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from eegqc.core.state import ASRConfig, EpochDecision, IntervalDecision, Processing, Review
from eegqc.core.conditions import Clause, Condition


def _processing(record):
    asr = record.get("asr")
    if asr is not None:
        asr = dict(asr)
        asr["parent"] = _processing(asr["parent"])
        asr = ASRConfig(**asr)
    return Processing(
        tuple(record["excluded_ics"]),
        tuple(record["interpolated"]),
        tuple(record["dropped"]),
        record["average_reference"],
        asr,
    )


def _source_record(dataset):
    if dataset.source is None:
        return None
    paths = [dataset.source]
    datfile = dataset.metadata.get("datfile")
    if isinstance(datfile, str) and datfile:
        paths.append(dataset.source.parent / datfile)
    result = []
    for path in paths:
        stat = path.stat()
        result.append({"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return result


def save_session(session, path: str | Path):
    ds = session.dataset
    source = _source_record(ds)
    record = {
        "schema_version": 1,
        "source": source,
        "data_id": ds.data_id,
        "shape": list(ds.data.shape),
        "labels": list(ds.labels),
        "sfreq": ds.sfreq,
        "decomposition_id": ds.ica.decomposition_id if ds.ica is not None else None,
        "review": session.review.record(),
        "reference": session.reference.record(),
        "iclabel": session.iclabel,
        "contribution_groups": session.contribution_groups,
        "erp_conditions": {name: asdict(query) for name, query in session.erp_conditions.items()},
    }
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    pending.replace(path)
    session.saved_revision = session.revision
    return path


def _review(record):
    decisions = []
    for index, decision in record["decisions"]:
        d = dict(decision)
        d["processing"] = _processing(d["processing"])
        d["suggestions_seen"] = tuple(d.get("suggestions_seen", []))
        decisions.append((int(index), EpochDecision(**d)))
    intervals = tuple(
        IntervalDecision(d["start"], d["stop"], _processing(d["processing"]), d["reason"], d["note"])
        for d in record.get("intervals", [])
    )
    return Review(
        _processing(record["processing"]), tuple(decisions), record["rating"], record["note"], intervals
    )


def restore_session(session, path: str | Path):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record["schema_version"] != 1:
        raise ValueError("Unsupported session file version")
    ds = session.dataset
    if (
        record["shape"] != list(ds.data.shape)
        or record["labels"] != list(ds.labels)
        or record["sfreq"] != ds.sfreq
    ):
        raise ValueError("The session belongs to a different signal layout")
    if record["source"] is not None:
        if ds.source is None:
            raise ValueError("Open the original .set file before restoring this session")
        if record["source"] != _source_record(ds):
            raise ValueError(
                "The source file has changed or moved; re-associate it before restoring decisions"
            )
    ds.data_id = record["data_id"]
    if ds.ica is not None:
        ds.ica.decomposition_id = record["decomposition_id"]
    session._commit(_review(record["review"]))
    session.reference = _review(record["reference"])
    session.iclabel = record.get("iclabel")
    session.contribution_groups = record.get("contribution_groups", {})
    session.erp_conditions = {
        name: Condition(tuple(Clause(**c) for c in query["clauses"]), query["mode"])
        for name, query in record.get("erp_conditions", {}).items()
    }
    session.saved_revision = session.revision


def export_labels(session, path: str | Path):
    """Export reviewed items only, including accepted and uncertain decisions' context."""
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "epoch_index",
                "source_trial_id",
                "reviewed",
                "quality",
                "action",
                "reason",
                "note",
                "origin",
                "suggestions_seen",
                "timestamp",
                "processing",
            ],
        )
        writer.writeheader()
        for index, decision in session.review.decisions:
            writer.writerow(
                {
                    "epoch_index": index + 1,
                    "source_trial_id": session.dataset.trial_ids[index],
                    "reviewed": True,
                    "quality": decision.quality,
                    "action": "reject" if decision.rejected else "keep",
                    "reason": decision.reason,
                    "note": decision.note,
                    "origin": decision.origin,
                    "timestamp": decision.timestamp,
                    "suggestions_seen": json.dumps(decision.suggestions_seen),
                    "processing": json.dumps(asdict(decision.processing), ensure_ascii=False),
                }
            )
    return path
