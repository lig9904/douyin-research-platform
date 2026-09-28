from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from douyin_research.media_storage import (
    MediaStorageError,
    MediaStorageIntegrityError,
    MediaStoragePromotionVerificationError,
    PrivateS3MediaStorage,
    S3MediaStorageConfig,
    StoredMediaObject,
)


SYNTHETIC_ACCESS_KEY = "synthetic-access-key-never-log"
SYNTHETIC_SECRET = "synthetic-secret-never-log"


class _ClientError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.bodies: dict[str, bytes] = {}
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
        self.bodies[key] = payload
        self.objects[key] = {
            "ContentLength": len(payload),
            "ContentType": ExtraArgs["ContentType"],
            "Metadata": dict(ExtraArgs["Metadata"]),
        }

    def get_object(self, *, Bucket, Key):
        from botocore.response import StreamingBody
        payload = self.bodies[Key]
        return {"Body": StreamingBody(io.BytesIO(payload), len(payload))}

    def generate_presigned_url(self, operation: str, *, Params: dict[str, str], ExpiresIn: int, HttpMethod: str) -> str:
        if self.presign_error is not None:
            raise self.presign_error
        return f"http://signed.invalid/{Params['Key']}?synthetic-signature&expires={ExpiresIn}"


class _TrackedBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def iter_chunks(self, *, chunk_size: int):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset:offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class _VersionedFakeS3(_FakeS3):
    def __init__(self) -> None:
        super().__init__()
        self.versions: dict[str, dict[str, object]] = {}
        self.latest: str | None = None
        self.get_calls: list[dict[str, str]] = []
        self.signed_params: list[dict[str, str]] = []
        self.get_error: Exception | None = None
        self.last_body: _TrackedBody | None = None

    def get_object(self, **params: str):
        self.get_calls.append(params)
        if self.get_error is not None:
            raise self.get_error
        version = params.get("VersionId", self.latest)
        if version not in self.versions:
            raise _ClientError(404, "NoSuchVersion")
        response = self.versions[version].copy()
        self.last_body = _TrackedBody(response.pop("Payload"))  # type: ignore[arg-type]
        return {**response, "Body": self.last_body}

    def generate_presigned_url(self, operation: str, *, Params: dict[str, str], ExpiresIn: int, HttpMethod: str) -> str:
        self.signed_params.append(Params.copy())
        return super().generate_presigned_url(operation, Params=Params, ExpiresIn=ExpiresIn, HttpMethod=HttpMethod)


def _versioned_object(payload: bytes = b"original bytes") -> tuple[StoredMediaObject, _VersionedFakeS3, PrivateS3MediaStorage]:
    sha = hashlib.sha256(payload).hexdigest()
    stored = StoredMediaObject(PrivateS3MediaStorage.object_key(sha), len(payload), sha, "video/mp4")
    client = _VersionedFakeS3()
    client.versions["version-one"] = {
        "VersionId": "version-one",
        "ContentLength": len(payload),
        "ContentType": "video/mp4",
        "Payload": payload,
    }
    client.latest = "version-one"
    return stored, client, PrivateS3MediaStorage(_config(), client=client)


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


def test_existing_mime_mismatch_rejected_on_upload_and_reference_verification(tmp_path: Path) -> None:
    file = tmp_path / "sample.wav"
    file.write_bytes(b"synthetic-wav")
    client = _FakeS3()
    store = PrivateS3MediaStorage(_config(), client=client)
    stored = store.upload_file(file, content_type="audio/wav")
    client.objects[stored.key]["ContentType"] = "video/mp4"
    with pytest.raises(MediaStorageIntegrityError):
        store.upload_file(file, content_type="audio/wav")
    with pytest.raises(MediaStorageIntegrityError):
        store.verify_object(stored)
    assert len(client.uploads) == 1
    client.objects[stored.key]["ContentType"] = "Audio/WAV"
    store.verify_object(stored)
    del client.objects[stored.key]["ContentType"]
    with pytest.raises(MediaStorageIntegrityError):
        store.verify_object(stored)


