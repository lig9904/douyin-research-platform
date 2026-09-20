"""Private, content-addressed S3-compatible media storage.

This module deliberately contains no application configuration loading.  Callers
must pass a reviewed configuration (normally assembled from a Secret), which
makes it impossible for this boundary to silently fall back to a public bucket
or a different endpoint.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class MediaStorageError(RuntimeError):
    """A safe media-storage failure which never includes provider details."""


class MediaStorageIntegrityError(MediaStorageError):
    """A content-addressed object exists but does not match its declared hash."""


@dataclass(frozen=True, slots=True, repr=False)
class S3MediaStorageConfig:
    """Explicit S3-compatible configuration; ``repr`` intentionally hides keys."""

    endpoint: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    force_path_style: bool
    use_ssl: bool
    public_endpoint: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "endpoint",
            "bucket",
            "region",
            "access_key_id",
            "secret_access_key",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"S3 {name} is required")
        if type(self.force_path_style) is not bool or type(self.use_ssl) is not bool:
            raise ValueError("S3 force_path_style and use_ssl must be booleans")

        parsed = self._origin(self.endpoint, name="endpoint", https_only=False)
        if self.use_ssl != (parsed.scheme == "https"):
            raise ValueError("S3 endpoint scheme must match use_ssl")
        if self.public_endpoint is not None:
            self._origin(self.public_endpoint, name="public_endpoint", https_only=True)
        if "/" in self.bucket or self.bucket in {".", ".."}:
            raise ValueError("S3 bucket must be a bucket name, not a path")

    def __repr__(self) -> str:
        return (
            "S3MediaStorageConfig("
            f"endpoint={self.endpoint!r}, bucket={self.bucket!r}, region={self.region!r}, "
            f"force_path_style={self.force_path_style!r}, use_ssl={self.use_ssl!r}, "
            f"public_endpoint={self.public_endpoint!r}, "
            "access_key_id=<redacted>, secret_access_key=<redacted>)"
        )

    @staticmethod
    def _origin(value: str, *, name: str, https_only: bool) -> Any:
        parsed = urlsplit(value)
        valid_schemes = {"https"} if https_only else {"http", "https"}
        if (
            parsed.scheme not in valid_schemes
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            requirement = "an HTTPS absolute origin URL" if https_only else "an absolute origin URL"
            raise ValueError(f"S3 {name} must be {requirement}")
        return parsed


@dataclass(frozen=True, slots=True)
class StoredMediaObject:
    """Non-secret metadata for a private content-addressed media object."""

    key: str
    size: int
    sha256: str
    content_type: str


class PrivateS3MediaStorage:
    """Upload and read private content-addressed objects from an S3 API.

    Object keys are generated exclusively from SHA-256.  No ACL is supplied to
    S3, so this adapter cannot make an object public as part of an upload.
    Bucket-level public access controls remain an operator responsibility.
    """

    _CHUNK_SIZE = 1024 * 1024
    _MIN_PRESIGN_SECONDS = 60
    _MAX_PRESIGN_SECONDS = 3600

    def __init__(
        self,
        config: S3MediaStorageConfig,
        *,
        client: Any | None = None,
        signing_client: Any | None = None,
    ) -> None:
        if not isinstance(config, S3MediaStorageConfig):
            raise TypeError("S3MediaStorageConfig is required")
        self._config = config
        self._client = client if client is not None else self._create_client(config, endpoint=config.endpoint)
        self._signing_client = (
            signing_client
            if signing_client is not None
            else (
                self._client
                if config.public_endpoint is None
                else self._create_client(config, endpoint=config.public_endpoint)
            )
        )

    @staticmethod
    def _create_client(config: S3MediaStorageConfig, *, endpoint: str) -> Any:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - packaging boundary
            raise RuntimeError("boto3 is required for S3 media storage") from exc
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            # A public signing endpoint may be HTTPS while the private MinIO
            # endpoint is HTTP; derive transport from the client endpoint.
            use_ssl=urlsplit(endpoint).scheme == "https",
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 1, "mode": "standard"},
                s3={
                    "addressing_style": "path" if config.force_path_style else "virtual",
                }
            ),
        )

    @staticmethod
    def object_key(sha256: str) -> str:
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
            raise ValueError("SHA-256 must be a 64-character hexadecimal string")
        canonical = sha256.lower()
        return f"sha256/{canonical[:2]}/{canonical}"

    def upload_file(
        self, path: str | Path, *, content_type: str | None = None
    ) -> StoredMediaObject:
        local_path = Path(path)
        sha256, size = self._digest_file(local_path)
        key = self.object_key(sha256)
        normalized_content_type = self._content_type(local_path, content_type)
        stored = StoredMediaObject(key, size, sha256, normalized_content_type)

        existing = self._head(key)
        if existing is not None:
            self._validate_existing(existing, stored)
            return stored

        try:
            # No ACL / Grant* options are ever passed: uploads stay private.
            self._client.upload_file(
                str(local_path),
                self._config.bucket,
                key,
                ExtraArgs={
                    "ContentType": normalized_content_type,
                    "Metadata": {"sha256": sha256},
                },
            )
        except Exception as exc:
            raise MediaStorageError("S3 media upload failed") from None

        # A post-write head detects a broken gateway or a collision before the
        # caller receives a success result.
        confirmed = self._head(key)
        if confirmed is None:
            raise MediaStorageError("S3 media upload could not be confirmed")
        self._validate_existing(confirmed, stored)
        return stored

    def presigned_read_url(self, key: str, *, expires_in: int = 900) -> str:
        if not self._is_canonical_key(key):
            raise ValueError("only content-addressed media keys may be signed")
        if type(expires_in) is not int or not (
            self._MIN_PRESIGN_SECONDS <= expires_in <= self._MAX_PRESIGN_SECONDS
        ):
            raise ValueError("S3 signed-read expiry must be between 60 and 3600 seconds")
        try:
            return self._signing_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._config.bucket, "Key": key},
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        except Exception:
            raise MediaStorageError("S3 signed-read URL creation failed") from None

    @property
    def bucket(self) -> str:
        """Non-secret bucket identity for persisted asset binding."""
        return self._config.bucket

    def verify_object(self, stored: StoredMediaObject) -> None:
        """Confirm a persisted reference still points to the expected object."""
        if stored.key != self.object_key(stored.sha256):
            raise ValueError("invalid content-addressed media reference")
        existing = self._head(stored.key)
        if existing is None:
            raise MediaStorageError("S3 media object is missing")
        self._validate_existing(existing, stored)

    def download_file(self, stored: StoredMediaObject, destination: str | Path) -> None:
        """Recover an uploaded asset for an interrupted extraction, no CDN call."""
        self.verify_object(stored)
        target = Path(destination)
        if target.exists() or target.is_symlink():
            raise MediaStorageError("local media destination already exists")
        descriptor, name = tempfile.mkstemp(prefix=".s3-media-", dir=target.parent)
        temporary = Path(name)
        body = None
        try:
            with os.fdopen(descriptor, "wb") as output:
                response = self._client.get_object(Bucket=self._config.bucket, Key=stored.key)
                body = response["Body"]
                size = 0
                digest = hashlib.sha256()
                for chunk in body.iter_chunks(chunk_size=self._CHUNK_SIZE):
                    size += len(chunk)
                    if size > stored.size:
                        raise MediaStorageIntegrityError("S3 media read exceeds recorded size")
                    digest.update(chunk)
                    output.write(chunk)
            if size != stored.size or digest.hexdigest() != stored.sha256:
                raise MediaStorageIntegrityError("S3 media read failed integrity validation")
            os.link(temporary, target)
        except MediaStorageError:
            raise
        except Exception:
            raise MediaStorageError("S3 media download failed") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass  # Cleanup must not leak a transport's URL/error.
            temporary.unlink(missing_ok=True)

    @classmethod
    def _is_canonical_key(cls, key: object) -> bool:
        if not isinstance(key, str):
            return False
        match = re.fullmatch(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})", key)
        if match is None or match.group(1) != match.group(2)[:2]:
            return False
        return key == cls.object_key(match.group(2))

    def _head(self, key: str) -> Mapping[str, Any] | None:
        try:
            result = self._client.head_object(Bucket=self._config.bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            # Access denied is deliberately not considered a missing object.
            raise MediaStorageError("S3 media object check failed") from None
        if not isinstance(result, Mapping):
            raise MediaStorageError("S3 media object check returned an invalid response")
        return result

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        response = getattr(error, "response", None)
        if not isinstance(response, Mapping):
            return False
        metadata = response.get("ResponseMetadata")
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
        detail = response.get("Error")
        code = detail.get("Code") if isinstance(detail, Mapping) else None
        return status == 404 or str(code) in {"404", "NoSuchKey", "NotFound", "NoSuchObject"}

    @staticmethod
    def _validate_existing(existing: Mapping[str, Any], expected: StoredMediaObject) -> None:
        metadata = existing.get("Metadata")
        supplied_sha = metadata.get("sha256") if isinstance(metadata, Mapping) else None
        if (supplied_sha != expected.sha256
            or existing.get("ContentLength") != expected.size
            or _canonical_content_type(existing.get("ContentType"))
                != _canonical_content_type(expected.content_type)):
            raise MediaStorageIntegrityError("S3 content-addressed media object failed integrity validation")

    @classmethod
    def _digest_file(cls, path: Path) -> tuple[str, int]:
        try:
            with path.open("rb") as handle:
                digest = hashlib.sha256()
                size = 0
                while chunk := handle.read(cls._CHUNK_SIZE):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError:
            raise MediaStorageError("local media file cannot be read") from None
        return digest.hexdigest(), size

    @staticmethod
    def _content_type(path: Path, explicit: str | None) -> str:
        if explicit is not None:
            if not isinstance(explicit, str) or not explicit.strip() or "\r" in explicit or "\n" in explicit:
                raise ValueError("media content type is invalid")
            return explicit.strip()
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _canonical_content_type(value: object) -> str | None:
    # Media type is case-insensitive; parameter values may not be. Preserve
    # parameter order/case and require explicit metadata, never assume a MIME.
    if not isinstance(value, str) or not value.strip():
        return None
    media_type, *parameters = value.split(";")
    return ";".join([media_type.strip().lower(), *(part.strip() for part in parameters)])
