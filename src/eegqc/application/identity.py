import json
import os
from pathlib import Path
from uuid import uuid4


def local_rater_id(directory=None):
    directory = (
        Path(directory)
        if directory is not None
        else Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "EEGqc"
    )
    path = directory / "identity.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["rater_id"]
    directory.mkdir(parents=True, exist_ok=True)
    identity = str(uuid4())
    path.write_text(json.dumps({"rater_id": identity}), encoding="utf-8")
    return identity