def test_recover_private_object_verifies_bytes_and_never_overwrites(tmp_path: Path) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic-video")
    client = _FakeS3()
    store = PrivateS3MediaStorage(_config(), client=client)
    stored = store.upload_file(original)
    recovered = tmp_path / "recovered.mp4"
    store.download_file(stored, recovered)
    assert recovered.read_bytes() == original.read_bytes()
    with pytest.raises(MediaStorageError, match="already exists"):
        store.download_file(stored, recovered)
    client.bodies[stored.key] = b"corrupted"
    with pytest.raises(MediaStorageIntegrityError):
        store.download_file(stored, tmp_path / "corrupt.mp4")
    assert not (tmp_path / "corrupt.mp4").exists()
    assert not list(tmp_path.glob(".s3-media-*"))
    assert len(client.uploads) == 1


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


def test_verified_version_reads_full_bytes_and_pins_old_version_after_overwrite() -> None:
    stored, client, store = _versioned_object()
    assert store.verified_version(stored) == "version-one"
    assert client.get_calls == [{"Bucket": store.bucket, "Key": stored.key}]
    assert client.last_body is not None and client.last_body.closed

    # The current key has been overwritten, but the pinned old version is intact.
    client.versions["version-two"] = {
        "VersionId": "version-two", "ContentLength": stored.size,
        "ContentType": "video/mp4", "Payload": b"overwritten!!",
    }
    client.latest = "version-two"
    with pytest.raises(MediaStorageIntegrityError):
        store.verified_version(stored)
    assert client.last_body is not None and client.last_body.closed
    assert store.verified_version(stored, version_id="version-one") == "version-one"
    assert client.get_calls[-1] == {"Bucket": store.bucket, "Key": stored.key, "VersionId": "version-one"}
    assert client.last_body is not None and client.last_body.closed

    store.presigned_read_url(stored.key)
    store.presigned_read_url(stored.key, version_id="version-one")
    assert client.signed_params == [
        {"Bucket": store.bucket, "Key": stored.key},
        {"Bucket": store.bucket, "Key": stored.key, "VersionId": "version-one"},
    ]


@pytest.mark.parametrize("version", [None, "", "null", "NULL", " "])
def test_verified_version_rejects_missing_or_null_response_version(version: str | None) -> None:
    stored, client, store = _versioned_object()
    client.versions["version-one"]["VersionId"] = version
    with pytest.raises(MediaStorageIntegrityError):
        store.verified_version(stored)
    assert client.last_body is not None and client.last_body.closed


def test_verified_version_rejects_wrong_bytes_headers_or_requested_version() -> None:
    stored, client, store = _versioned_object()
    client.versions["version-one"]["Payload"] = b"different bytes"
    with pytest.raises(MediaStorageIntegrityError):
        store.verified_version(stored)
    assert client.last_body is not None and client.last_body.closed
    client.versions["version-one"]["Payload"] = b"original bytes"
    client.versions["version-one"]["ContentLength"] = stored.size + 1
    with pytest.raises(MediaStorageIntegrityError):
        store.verified_version(stored)
    client.versions["version-one"]["ContentLength"] = stored.size
    client.versions["version-one"]["ContentType"] = "audio/mpeg"
    with pytest.raises(MediaStorageIntegrityError):
        store.verified_version(stored)
    client.versions["version-one"]["ContentType"] = "video/mp4"
    client.versions["version-one"]["VersionId"] = "another-version"
    with pytest.raises(MediaStorageIntegrityError):
        store.verified_version(stored, version_id="version-one")


def test_verified_version_fails_closed_on_get_errors_and_invalid_references() -> None:
    stored, client, store = _versioned_object()
    for failure in (_ClientError(403, "AccessDenied"), _ClientError(404, "NoSuchVersion")):
        client.get_error = failure
        with pytest.raises(MediaStorageError, match="verification failed") as caught:
            store.verified_version(stored, version_id="version-one")
        assert "AccessDenied" not in str(caught.value)
    client.get_error = None
    for version in ("", "null", " ", " version-one "):
        with pytest.raises(ValueError, match="version ID"):
            store.verified_version(stored, version_id=version)
        with pytest.raises(ValueError, match="version ID"):
            store.presigned_read_url(stored.key, version_id=version)
    with pytest.raises(ValueError, match="content-addressed"):
        store.verified_version(StoredMediaObject("media/untrusted", stored.size, stored.sha256, stored.content_type))


