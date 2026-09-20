import importlib.util
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


SCRIPT = Path(__file__).parents[1] / "windmill/f/content_research/collectors/ingest_video_media.py"
spec = importlib.util.spec_from_file_location("ingest_video_media", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _configuration(tmp_path):
    return dict(endpoint="http://minio.internal:9000", public_endpoint="https://media.example.test",
        bucket="test-media", region="us-east-1", access_key_id="synthetic-access",
        secret_access_key="synthetic-secret", force_path_style=True, use_ssl=False,
        storage_location="research-media-v1", temp_directory=str(tmp_path),
        allowed_download_hosts=["cdn.example.test"], ffmpeg_binary="ffmpeg")


def _variables(monkeypatch, data):
    monkeypatch.setattr(module, "_variable", lambda path: (
        json.dumps(data) if path == module.CONFIG_PATH else "windmill:test-research:automation"))
    monkeypatch.setattr(module.shutil, "which", lambda value: "/usr/bin/ffmpeg")


def test_only_video_identity_is_a_public_argument():
    assert list(inspect.signature(module.main).parameters) == ["video_id"]
    with pytest.raises(TypeError):
        module.main(video_id=str(uuid4()), db={"host": "attacker.test"})
    assert "WM_END_USER_EMAIL" not in SCRIPT.read_text()
    assert "fd2694729c784a0f2e34b2c0a4d6746da57813fe" in SCRIPT.read_text()


def test_invalid_video_is_rejected_before_loading_secrets(monkeypatch):
    monkeypatch.setattr(module, "_configuration", lambda: pytest.fail("secret read"))
    with pytest.raises(ValueError):
        module.main("not-a-uuid")


def test_database_resource_path_is_fixed(monkeypatch):
    seen = []
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_resource=lambda path: seen.append(path) or {}))
    assert module._database_resource() == {}
    assert seen == ["f/content_research/research_db"]


def test_malformed_database_configuration_is_redacted(monkeypatch):
    monkeypatch.setattr(module, "_database_resource", lambda: {"host": "localhost", "port": "synthetic-secret"})
    with pytest.raises(RuntimeError, match="configuration unavailable") as caught:
        module.main(str(uuid4()))
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("temp_directory", "relative"), ("allowed_download_hosts", "cdn.example.test"),
    ("max_download_bytes", True), ("use_ssl", "false"), ("public_endpoint", "http://public.test"),
])
def test_invalid_server_configuration_is_safe(tmp_path, monkeypatch, field, value):
    data = _configuration(tmp_path)
    data[field] = value
    _variables(monkeypatch, data)
    with pytest.raises(RuntimeError) as caught:
        module._configuration()
    assert "synthetic-secret" not in str(caught.value)
    assert "public.test" not in str(caught.value)


def test_missing_ffmpeg_does_not_construct_storage(tmp_path, monkeypatch):
    _variables(monkeypatch, _configuration(tmp_path))
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="not ready"):
        module._configuration()


def test_worker_uses_only_server_identity_and_returns_service_result(tmp_path, monkeypatch):
    _variables(monkeypatch, _configuration(tmp_path))
    seen = {}
    monkeypatch.setattr(module, "_preflight", lambda *args: None)
    monkeypatch.setattr(module, "_database_resource", lambda: dict(host="localhost", user="test", password="synthetic-db-password", dbname="test"))
    monkeypatch.setattr(module, "PrivateS3MediaStorage", lambda config: "storage-instance")

    def service(dsn, storage, config):
        seen.update(storage=storage, actor=config.worker_identity)
        return SimpleNamespace(run=lambda identifier: {"status": "completed", "video_id": str(identifier)})

    monkeypatch.setattr(module, "MediaIngestionService", service)
    identifier = uuid4()
    result = module.main(str(identifier))
    assert result == {"status": "completed", "video_id": str(identifier)}
    assert seen["actor"] == "windmill:test-research:automation/media"
    assert "synthetic" not in repr(result)


def test_valid_uuid_preflight_failure_does_not_read_media_secrets(monkeypatch):
    monkeypatch.setattr(module, "_database_resource", lambda: dict(host="localhost", user="test", password="test", dbname="test"))
    def rejected(*args):
        raise ValueError("an existing Douyin video is required")
    monkeypatch.setattr(module, "_preflight", rejected)
    monkeypatch.setattr(module, "_configuration", lambda: pytest.fail("media secret read"))
    with pytest.raises(ValueError, match="existing Douyin"):
        module.main(str(uuid4()))


def test_persisted_failure_becomes_a_failed_windmill_job(tmp_path, monkeypatch):
    _variables(monkeypatch, _configuration(tmp_path))
    monkeypatch.setattr(module, "_database_resource", lambda: dict(host="localhost", user="test", password="test", dbname="test"))
    monkeypatch.setattr(module, "_preflight", lambda *args: None)
    monkeypatch.setattr(module, "PrivateS3MediaStorage", lambda _: None)
    monkeypatch.setattr(module, "MediaIngestionService", lambda *args: SimpleNamespace(run=lambda _: {
        "status": "failed", "error_code": "private-url-must-not-leak"}))
    with pytest.raises(RuntimeError, match="inspect persisted") as caught:
        module.main(str(uuid4()))
    assert "private-url" not in str(caught.value)
