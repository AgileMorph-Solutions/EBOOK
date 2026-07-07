"""Artifact storage — S3/MinIO in deployment, local filesystem fallback for the
Phase-0 spike so it runs with zero infra.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from bookforge.config import settings

log = structlog.get_logger(__name__)

_LOCAL_ROOT = Path(os.environ.get("BOOKFORGE_LOCAL_STORE", "./artifacts")).resolve()
_USE_S3 = os.environ.get("BOOKFORGE_USE_S3", "0") == "1"

# Transient S3/disk hiccups shouldn't lose a finished PDF or a generated image.
# Retry a few times with short backoff; a persistent failure still raises so the
# caller can degrade (image → skip) or the job can retry (final PDF put).
_storage_retry = retry(
    stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True,
)


def _s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


@_storage_retry
def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    if _USE_S3:
        _s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=data,
                         ContentType=content_type)
        return key
    path = _LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


@_storage_retry
def get(key: str) -> bytes:
    if _USE_S3:
        return _s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
    return (_LOCAL_ROOT / key).read_bytes()


def exists(key: str) -> bool:
    """Cheap existence check. Used by resume to decide whether to regenerate."""
    if _USE_S3:
        try:
            _s3().head_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except Exception:
            return False
    return (_LOCAL_ROOT / key).is_file()


def delete(key: str) -> None:
    """Best-effort delete. Missing keys are ignored."""
    if _USE_S3:
        try:
            _s3().delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception:
            log.warning("s3_delete_failed", key=key)
        return
    path = _LOCAL_ROOT / key
    try:
        path.unlink(missing_ok=True)
    except Exception:
        log.warning("local_delete_failed", key=key)


def local_path(key: str) -> Path:
    """Resolve a key to a local path (downloading from S3 if needed)."""
    if not _USE_S3:
        return _LOCAL_ROOT / key
    path = _LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(get(key))
    return path


def guides_out(name: str) -> Path:
    """Absolute output dir for gen_guide PDFs/manifests (matches image storage root)."""
    out = _LOCAL_ROOT / "guides" / name
    out.mkdir(parents=True, exist_ok=True)
    return out
