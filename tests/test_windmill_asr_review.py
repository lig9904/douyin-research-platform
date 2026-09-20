import importlib.util
import inspect
import sys
import json
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

PATH = Path(__file__).parents[1] / "windmill/f/content_research/research_dashboard.raw_app/backend/asr_media_review.py"
SPEC = importlib.util.spec_from_file_location("asr_review_backend", PATH)
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


def test_review_api_has_no_caller_identity_or_database_fields():
    assert not {"db", "actor", "reviewer_allowlist", "delivery_origin"} & set(inspect.signature(backend.main).parameters)


def test_backend_dependency_lock_matches_source_revision_and_runtime():
    lock = PATH.with_suffix(".lock").read_text()
    source = PATH.read_text()
    revision = "92153f603368a3ab2cb7810924d6d3948857987a"
    assert revision in lock and revision in source
    assert "# py: 3.13" in lock
    assert "wmill==1.815.0" in lock
    assert "boto3==" in lock and "psycopg-binary==3.3.6" in lock


def test_approve_requires_preview_before_configuration(monkeypatch):
    monkeypatch.setattr(backend, "_dsn", lambda: pytest.fail("configuration read"))
    with pytest.raises(ValueError, match="fingerprint"):
        backend.main(str(uuid4()), str(uuid4()), "v1", "approve")


@pytest.mark.parametrize("action", ["preview", "approve"])
def test_backend_routes_only_review_actions(monkeypatch, action):
    monkeypatch.setattr(backend, "_dsn", lambda: "test")
    seen = []
    monkeypatch.setattr(backend, "prepare_asr_media", lambda *args, **kwargs: seen.append("preview") or {"status": "preview"})
    monkeypatch.setattr(backend, "approve_asr_media", lambda *args, **kwargs: seen.append("approve") or {"status": "approved"})
    backend.main(str(uuid4()), str(uuid4()), "v1", action, "a" * 64)
    assert seen == [action]


def test_backend_redacts_underlying_error(monkeypatch):
    def failure():
        raise RuntimeError("private-password")
    monkeypatch.setattr(backend, "_dsn", failure)
    with pytest.raises(RuntimeError) as caught:
        backend.main(str(uuid4()), str(uuid4()), "v1")
    assert "private-password" not in str(caught.value)


def test_list_does_not_require_manual_asset_id_or_approve(monkeypatch):
    monkeypatch.setattr(backend, "_dsn", lambda: "test")
    monkeypatch.setattr(backend, "_list_assets", lambda dsn, video, version: {"assets": [], "db_writes": 0})
    monkeypatch.setattr(backend, "approve_asr_media", lambda *args, **kwargs: pytest.fail("list cannot approve"))
    result = backend.main(str(uuid4()), action="list")
    assert result == {"assets": [], "db_writes": 0}


def test_playback_auth_precedes_storage_configuration(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("not a reviewer")
    monkeypatch.setattr(backend, "prepare_asr_media", denied)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=lambda _: pytest.fail("secret read")))
    with pytest.raises(PermissionError):
        backend._playback("test", uuid4(), uuid4(), "v1")


def test_playback_verifies_asset_before_short_lived_signing(monkeypatch):
    monkeypatch.setattr(backend, "prepare_asr_media", lambda *args, **kwargs: {"asset_id": "test"})
    reference = SimpleNamespace(bucket="private", storage_location="test", object_key="key",
                                content_sha256="a" * 64, size_bytes=100, content_type="audio/wav")
    monkeypatch.setattr(backend, "MediaAssetStore", lambda _: SimpleNamespace(get=lambda *args: reference))
    fields = {key: "synthetic" for key in ("endpoint", "public_endpoint", "bucket", "region", "access_key_id", "secret_access_key", "force_path_style", "use_ssl")}
    fields["storage_location"] = "test"
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=lambda _: json.dumps(fields)))
    monkeypatch.setattr(backend, "S3MediaStorageConfig", lambda **kwargs: kwargs)
    calls = []
    class Storage:
        bucket = "private"
        def verify_object(self, obj):
            calls.append("verify")
        def presigned_read_url(self, key, expires_in):
            assert calls == ["verify"] and expires_in == 300
            return "https://media.example.test/synthetic"
    monkeypatch.setattr(backend, "PrivateS3MediaStorage", lambda _: Storage())
    result = backend._playback("test", uuid4(), uuid4(), "v1")
    assert result["expires_in_seconds"] == 300