class _LegacyPromotionFakeS3(_VersionedFakeS3):
    def __init__(self, payload: bytes) -> None:
        super().__init__()
        self.payload = payload
        self.latest = "null"
        self.put_calls: list[dict[str, object]] = []
        self.put_error: Exception | None = None
        self.bucket_versioning_status = "Enabled"
        self.versioning_sequence: list[str] = []
        self.deny_explicit_null = False
        self.tags: list[dict[str, str]] = []
        self.hide_old_after_write = False
        self.drop_written_header: str | None = None
        self.drop_written_metadata_key: str | None = None
        self.versions["null"] = {
            "VersionId": "null", "ETag": '"legacy-etag"', "ContentLength": len(payload),
            "ContentType": "video/mp4", "Metadata": {"sha256": hashlib.sha256(payload).hexdigest()},
            "Payload": payload,
        }

    def get_object(self, **params: str):
        if self.deny_explicit_null and params.get("VersionId") == "null":
            raise _ClientError(403, "AccessDenied")
        if self.hide_old_after_write and self.latest != "null" and params.get("VersionId") == "null":
            raise _ClientError(404, "NoSuchVersion")
        return super().get_object(**params)

    def put_object(self, **params: object):
        self.put_calls.append(params)
        if self.put_error is not None:
            raise self.put_error
        if params["IfMatch"] != self.versions[self.latest]["ETag"]:
            raise _ClientError(412, "PreconditionFailed")
        version = "concrete-version-one"
        payload = params["Body"]
        assert isinstance(payload, bytes)
        self.versions[version] = {
            "VersionId": version, "ETag": '"new-etag"', "ContentLength": len(payload),
            "ContentType": params["ContentType"], "Metadata": dict(params["Metadata"]),
            "Payload": payload,
        }
        for name in ("CacheControl", "ContentDisposition", "ContentEncoding", "ContentLanguage", "Expires"):
            if name in params and name != self.drop_written_header:
                self.versions[version][name] = params[name]
        if self.drop_written_metadata_key is not None:
            self.versions[version]["Metadata"].pop(self.drop_written_metadata_key, None)
        self.latest = version
        return {"VersionId": version}

    def get_bucket_versioning(self, *, Bucket: str):
        status = self.versioning_sequence.pop(0) if self.versioning_sequence else self.bucket_versioning_status
        return {"Status": status}

    def head_object(self, *, Bucket: str, Key: str):
        return {name: value for name, value in self.versions[self.latest].items() if name != "Payload"}

    def get_object_tagging(self, *, Bucket: str, Key: str, VersionId: str):
        return {"TagSet": list(self.tags)}


def _legacy_promotion_object() -> tuple[StoredMediaObject, _LegacyPromotionFakeS3, PrivateS3MediaStorage]:
    payload = b"synthetic legacy video"
    sha = hashlib.sha256(payload).hexdigest()
    stored = StoredMediaObject(PrivateS3MediaStorage.object_key(sha), len(payload), sha, "video/mp4")
    client = _LegacyPromotionFakeS3(payload)
    return stored, client, PrivateS3MediaStorage(_config(), client=client)


