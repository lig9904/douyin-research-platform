from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "minio_recovery_drill",
    Path(__file__).resolve().parents[1] / "scripts/minio_object_recovery_drill.py",
)
assert SPEC and SPEC.loader
drill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = drill
SPEC.loader.exec_module(drill)


class ClientError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}


class Body:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.closed = False

    def iter_chunks(self, *, chunk_size: int):
        for start in range(0, len(self.value), chunk_size):
            yield self.value[start:start + chunk_size]

    def close(self):
        self.closed = True


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.deleted: list[str] = []
        self.operations: list[tuple[str, str]] = []
        self.fail_at: str | None = None
        self.fail_on_delete_number: int | None = None
        self.delete_attempts = 0

    def _maybe_fail(self, name: str):
        if self.fail_at == name:
            raise ClientError(500, "SyntheticFailure")

    def head_object(self, *, Bucket: str, Key: str):
        self._maybe_fail("head")
        self.operations.append(("head", Key))
        if Key not in self.objects:
            raise ClientError(404, "NoSuchKey")
        object_ = self.objects[Key]
        return {key: object_[key] for key in ("ContentLength", "ContentType", "Metadata")}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentLength: int, ContentType: str, Metadata: dict[str, str]):
        self._maybe_fail("put")
        self.operations.append(("put", Key))
        self.objects[Key] = {"Body": Body, "ContentLength": ContentLength, "ContentType": ContentType, "Metadata": dict(Metadata)}

    def get_object(self, *, Bucket: str, Key: str):
        self._maybe_fail("get")
        self.operations.append(("get", Key))
        if Key not in self.objects:
            raise ClientError(404, "NoSuchKey")
        return {"Body": Body(self.objects[Key]["Body"])}

    def copy_object(self, *, Bucket: str, Key: str, CopySource: dict[str, str], MetadataDirective: str):
        self._maybe_fail("copy")
        self.operations.append(("copy", Key))
        source = CopySource["Key"]
        if source not in self.objects:
            raise ClientError(404, "NoSuchKey")
        copied = self.objects[source]
        self.objects[Key] = {"Body": copied["Body"], "ContentLength": copied["ContentLength"], "ContentType": copied["ContentType"], "Metadata": dict(copied["Metadata"])}

    def delete_object(self, *, Bucket: str, Key: str):
        self.delete_attempts += 1
        if self.fail_on_delete_number == self.delete_attempts:
            raise ClientError(500, "SyntheticDeleteFailure")
        self._maybe_fail("delete")
        self.operations.append(("delete", Key))
        self.deleted.append(Key)
        self.objects.pop(Key, None)


def test_success_only_uses_two_recovery_drill_keys_and_cleans_after_restore() -> None:
    client = FakeS3()
    payload = b"synthetic recovery payload"
    objects = drill.run_drill(client, bucket="private-media", payload=payload, run_id="a" * 32)
    assert client.objects == {}
    assert objects.target == "recovery-drill/" + "a" * 32 + "/target.bin"
    assert objects.snapshot == "recovery-drill/" + "a" * 32 + "/snapshot.bin"
    assert objects.sha256 == hashlib.sha256(payload).hexdigest()
    assert client.deleted == [objects.target, objects.target, objects.snapshot]
    assert {key for _operation, key in client.operations} == {objects.target, objects.snapshot}
    assert ("copy", objects.snapshot) in client.operations
    assert ("copy", objects.target) in client.operations


@pytest.mark.parametrize("failure", ["put", "copy", "delete", "get"])
def test_failure_keeps_existing_drill_objects_for_investigation(failure: str) -> None:
    client = FakeS3()
    client.fail_at = failure
    with pytest.raises(drill.DrillError):
        drill.run_drill(client, bucket="private-media", payload=b"failure payload", run_id="b" * 32)
    # Any successful creation is retained; no broad or implicit cleanup occurs.
    assert all(key.startswith("recovery-drill/" + "b" * 32 + "/") for key in client.objects)
    assert len(set(key for _operation, key in client.operations)) <= 2


def test_preexisting_target_refuses_before_any_mutation() -> None:
    client = FakeS3()
    key = "recovery-drill/" + "c" * 32 + "/target.bin"
    client.objects[key] = {"Body": b"old", "ContentLength": 3, "ContentType": drill.CONTENT_TYPE, "Metadata": {}}
    with pytest.raises(drill.DrillError, match="target_already_exists"):
        drill.run_drill(client, bucket="private-media", payload=b"new", run_id="c" * 32)
    assert not any(operation in {"put", "copy", "delete"} for operation, _key in client.operations)
    assert client.objects[key]["Body"] == b"old"


