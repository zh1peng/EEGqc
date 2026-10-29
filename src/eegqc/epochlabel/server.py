"""Small opt-in collection service. No archive extraction or arbitrary file paths."""

from io import BytesIO
import json
from pathlib import Path
import secrets
from zipfile import BadZipFile, ZipFile


def create_app(directory, token):
    from fastapi import FastAPI, HTTPException, Request

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="EEGqc EpochLabel collection")

    def authorize(request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}"):
            raise HTTPException(401, "Invalid contribution token")

    @app.post("/contributions", status_code=201)
    async def contribute(request: Request):
        authorize(request)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 256 * 1024 * 1024:
                raise HTTPException(413, "Contribution exceeds 256 MB")
        try:
            with ZipFile(BytesIO(body)) as archive:
                info = archive.getinfo("manifest.json")
                if info.file_size > 4 * 1024 * 1024:
                    raise ValueError("Manifest exceeds 4 MB")
                manifest = json.loads(archive.read(info))
                if (
                    manifest["schema_version"] != 1
                    or manifest["consent"] is not True
                    or not manifest["samples"]
                ):
                    raise ValueError("Unsupported or unconsented contribution")
                if any(not manifest["groups"].get(k) for k in ("lab", "study", "participant", "recording")):
                    raise ValueError("Missing anonymous group IDs")
                if manifest["include_waveforms"]:
                    for sample in manifest["samples"]:
                        if archive.getinfo(sample["waveform"]).file_size > 32 * 1024 * 1024:
                            raise ValueError("Individual epoch exceeds 32 MB")
        except (BadZipFile, KeyError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        # Server-owned ID: no supplied filename, path or bundle ID becomes a filesystem path.
        receipt = secrets.token_hex(12)
        pending = directory / f"{receipt}.part"
        pending.write_bytes(body)
        pending.replace(directory / f"{receipt}.zip")
        return {"receipt": receipt, "samples": len(manifest["samples"])}

    @app.delete("/contributions/{receipt}")
    def remove(receipt: str, request: Request):
        authorize(request)
        if len(receipt) != 24 or any(c not in "0123456789abcdef" for c in receipt):
            raise HTTPException(404, "Contribution not found")
        path = directory / f"{receipt}.zip"
        if not path.exists():
            raise HTTPException(404, "Contribution not found")
        path.unlink()
        return {"removed": receipt}

    return app


def main():
    import argparse
    import os
    import uvicorn

    parser = argparse.ArgumentParser(description="Run a local EpochLabel contribution receiver")
    parser.add_argument("--directory", default="contributions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get("EEGQC_CONTRIBUTION_TOKEN")
    if not token:
        parser.error("Set EEGQC_CONTRIBUTION_TOKEN to your project contribution token")
    uvicorn.run(create_app(args.directory, token), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
