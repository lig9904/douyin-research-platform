from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from douyin_research.media_storage import (
    MediaStorageError,
    MediaStorageIntegrityError,
    PrivateS3MediaStorage,
    S3MediaStorageConfig,
)


SYNTHETIC_ACCESS_KEY = "synthetic-access-key-never-log"
SYNTHETIC_SECRET = "synthetic-secret-never-log"


class _ClientError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.uploads: list[tuple[str, str, str, dict[str, object]]] = []
        self.head_error: Exception | None = None
        self.presign_error: Exception | None = None

    def head_object(self, *, Bucket: str, Key: str):
        if self.head_error is not None:
            raise self.head_error
        if Key not in self.objects:
            raise _ClientError(404, "NoSuchKey")
        return self.objects[Key]

    def upload_file(self, filename: str, bucket: str, key: str, *, ExtraArgs: dict[str, object]) -> None:
        self.uploads.append((filename, bucket, key, ExtraArgs))
        payload = Path(filename).read_bytes()
        self.objects[key] = {
            "ContentLength": len(payload),
            "Metadata": dict(ExtraArgs["Metadata"]),
        }

    def generate_presigned_url(self, operation: str, *, Params: dict[str, str], ExpiresIn: int, HttpMethod: str) -> str:
        if self.presign_error is not None:
            raise self.presign_error
        return f"http://signed.invalid/{Params['Key']}?synthetic-signature&expires={ExpiresIn}"


def _config(**overrides: object) -> S3MediaStorageConfig:
    values: dict[str, object] = {
        "endpoint": "http://minio.internal:9000",
        "bucket": "douyin-research-media",
        "region": "us-east-1",
        "access_key_id": SYNTHETIC_ACCESS_KEY,
        "secret_access_key": SYNTHETIC_SECRET,
        "force_path_style": True,
        "use_ssl": False,
    }
    values.update(overrides)
    return S3MediaStorageConfig(**values)  # type: ignore[arg-type]


def test_configuration_hides_credentials_and_requires_matching_transport() -> None:
    config = _config()
    assert SYNTHETIC_ACCESS_KEY not in repr(config)
    assert SYNTHETIC_SECRET not in repr(config)
    assert "minio.internal" in repr(config)
    with pytest.raises(ValueError, match="match use_ssl"):
        _config(endpoint="https://minio.internal:9000")
    with pytest.raises(ValueError, match="bucket"):
        _config(bucket="bucket/path")
    with pytest.raises(ValueError, match="endpoint"):
        _config(endpoint="http://key:secret@minio.internal:9000/?ignored=true#fragment")
    with pytest.raises(ValueError, match="public_endpoint"):
        _config(public_endpoint="http://media.example.test")


def test_upload_is_private_content_addressed_and_deduplicated(tmp_path: Path) -> None:
    file = tmp_path / "sample.mp4"
    file.write_bytes(b"synthetic media bytes")
    client = _FakeS3()
    store = PrivateS3MediaStorage(_config(), client=client)

    first = store.upload_file(file, content_type="video/mp4")
    second = store.upload_file(file, content_type="video/mp4")

    expected_sha = hashlib.sha256(b"synthetic media bytes").hexdigest()
    assert first == second
    assert first.key == f"sha256/{expected_sha[:2]}/{expected_sha}"
    assert first.size == len(b"synthetic media bytes")
    assert first.sha256 == expected_sha
    assert len(client.uploads) == 1
    _filename, bucket, key, extra = client.uploads[0]
    assert bucket == "douyin-research-media"
    assert key == first.key
    assert extra == {"ContentType": "video/mp4", "Metadata": {"sha256": expected_sha}}
    assert "ACL" not in extra


def test_existing_mismatched_content_addressed_object_is_not_overwritten(tmp_path: Path) -> None:
    file = tmp_path / "sample.mp3"
    file.write_bytes(b"media")
    client = _FakeS3()
    store = PrivateS3MediaStorage(_config(), client=client)
    sha = hashlib.sha256(b"media").hexdigest()
    client.objects[store.object_key(sha)] = {"ContentLength": 5, "Metadata": {"sha256": "not-the-digest"}}

    with pytest.raises(MediaStorageIntegrityError):
        store.upload_file(file)
    assert client.uploads == []


def test_unknown_or_forbidden_head_failures_are_not_treated_as_missing(tmp_path: Path) -> None:
    file = tmp_path / "sample.wav"
    file.write_bytes(b"media")
    for failure in (_ClientError(403, "AccessDenied"), _ClientError(500, "InternalError")):
        client = _FakeS3()
        client.head_error = failure
        store = PrivateS3MediaStorage(_config(), client=client)
        with pytest.raises(MediaStorageError, match="object check") as caught:
            store.upload_file(file)
        assert SYNTHETIC_SECRET not in str(caught.value)
        assert client.uploads == []


def test_presigned_reads_are_short_lived_and_failures_hide_provider_details() -> None:
    client = _FakeS3()
    store = PrivateS3MediaStorage(_config(), client=client)
    key = store.object_key("a" * 64)
    assert "synthetic-signature" in store.presigned_read_url(key, expires_in=900)
    for expiry in (59, 3601, True):
        with pytest.raises(ValueError, match="expiry"):
            store.presigned_read_url(key, expires_in=expiry)  # type: ignore[arg-type]
    client.presign_error = RuntimeError(SYNTHETIC_SECRET)
    with pytest.raises(MediaStorageError, match="signed-read") as caught:
        store.presigned_read_url(key)
    assert SYNTHETIC_SECRET not in str(caught.value)


def test_public_https_endpoint_uses_a_separate_signing_client() -> None:
    upload_client = _FakeS3()
    signing_client = _FakeS3()
    store = PrivateS3MediaStorage(
        _config(public_endpoint="https://media.example.test"),
        client=upload_client,
        signing_client=signing_client,
    )
    key = store.object_key("b" * 64)

    assert store.presigned_read_url(key).startswith("http://signed.invalid/")
    # The primary private endpoint client must not create a public-read URL.
    upload_client.presign_error = RuntimeError("wrong client")
    assert store.presigned_read_url(key).startswith("http://signed.invalid/")


@pytest.mark.parametrize(
    "key",
    [
        "sha256/aa/" + "A" * 64,
        "sha256/ab/" + "a" * 64,
        "sha256/aa/" + "a" * 63,
        "sha256/aa/" + "a" * 64 + "/extra",
    ],
)
def test_presign_requires_exact_canonical_content_key(key: str) -> None:
    store = PrivateS3MediaStorage(_config(), client=_FakeS3())
    with pytest.raises(ValueError, match="content-addressed"):
        store.presigned_read_url(key)


def test_local_failures_and_invalid_keys_are_safe(tmp_path: Path) -> None:
    store = PrivateS3MediaStorage(_config(), client=_FakeS3())
    with pytest.raises(MediaStorageError, match="cannot be read"):
        store.upload_file(tmp_path / "missing.mp4")
    with pytest.raises(ValueError, match="content-addressed"):
        store.presigned_read_url("media/public.mp4")
    with pytest.raises(ValueError, match="SHA-256"):
        store.object_key("f" * 63)
    assert store.object_key("F" * 64) == "sha256/ff/" + "f" * 64
