"""Object storage (ADR 0024): the system of record for run artefacts; `data/runs` is a cache.

Layout, one prefix per inspection, five kinds::

    <org>/<site>/<inspection>/video/<filename>          immutable (put refuses to overwrite)
    <org>/<site>/<inspection>/frames/…                  frames.json, jpgs, quality report, step.json
    <org>/<site>/<inspection>/sparse/…                  COLMAP work dir, rebuilt
    <org>/<site>/<inspection>/dense/…                   fused.ply (+ .vis), rebuilt
    <org>/<site>/<inspection>/bundle/web/manifest.json… the viewer bundle, cloud.laz/ply, rebuilt

Runs outside the catalog (CLI, benchmark) use org `_local`, site `_local`, inspection = run name.
`S3Storage` talks to Versity (dev) and to S3 (cloud) with the same code: endpoint, bucket and
credentials come from `QARAU_S3_*`; `storage_from_env()` is None without `QARAU_S3_BUCKET`, the
local-only mode. boto3 is imported only when a client is made.
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

from . import layout

KINDS = ("video", "frames", "sparse", "dense", "bundle")
LOCAL = "_local"


class Storage(Protocol):
    def put(self, key: str, data: Path | bytes) -> None: ...
    def get(self, key: str, dest: Path) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> None: ...
    def stream(self, key: str) -> Iterator[bytes]: ...
    def signed_url(self, key: str, ttl_s: int = 300) -> str: ...


def artifact_key(org: str, site: str, inspection: str, kind: str, relpath: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown artifact kind {kind!r}; one of {', '.join(KINDS)}")
    return f"{org}/{site}/{inspection}/{kind}/{relpath}"


def local_prefix(run_name: str) -> str:
    return f"{LOCAL}/{LOCAL}/{run_name}"


def key_for(prefix: str, relpath: str) -> str | None:
    """Run-relative path (`export/web/manifest.json`) -> key under `prefix`, via layout.KIND_DIRS;
    None for a path outside the stored kinds or with an empty, `.` or `..` part."""
    parts = relpath.split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
        return None
    kind = next((k for k, d in layout.KIND_DIRS.items() if d == parts[0]), None)
    if kind is None:
        return None
    org, site, inspection = prefix.split("/")
    return artifact_key(org, site, inspection, kind, "/".join(parts[1:]))


def _is_video(key: str) -> bool:
    parts = key.split("/")
    return len(parts) > 4 and parts[3] == "video"


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        client: Any = None,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket, self.region = bucket, region
        self._client: Any = client or boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            extra = {} if self.region == "us-east-1" else {
                "CreateBucketConfiguration": {"LocationConstraint": self.region}
            }  # fmt: skip
            self._client.create_bucket(Bucket=self.bucket, **extra)

    def put(self, key: str, data: Path | bytes) -> None:
        """Content type by extension, so a signed URL serves jpgs and JSON as such."""
        if _is_video(key) and self.exists(key):
            raise FileExistsError(f"{key} exists; videos are immutable (ADR 0024)")
        ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
        if isinstance(data, Path):
            self._client.upload_file(str(data), self.bucket, key, ExtraArgs={"ContentType": ctype})
        else:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=ctype)

    def get(self, key: str, dest: Path) -> None:
        """Download to a temp file next to `dest`, then rename: a reader sees whole files only."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.part")
        self._client.download_file(self.bucket, key, str(tmp))
        os.replace(tmp, dest)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def list(self, prefix: str) -> list[str]:
        pages = self._client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        )
        return sorted(o["Key"] for page in pages for o in page.get("Contents", []))

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def stream(self, key: str, chunk: int = 1 << 20) -> Iterator[bytes]:
        """The object's bytes in chunks; KeyError when there is no such object."""
        from botocore.exceptions import ClientError

        try:
            body = self._client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise KeyError(key) from e
            raise
        chunks: Iterator[bytes] = body.iter_chunks(chunk)
        return chunks

    def signed_url(self, key: str, ttl_s: int = 300) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_s
        )
        return url


def storage_from_env(env: Mapping[str, str] = os.environ) -> S3Storage | None:
    """`S3Storage` from QARAU_S3_* (the un-prefixed S3_* of the compose .env serve as fallback
    for endpoint and keys), or None when QARAU_S3_BUCKET is unset: the local-only mode."""
    bucket = env.get("QARAU_S3_BUCKET", "")
    if not bucket:
        return None
    s = S3Storage(
        bucket,
        env.get("QARAU_S3_ENDPOINT") or env.get("S3_ENDPOINT"),
        env.get("QARAU_S3_ACCESS_KEY") or env.get("S3_ACCESS_KEY"),
        env.get("QARAU_S3_SECRET_KEY") or env.get("S3_SECRET_KEY"),
        env.get("QARAU_S3_REGION") or "us-east-1",
    )
    s.ensure_bucket()
    return s
