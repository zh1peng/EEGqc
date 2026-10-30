from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np


def export_bundle(
    session, path, *, rater_id, groups, consent=False, include_waveforms=True, condition_fields=("condition",)
):
    """Export exactly the reviewed state of each epoch; paths and free text stay local."""
    if not consent:
        raise ValueError("Contribution requires explicit user consent")
    required = ("lab", "study", "participant", "recording")
    if any(not groups.get(key, "").strip() for key in required):
        raise ValueError("Supply consistent anonymous lab, study, participant and recording IDs")
    ds = session.dataset
    if ds.continuous:
        raise ValueError("EpochLabel contribution currently requires epoched data")
    if not session.review.decisions:
        raise ValueError("No manually reviewed epochs to contribute")
    samples = []
    manifest = {
        "schema_version": 1,
        "bundle_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "consent": True,
        "rater_id": rater_id,
        "groups": {k: groups[k].strip() for k in required},
        "data_id": ds.data_id,
        "decomposition_id": ds.ica.decomposition_id if ds.ica else None,
        "sfreq": ds.sfreq,
        "tmin": ds.tmin,
        "units": "V",
        "channels": list(ds.labels),
        "chanlocs": [
            {
                k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                for k, v in loc.items()
                if k in ("labels", "type", "X", "Y", "Z", "theta", "radius")
            }
            for loc in ds.chanlocs
        ],
        "include_waveforms": include_waveforms,
        "samples": samples,
    }
    path = Path(path)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for index, decision in session.review.decisions:
            record = {
                "sample_id": f"{ds.data_id}:{index}",
                "epoch_index": index,
                "quality": decision.quality,
                "action": "reject" if decision.rejected else "keep",
                "reason": decision.reason,
                "origin": decision.origin,
                "suggestions_seen": list(decision.suggestions_seen),
                "processing": asdict(decision.processing),
                "decomposition_id": (
                    ds.ica_for(decision.processing).decomposition_id
                    if ds.ica_for(decision.processing) is not None
                    else None
                ),
                "display_context": {
                    k: v for k, v in decision.display_context.items() if k != "erp_conditions"
                },
                "review_mode": decision.review_mode,
                "timestamp": decision.timestamp,
                "conditions": {
                    k: str(ds.conditions[k][index]) for k in condition_fields if k in ds.conditions
                },
                "excluded_channels": list(decision.processing.dropped),
            }
            if include_waveforms:
                filename = f"waveforms/{index:06d}.npy"
                stream = BytesIO()
                np.save(
                    stream,
                    session.engine.signal(decision.processing, [index])[0].astype(np.float32),
                    allow_pickle=False,
                )
                archive.writestr(filename, stream.getvalue())
                record["waveform"] = filename
            samples.append(record)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
    return path


def read_manifest(path):
    with ZipFile(path) as archive:
        return json.loads(archive.read("manifest.json"))
