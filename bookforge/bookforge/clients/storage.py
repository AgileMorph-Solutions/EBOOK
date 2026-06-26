"""Artifact storage — S3/MinIO in deployment, local filesystem fallback for the
Phase-0 spike so it runs with zero infra.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog

from bookforge.config import settings

log = structlog.get_logger(__name__)

_LOCAL_ROOT = Path(os.environ.get("BOOKFORGE_LOCAL_STORE", "./artifacts")).resolve()
_USE_S3 = os.environ.get("BOOKFORGE_USE_S3", "0") == "1"


def _s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    if _USE_S3:
        _s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=data,
                         ContentType=content_type)
        return key
    path = _LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def get(key: str) -> bytes:
    if _USE_S3:
        return _s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
    return (_LOCAL_ROOT / key).read_bytes()


def local_path(key: str) -> Path:
    """Resolve a key to a local path (downloading from S3 if needed)."""
    if not _USE_S3:
        return _LOCAL_ROOT / key
    path = _LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(get(key))
    return path