def test_legacy_promotion_conditionally_writes_identical_bytes_and_preserves_null_version() -> None:
    stored, client, store = _legacy_promotion_object()
    version = store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert version == "concrete-version-one"
    assert len(client.put_calls) == 1
    assert client.put_calls[0] == {
        "Bucket": store.bucket, "Key": stored.key, "Body": client.payload,
        "ContentType": "video/mp4", "Metadata": {"sha256": stored.sha256},
        "IfMatch": '"legacy-etag"',
    }
    assert client.versions["null"]["Payload"] == client.payload
    assert client.latest == version
    assert store.verified_version(stored, version_id=version) == version
    with pytest.raises(MediaStorageIntegrityError, match="identity changed"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert len(client.put_calls) == 1


def test_legacy_promotion_verifies_preserved_headers_and_full_metadata() -> None:
    stored, client, store = _legacy_promotion_object()
    client.versions["null"]["CacheControl"] = "private, max-age=60"
    client.versions["null"]["ContentDisposition"] = 'attachment; filename="review.mp4"'
    client.versions["null"]["Metadata"]["source"] = "reviewed"
    assert store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"') == "concrete-version-one"
    assert client.put_calls[0]["CacheControl"] == "private, max-age=60"
    assert client.put_calls[0]["ContentDisposition"] == 'attachment; filename="review.mp4"'
    assert client.versions["concrete-version-one"]["Metadata"]["source"] == "reviewed"

    stored, client, store = _legacy_promotion_object()
    client.versions["null"]["CacheControl"] = "private, max-age=60"
    client.drop_written_header = "CacheControl"
    with pytest.raises(MediaStoragePromotionVerificationError) as caught:
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert caught.value.version_id == "concrete-version-one"
    assert len(client.put_calls) == 1

    stored, client, store = _legacy_promotion_object()
    client.versions["null"]["Metadata"]["source"] = "reviewed"
    client.drop_written_metadata_key = "source"
    with pytest.raises(MediaStoragePromotionVerificationError) as caught:
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert caught.value.version_id == "concrete-version-one"
    assert len(client.put_calls) == 1


def test_legacy_promotion_rejects_identity_drift_before_write() -> None:
    stored, client, store = _legacy_promotion_object()
    for etag in ('"wrong"', "", " quoted "):
        with pytest.raises((MediaStorageIntegrityError, ValueError)):
            store.promote_legacy_null_version(stored, expected_etag=etag)
    client.versions["null"]["Payload"] = b"corrupted legacy bytes"
    with pytest.raises(MediaStorageIntegrityError, match="bytes changed"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []


def test_legacy_promotion_rejects_missing_sha_metadata_before_write() -> None:
    stored, client, store = _legacy_promotion_object()
    client.versions["null"]["Metadata"] = {}
    with pytest.raises(MediaStorageIntegrityError, match="content-addressed"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []


def test_legacy_promotion_rejects_tags_or_unpreserved_attributes_before_write() -> None:
    stored, client, store = _legacy_promotion_object()
    client.tags = [{"Key": "retention", "Value": "keep"}]
    with pytest.raises(MediaStorageIntegrityError, match="tags"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []
    client.tags = []
    client.versions["null"]["ServerSideEncryption"] = "aws:kms"
    with pytest.raises(MediaStorageIntegrityError, match="unsupported object attributes"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []


def test_legacy_promotion_requires_readable_explicit_null_version_before_write() -> None:
    stored, client, store = _legacy_promotion_object()
    client.deny_explicit_null = True
    with pytest.raises(MediaStorageError, match="preflight"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []


def test_legacy_promotion_refuses_suspended_bucket_before_write() -> None:
    stored, client, store = _legacy_promotion_object()
    client.bucket_versioning_status = "Suspended"
    with pytest.raises(MediaStorageError, match="versioning must be enabled"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []
    client.bucket_versioning_status = "Enabled"
    client.versioning_sequence = ["Enabled", "Suspended"]
    with pytest.raises(MediaStorageError, match="versioning changed"):
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert client.put_calls == []


def test_legacy_promotion_does_not_retry_unknown_write_or_post_write_failure() -> None:
    stored, client, store = _legacy_promotion_object()
    client.put_error = RuntimeError(SYNTHETIC_SECRET)
    with pytest.raises(MediaStorageError, match="outcome unknown") as caught:
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert SYNTHETIC_SECRET not in str(caught.value)
    assert len(client.put_calls) == 1

    stored, client, store = _legacy_promotion_object()
    client.hide_old_after_write = True
    with pytest.raises(MediaStoragePromotionVerificationError) as caught:
        store.promote_legacy_null_version(stored, expected_etag='"legacy-etag"')
    assert caught.value.version_id == "concrete-version-one"
    assert len(client.put_calls) == 1