def test_metadata_or_stream_corruption_stops_and_preserves_objects() -> None:
    client = FakeS3()
    original = client.copy_object

    def corrupt(*, Bucket, Key, CopySource, MetadataDirective):
        original(Bucket=Bucket, Key=Key, CopySource=CopySource, MetadataDirective=MetadataDirective)
        if Key.endswith("snapshot.bin"):
            client.objects[Key]["Body"] = b"corrupt"

    client.copy_object = corrupt  # type: ignore[method-assign]
    with pytest.raises(drill.DrillError, match="object_stream_integrity_mismatch"):
        drill.run_drill(client, bucket="private-media", payload=b"payload", run_id="d" * 32)
    assert any(key.endswith("target.bin") for key in client.objects)
    assert any(key.endswith("snapshot.bin") for key in client.objects)


def test_second_cleanup_delete_failure_reports_partial_known_state() -> None:
    client = FakeS3()
    # Delete 1 is the simulated object-loss step; delete 2 removes the restored
    # target during cleanup; delete 3 is the snapshot cleanup we fail.
    client.fail_on_delete_number = 3
    with pytest.raises(drill.DrillError, match="object_delete_failed") as caught:
        drill.run_drill(client, bucket="private-media", payload=b"payload", run_id="e" * 32)
    assert caught.value.cleanup == "partial"
    assert caught.value.object_states == {"target": "absent", "snapshot": "present"}
    assert "recovery-drill/" + "e" * 32 + "/target.bin" not in client.objects
    assert "recovery-drill/" + "e" * 32 + "/snapshot.bin" in client.objects


def test_credentials_are_required_private_json_and_not_embedded_in_errors(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"accessKey": "access-secret", "secretKey": "actual-secret"}))
    credentials.chmod(0o600)
    assert drill.load_credentials(credentials) == ("access-secret", "actual-secret")
    credentials.chmod(0o644)
    with pytest.raises(drill.DrillError, match="permissions") as caught:
        drill.load_credentials(credentials)
    assert "actual-secret" not in str(caught.value)


def test_credentials_reject_symlink_even_when_target_is_private(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"accessKey": "access", "secretKey": "secret"}))
    credentials.chmod(0o600)
    link = tmp_path / "credentials-link.json"
    link.symlink_to(credentials)
    with pytest.raises(drill.DrillError, match="credentials_file_invalid"):
        drill.load_credentials(link)


def test_cli_requires_explicit_execute_before_loading_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setattr(drill, "load_credentials", lambda _: pytest.fail("credentials must not load"))
    assert drill.main(["--endpoint", "https://minio.example.test", "--bucket", "private", "--credentials-file", "x"]) == 2
    assert "execute_switch_required" in capsys.readouterr().err


def test_cli_hides_sdk_and_credential_failures(monkeypatch, capsys) -> None:
    monkeypatch.setattr(drill, "load_credentials", lambda _: ("private-access", "private-secret"))
    def fail(**_kwargs):
        raise RuntimeError("private-secret https://not-for-output.invalid")
    monkeypatch.setattr(drill, "_client", fail)
    result = drill.main([
        "--execute", "--endpoint", "https://minio.example.test", "--bucket", "private",
        "--credentials-file", "credentials.json",
    ])
    output = capsys.readouterr().err
    assert result == 1
    assert "client_initialization_failed" in output
    assert "private-secret" not in output
    assert "not-for-output" not in output


def test_cli_reports_partial_cleanup_object_states(monkeypatch, capsys) -> None:
    monkeypatch.setattr(drill, "load_credentials", lambda _: ("access", "secret"))
    monkeypatch.setattr(drill, "_client", lambda **_kwargs: object())

    def fail(*_args, **_kwargs):
        raise drill.DrillError(
            "object_delete_failed",
            cleanup="partial",
            object_states={"target": "absent", "snapshot": "present"},
        )

    monkeypatch.setattr(drill, "run_drill", fail)
    result = drill.main([
        "--execute", "--endpoint", "https://minio.example.test", "--bucket", "private",
        "--credentials-file", "credentials.json",
    ])
    output = json.loads(capsys.readouterr().err)
    assert result == 1
    assert output["cleanup"] == "partial"
    assert output["object_states"] == {"target": "absent", "snapshot": "present"}


@pytest.mark.parametrize("endpoint", ["http://s3.example.test", "ftp://s3.example.test", "https://user:pass@s3.example.test", "https://s3.example.test/path", "https://s3.example.test/?q=1"])
def test_endpoint_rejects_embedded_credentials_and_rewrites(endpoint: str) -> None:
    with pytest.raises(drill.DrillError, match="endpoint_invalid"):
        drill._validate_endpoint(endpoint)
