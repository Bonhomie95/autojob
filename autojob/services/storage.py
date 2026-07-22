"""
Uploaded-file storage abstraction.

Two backends behind one interface: ``local`` (files under a root dir, for dev)
and ``s3`` (any S3-compatible object store, for prod). Keys are always
tenant-prefixed — ``cv/<user_id>/<hash>.<ext>`` — so one user's files can never
collide with or be addressed as another's. Nothing here trusts a client-
supplied path; the key is derived from the authenticated user id plus a content
hash we compute server-side.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from flask import current_app

logger = logging.getLogger(__name__)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cv_key(user_id: str, digest: str, ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return f"cv/{user_id}/{digest}.{ext}"


class _LocalBackend:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # Resolve and confine within root — defence against any '..' in a key.
        p = (self.root / key).resolve()
        root = self.root.resolve()
        if not str(p).startswith(str(root)):
            raise ValueError("storage key escapes root")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def open_tempfile(self, key: str, suffix: str) -> str:
        """Materialise the object as a local temp file; return its path."""
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(self.get(key))
        return tmp


class _S3Backend:
    def __init__(self, bucket: str, endpoint: str = "", region: str = ""):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            region_name=region or None,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def open_tempfile(self, key: str, suffix: str) -> str:
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(self.get(key))
        return tmp


def get_storage():
    backend = current_app.config.get("STORAGE_BACKEND", "local")
    if backend == "s3":
        return _S3Backend(
            bucket=current_app.config["S3_BUCKET"],
            endpoint=current_app.config.get("S3_ENDPOINT_URL", ""),
            region=current_app.config.get("S3_REGION", ""),
        )
    return _LocalBackend(current_app.config.get("STORAGE_LOCAL_ROOT", "storage"))
