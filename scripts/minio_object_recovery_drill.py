"""Exercise recovery of two isolated S3 objects without touching project media.

The drill creates exactly ``recovery-drill/<uuid>/target.bin`` and its
``snapshot.bin`` copy.  It never lists a bucket, never changes a bucket policy,
and never derives a key from a persisted media record.  Before cleanup, any
failure retains every successfully created object for inspection.  Cleanup is
attempted only after the restored target passes an independent integrity check;
if cleanup itself partially fails, the exact known state is reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


CONTENT_TYPE = "application/vnd.douyin-research.recovery-drill"
CHUNK_SIZE = 1024 * 1024


class DrillError(RuntimeError):
    """A deliberate, non-sensitive drill failure."""

    def __init__(
        self,
        stage: str,
        *,
        cleanup: str = "retained_on_failure",
        object_states: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(stage)
        self.cleanup = cleanup
        self.object_states = dict(object_states or {})


@dataclass(frozen=True, slots=True)
class DrillObjects:
    run_id: str
    target: str
    snapshot: str
    sha256: str
    size: int


def _is_missing(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False
    metadata = response.get("ResponseMetadata")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    detail = response.get("Error")
    code = detail.get("Code") if isinstance(detail, Mapping) else None
    return status == 404 or str(code) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}


def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise DrillError("endpoint_invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise DrillError("endpoint_invalid")
    return value.rstrip("/")


def _validate_bucket(value: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise DrillError("bucket_invalid")
    return value


def load_credentials(path: str | Path) -> tuple[str, str]:
    """Read only the MinIO access/secret pair without disclosing either value."""

    candidate = Path(path)
    descriptor: int | None = None
    try:
        before = candidate.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise DrillError("credentials_file_invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise DrillError("credentials_file_invalid")
        if opened.st_mode & 0o077:
            raise DrillError("credentials_file_permissions_invalid")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            value = json.load(handle)
    except DrillError:
        raise
    except Exception:
        raise DrillError("credentials_file_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise DrillError("credentials_file_invalid")
    access, secret = value.get("accessKey"), value.get("secretKey")
    if not isinstance(access, str) or not access.strip() or not isinstance(secret, str) or not secret.strip():
        raise DrillError("credentials_file_invalid")
    return access, secret


def _head(client: Any, bucket: str, key: str) -> Mapping[str, Any] | None:
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        if _is_missing(error):
            return None
        raise DrillError("object_head_failed") from None
    if not isinstance(response, Mapping):
        raise DrillError("object_head_invalid")
    return response


def _must_be_missing(client: Any, bucket: str, key: str, *, stage: str) -> None:
    if _head(client, bucket, key) is not None:
        raise DrillError(f"{stage}_already_exists")


def _validate_head(head: Mapping[str, Any], objects: DrillObjects) -> None:
    metadata = head.get("Metadata")
    if not isinstance(metadata, Mapping):
        raise DrillError("object_metadata_invalid")
    if (
        head.get("ContentLength") != objects.size
        or str(head.get("ContentType", "")).lower() != CONTENT_TYPE
        or metadata.get("sha256") != objects.sha256
        or metadata.get("recovery-drill") != objects.run_id
    ):
        raise DrillError("object_metadata_mismatch")


def _stream_validate(client: Any, bucket: str, key: str, objects: DrillObjects) -> None:
    body = None
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        digest = hashlib.sha256()
        size = 0
        for chunk in body.iter_chunks(chunk_size=CHUNK_SIZE):
            if not isinstance(chunk, bytes):
                raise DrillError("object_stream_invalid")
            size += len(chunk)
            if size > objects.size:
                raise DrillError("object_stream_size_mismatch")
            digest.update(chunk)
        if size != objects.size or digest.hexdigest() != objects.sha256:
            raise DrillError("object_stream_integrity_mismatch")
    except DrillError:
        raise
    except Exception:
        raise DrillError("object_stream_failed") from None
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass


def _copy(client: Any, bucket: str, source: str, destination: str) -> None:
    try:
        client.copy_object(
            Bucket=bucket,
            Key=destination,
            CopySource={"Bucket": bucket, "Key": source},
            MetadataDirective="COPY",
        )
    except Exception:
        raise DrillError("object_copy_failed") from None


def _delete(client: Any, bucket: str, key: str) -> None:
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        raise DrillError("object_delete_failed") from None


def _objects_for(payload: bytes, run_id: str) -> DrillObjects:
    if not isinstance(run_id, str) or len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        raise DrillError("run_id_invalid")
    if not isinstance(payload, bytes) or not payload:
        raise DrillError("payload_invalid")
    digest = hashlib.sha256(payload).hexdigest()
    prefix = f"recovery-drill/{run_id}"
    return DrillObjects(run_id, f"{prefix}/target.bin", f"{prefix}/snapshot.bin", digest, len(payload))


def run_drill(client: Any, *, bucket: str, payload: bytes | None = None, run_id: str | None = None) -> DrillObjects:
    """Run the bounded drill and report partial cleanup without hiding state."""

    token = run_id or uuid.uuid4().hex
    data = payload if payload is not None else secrets.token_bytes(4096)
    objects = _objects_for(data, token)

    _must_be_missing(client, bucket, objects.target, stage="target")
    _must_be_missing(client, bucket, objects.snapshot, stage="snapshot")
    try:
        client.put_object(
            Bucket=bucket,
            Key=objects.target,
            Body=data,
            ContentLength=objects.size,
            ContentType=CONTENT_TYPE,
            Metadata={"sha256": objects.sha256, "recovery-drill": objects.run_id},
        )
    except Exception:
        raise DrillError("target_put_failed") from None
    target = _head(client, bucket, objects.target)
    if target is None:
        raise DrillError("target_missing_after_put")
    _validate_head(target, objects)
    _stream_validate(client, bucket, objects.target, objects)

    _copy(client, bucket, objects.target, objects.snapshot)
    snapshot = _head(client, bucket, objects.snapshot)
    if snapshot is None:
        raise DrillError("snapshot_missing_after_copy")
    _validate_head(snapshot, objects)
    _stream_validate(client, bucket, objects.snapshot, objects)

    _delete(client, bucket, objects.target)
    _must_be_missing(client, bucket, objects.target, stage="target_after_delete")

    _copy(client, bucket, objects.snapshot, objects.target)
    restored = _head(client, bucket, objects.target)
    if restored is None:
        raise DrillError("target_missing_after_restore")
    _validate_head(restored, objects)
    _stream_validate(client, bucket, objects.target, objects)

    # Cleanup is intentionally reached only after all recovery checks pass.
    states = {"target": "present", "snapshot": "present"}
    try:
        _delete(client, bucket, objects.target)
        states["target"] = "delete_requested"
        _must_be_missing(client, bucket, objects.target, stage="target_cleanup")
        states["target"] = "absent"
        _delete(client, bucket, objects.snapshot)
        states["snapshot"] = "delete_requested"
        _must_be_missing(client, bucket, objects.snapshot, stage="snapshot_cleanup")
        states["snapshot"] = "absent"
    except DrillError as error:
        raise DrillError(
            str(error),
            cleanup="partial",
            object_states=states,
        ) from None
    return objects


def _client(*, endpoint: str, access_key: str, secret_key: str, region: str):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise DrillError("boto3_unavailable") from None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        use_ssl=True,
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"total_max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"ok": False, "reason": "execute_switch_required"}), file=sys.stderr)
        return 2
    objects: DrillObjects | None = None
    try:
        endpoint = _validate_endpoint(args.endpoint)
        bucket = _validate_bucket(args.bucket)
        access, secret_key = load_credentials(args.credentials_file)
        payload = secrets.token_bytes(4096)
        objects = _objects_for(payload, uuid.uuid4().hex)
        # Do not include this client, config, exception, endpoint, or credentials in output.
        run_drill(
            _client(endpoint=endpoint, access_key=access, secret_key=secret_key, region=args.region),
            bucket=bucket,
            payload=payload,
            run_id=objects.run_id,
        )
    except DrillError as error:
        failure: dict[str, object] = {"ok": False, "stage": str(error), "cleanup": error.cleanup}
        if error.object_states:
            failure["object_states"] = error.object_states
        if objects is not None:
            failure.update({"run_id": objects.run_id, "target": objects.target, "snapshot": objects.snapshot})
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        # Do not let an SDK/configuration exception render credentials or a URL.
        failure = {"ok": False, "stage": "client_initialization_failed", "cleanup": "retained_on_failure"}
        if objects is not None:
            failure.update({"run_id": objects.run_id, "target": objects.target, "snapshot": objects.snapshot})
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "result": "MINIO_OBJECT_RECOVERY_DRILL_PASSED",
        "run_id": objects.run_id,
        "target": objects.target,
        "snapshot": objects.snapshot,
        "sha256": objects.sha256,
        "bytes": objects.size,
        "cleanup": "target_and_snapshot_deleted",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
