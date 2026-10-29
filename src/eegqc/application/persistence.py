import csv
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import numpy as np

from eegqc.core.dataset import ICA
from eegqc.core.preview import PreviewEngine
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
        record.get("decomposition_id"),
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
    path = Path(path)
    decompositions, stages = {}, {}

    def include(proc):
        if proc.asr is not None:
            if proc.asr not in stages:
                stages[proc.asr] = f"stage_{len(stages)}"
                include(proc.asr.parent)
        if proc.decomposition_id is not None and proc.decomposition_id not in decompositions:
            ica = ds.ica_for(proc)
            decompositions[ica.decomposition_id] = ica
            include(ica.input_processing)

    for review in (session.review, session.reference):
        include(review.processing)
        for _, decision in review.decisions:
            include(decision.processing)
        for decision in review.intervals:
            include(decision.processing)
    # Matrices and scientific stages are immutable under their ordinary IDs.
    # Label/notes autosaves therefore only need to replace the small JSON.
    package_key = (path.resolve(), frozenset(decompositions), frozenset(stages))
    package = getattr(session, "_array_package", None)
    arrays = {}
    if package is not None and package["key"] == package_key and package["path"].exists():
        array_path = package["path"]
        decomposition_records, stage_records = package["decompositions"], package["stages"]
    else:
        decomposition_records = []
        for index, ica in enumerate(decompositions.values()):
            prefix = f"ica_{index}"
            arrays[f"{prefix}_channels"] = ica.channels
            arrays[f"{prefix}_unmixing"] = ica.unmixing
            arrays[f"{prefix}_mixing"] = ica.mixing
            decomposition_records.append(
                {
                    "id": ica.decomposition_id,
                    "arrays": prefix,
                    "input_processing": asdict(ica.input_processing),
                    "fit": ica.fit,
                }
            )
        for config, key in stages.items():
            arrays[key] = session.engine.source(Processing(asr=config))
        stage_records = [{"config": asdict(config), "array": key} for config, key in stages.items()]
        array_path = path.with_name(f"{path.stem}.{uuid4().hex}.npz") if arrays else None
    record = {
        "schema_version": 2,
        "source": source,
        "data_id": ds.data_id,
        "shape": list(ds.data.shape),
        "labels": list(ds.labels),
        "sfreq": ds.sfreq,
        "decomposition_id": ds.ica.decomposition_id if ds.ica is not None else None,
        "source_ica": (
            {"channels": ds.ica.channels.tolist(), "count": ds.ica.count} if ds.ica is not None else None
        ),
        "array_file": array_path.name if array_path else None,
        "decompositions": decomposition_records,
        "stages": stage_records,
        "review": session.review.record(),
        "reference": session.reference.record(),
        "iclabel": session.iclabel,
        "contribution_groups": session.contribution_groups,
        "erp_conditions": {name: asdict(query) for name, query in session.erp_conditions.items()},
    }
    pending = path.with_suffix(path.suffix + ".tmp")
    array_pending = array_path.with_suffix(".npz.tmp") if arrays else None
    try:
        if array_pending:
            with array_pending.open("wb") as handle:
                np.savez(handle, **arrays)
            array_pending.replace(array_path)
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        pending.replace(path)
    except (OSError, TypeError, ValueError):
        pending.unlink(missing_ok=True)
        if array_pending:
            array_pending.unlink(missing_ok=True)
            array_path.unlink(missing_ok=True)
        raise
    session._array_package = (
        {
            "key": package_key,
            "path": array_path,
            "decompositions": decomposition_records,
            "stages": stage_records,
        }
        if array_path
        else None
    )
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
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    legacy = record["schema_version"] == 1
    if legacy:
        # The single supported migration: v1 had only the imported decomposition.
        record = dict(record, schema_version=2, array_file=None, decompositions=[], stages=[])
    if record["schema_version"] != 2:
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
    decompositions, stage_files = {}, {}
    array_file = record.get("array_file")
    if array_file is not None:
        if Path(array_file).name != array_file:
            raise ValueError("Session array file must be adjacent to the session JSON")
        array_path = path.parent / array_file
        with np.load(array_path, allow_pickle=False) as arrays:
            for d in record["decompositions"]:
                prefix = d["arrays"]
                ica = ICA(
                    arrays[f"{prefix}_channels"],
                    arrays[f"{prefix}_unmixing"],
                    arrays[f"{prefix}_mixing"],
                    d["id"],
                    _processing(d["input_processing"]),
                    d["fit"],
                )
                ds.validate_ica(ica)
                decompositions[ica.decomposition_id] = ica
            for stage in record["stages"]:
                config = _processing(
                    dict(
                        excluded_ics=[],
                        interpolated=[],
                        dropped=[],
                        average_reference=False,
                        asr=stage["config"],
                    )
                ).asr
                values = arrays[stage["array"]]
                if (
                    values.shape != ds.data.shape
                    or not np.issubdtype(values.dtype, np.floating)
                    or not np.isfinite(values).all()
                ):
                    raise ValueError("Saved processing stage does not match the dataset")
                stage_files[config] = (array_path, stage["array"])
    elif record["decompositions"] or record["stages"]:
        raise ValueError("Session is missing its processing arrays")
    review, reference = _review(record["review"]), _review(record["reference"])

    def validate_processing(proc, ancestors=()):
        if proc.asr is not None:
            if not legacy and proc.asr not in stage_files:
                raise ValueError("Session is missing a required saved ASR stage")
            validate_processing(proc.asr.parent, ancestors)
        if proc.decomposition_id is not None:
            key = proc.decomposition_id
            if key in ancestors or key not in decompositions:
                raise ValueError("Invalid or missing ICA input stage in session")
            validate_processing(decompositions[key].input_processing, (*ancestors, key))

    for rev in (review, reference):
        validate_processing(rev.processing)
        for _, decision in rev.decisions:
            validate_processing(decision.processing)
        for decision in rev.intervals:
            validate_processing(decision.processing)
    if (record["decomposition_id"] is None) != (ds.ica is None):
        raise ValueError("The source ICA layout differs from the saved session")
    if "source_ica" in record:
        layout = {"channels": ds.ica.channels.tolist(), "count": ds.ica.count} if ds.ica else None
        if layout != record["source_ica"]:
            raise ValueError("The source ICA layout differs from the saved session")
    conditions = {
        name: Condition(tuple(Clause(**c) for c in query["clauses"]), query["mode"])
        for name, query in record.get("erp_conditions", {}).items()
    }
    ds.data_id = record["data_id"]
    if ds.ica is not None:
        ds.ica.decomposition_id = record["decomposition_id"]
    ds.decompositions = decompositions
    session.engine = PreviewEngine(ds)
    session.engine._stage_files = stage_files
    session._array_package = (
        {
            "key": (path.resolve(), frozenset(decompositions), frozenset(stage_files)),
            "path": array_path,
            "decompositions": record["decompositions"],
            "stages": record["stages"],
        }
        if array_file
        else None
    )
    session._commit(review)
    session.candidate = review.processing
    session._undo.clear()
    session._redo.clear()
    session.reference = reference
    session.iclabel = record.get("iclabel")
    session.contribution_groups = record.get("contribution_groups", {})
    session.erp_conditions = conditions
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
