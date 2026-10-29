import json
from pathlib import Path
from uuid import uuid4
from urllib.request import Request, urlopen


def upload_bundle(path, endpoint, token):
    request = Request(
        endpoint.rstrip("/") + "/contributions",
        data=Path(path).read_bytes(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/zip"},
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    source = Path(path)
    receipt_path = source.with_name(f"{source.stem}.{uuid4().hex[:8]}.receipt.json")
    receipt_path.write_text(
        json.dumps({"endpoint": endpoint.rstrip("/"), "receipt": result["receipt"]}, indent=2),
        encoding="utf-8",
    )
    return {**result, "receipt_file": str(receipt_path)}


def withdraw_bundle(receipt_file, token):
    record = json.loads(Path(receipt_file).read_text(encoding="utf-8"))
    request = Request(
        record["endpoint"] + "/contributions/" + record["receipt"],
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)
