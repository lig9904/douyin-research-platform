import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


PATH = Path(__file__).parents[1] / (
    "windmill/f/content_research/research_dashboard.raw_app/backend/video_media_preview.py"
)
SPEC = importlib.util.spec_from_file_location("video_media_preview_backend", PATH)
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


def asset(**changes):
    digest = "a" * 64
    values = {
        "id": uuid4(),
        "video_id": uuid4(),
        "kind": "video",
        "storage_location": "research-media-v1",
        "bucket": "private-media",
        "object_key": backend.PrivateS3MediaStorage.object_key(digest),
        "content_sha256": digest,
        "size_bytes": 100,
        "content_type": "video/mp4",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_preview_accepts_no_caller_config_or_asset_selection_fields():
    assert tuple(backend.main.__annotations__) == ("video_id",)
    assert backend.main.__annotations__["video_id"] == "str"


def test_preview_reuses_the_fixed_asr_media_review_runtime_lock():
    backend_dir = PATH.parent
    assert (backend_dir / "video_media_preview.lock").read_text() == (
        backend_dir / "asr_media_review.lock"
    ).read_text()
    assert "92153f603368a3ab2cb7810924d6d3948857987a" in PATH.read_text()
    assert (backend_dir / "video_media_preview.yaml").read_text() == "type: inline\nfields: {}\n"


def test_denied_reviewer_cannot_read_database_or_storage_configuration(monkeypatch):
    def denied():
        raise PermissionError("not allowed")

    monkeypatch.setattr(backend, "_authorize", denied)
    monkeypatch.setattr(backend, "_dsn", lambda: pytest.fail("database resource read"))
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda _: pytest.fail("database resource read"),
        get_variable=lambda _: pytest.fail("storage secret read"),
    ))
    with pytest.raises(PermissionError, match="authenticated media reviewer"):
        backend.main(str(uuid4()))


def test_no_video_asset_does_not_load_storage_secret(monkeypatch):
    monkeypatch.setattr(backend, "_authorize", lambda: None)
    monkeypatch.setattr(backend, "_dsn", lambda: "read-only-dsn")
    monkeypatch.setattr(backend, "_load_single_video_asset", lambda *_: None)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda _: pytest.fail("no-asset path must not read storage secret"),
    ))
    assert backend.main(str(uuid4())) == {"has_media": False}


@pytest.mark.parametrize("identity", [None, "other@example.test", "REVIEWER@example.test"])
def test_real_authorization_denies_identity_before_resource_read(monkeypatch, identity):
    if identity is None:
        monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    else:
        monkeypatch.setenv("WM_END_USER_EMAIL", identity)
    def variable(path):
        assert path == backend._REVIEWERS_PATH
        return "reviewer@example.test"
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=variable,
        get_resource=lambda _: pytest.fail("unauthorized resource lookup"),
    ))
    with pytest.raises(PermissionError, match="authenticated media reviewer"):
        backend.main(str(uuid4()))


def test_single_asset_binding_is_checked_after_limit_two_query(monkeypatch):
    video = uuid4()
    row_id = uuid4()
    statements = []

    class Cursor:
        def fetchall(self):
            return [(row_id,)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, statement, *args):
            statements.append(statement)
            return Cursor()

    monkeypatch.setattr(backend.psycopg, "connect", lambda _: Connection())
    wrong_video = asset(video_id=uuid4())
    monkeypatch.setattr(backend, "MediaAssetStore", lambda _: SimpleNamespace(
        get=lambda *_: wrong_video,
    ))
    with pytest.raises(ValueError, match="binding"):
        backend._load_single_video_asset("read-only-dsn", video)
    assert any("kind='video'" in statement and "limit 2" in statement for statement in statements)
    assert statements[0].lower() == "set transaction read only"


@pytest.mark.parametrize("changes", [
    {"kind": "audio"},
    {"content_sha256": "A" * 64},
    {"object_key": "sha256/aa/not-the-content-hash"},
    {"size_bytes": 0},
    {"content_type": "audio/wav"},
])
def test_invalid_kind_or_metadata_is_rejected_before_storage_secret(monkeypatch, changes):
    reference = asset(**changes)
    if "content_sha256" in changes:
        reference.object_key = backend.PrivateS3MediaStorage.object_key(reference.content_sha256)
    monkeypatch.setattr(backend, "_authorize", lambda: None)
    monkeypatch.setattr(backend, "_dsn", lambda: "read-only-dsn")
    monkeypatch.setattr(backend, "_load_single_video_asset", lambda *_: reference)
    monkeypatch.setattr(backend, "_storage_for", lambda _: pytest.fail("invalid asset must not read storage"))
    with pytest.raises(RuntimeError, match="video preview unavailable"):
        backend.main(str(reference.video_id))


def test_ambiguous_video_assets_are_rejected(monkeypatch):
    video = uuid4()
    class Cursor:
        def fetchall(self):
            return [(uuid4(),), (uuid4(),)]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, *_): return Cursor()
    monkeypatch.setattr(backend.psycopg, "connect", lambda _: Connection())
    monkeypatch.setattr(backend, "MediaAssetStore", lambda _: pytest.fail("ambiguous assets cannot bind"))
    with pytest.raises(ValueError, match="multiple"):
        backend._load_single_video_asset("read-only-dsn", video)


@pytest.mark.parametrize("field,value", [
    ("bucket", "wrong-bucket"),
    ("storage_location", "wrong-location"),
])
def test_storage_config_must_match_persisted_bucket_and_location(monkeypatch, field, value):
    reference = asset()
    config = {key: "synthetic" for key in backend._STORAGE_FIELDS}
    config.update({"force_path_style": True, "use_ssl": True, "storage_location": reference.storage_location})
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda _: json.dumps(config),
    ))
    monkeypatch.setattr(backend, "S3MediaStorageConfig", lambda **kwargs: kwargs)
    storage = SimpleNamespace(bucket=reference.bucket)
    if field == "bucket":
        storage.bucket = value
    else:
        config["storage_location"] = value
        monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
            get_variable=lambda _: json.dumps(config),
        ))
    monkeypatch.setattr(backend, "PrivateS3MediaStorage", lambda _: storage)
    with pytest.raises(RuntimeError, match="storage configuration unavailable"):
        backend._storage_for(reference)


def test_object_is_verified_before_exactly_300_second_https_signing(monkeypatch):
    reference = asset()
    calls = []
    class Storage:
        def verify_object(self, stored):
            calls.append(("verify", stored))
        def presigned_read_url(self, key, *, expires_in):
            assert calls and calls[0][0] == "verify"
            assert key == reference.object_key and expires_in == 300
            calls.append(("sign", key))
            return "https://media.example.test/private-preview?signature=redacted"

    monkeypatch.setattr(backend, "_authorize", lambda: None)
    monkeypatch.setattr(backend, "_dsn", lambda: "read-only-dsn")
    monkeypatch.setattr(backend, "_load_single_video_asset", lambda *_: reference)
    monkeypatch.setattr(backend, "_storage_for", lambda _: Storage())
    assert backend.main(str(reference.video_id)) == {
        "has_media": True,
        "playback_url": "https://media.example.test/private-preview?signature=redacted",
        "expires_in_seconds": 300,
    }
    assert [call[0] for call in calls] == ["verify", "sign"]


def test_verify_failure_does_not_sign_or_disclose_provider_error(monkeypatch):
    reference = asset()
    class Storage:
        def verify_object(self, _):
            raise RuntimeError("secret-access-key=do-not-disclose")
        def presigned_read_url(self, *_args, **_kwargs):
            pytest.fail("signing must not follow a failed object verification")

    monkeypatch.setattr(backend, "_authorize", lambda: None)
    monkeypatch.setattr(backend, "_dsn", lambda: "read-only-dsn")
    monkeypatch.setattr(backend, "_load_single_video_asset", lambda *_: reference)
    monkeypatch.setattr(backend, "_storage_for", lambda _: Storage())
    with pytest.raises(RuntimeError) as caught:
        backend.main(str(reference.video_id))
    assert "secret-access-key" not in str(caught.value)
    assert "video preview unavailable" in str(caught.value)


def test_non_https_signed_url_is_rejected_and_not_returned(monkeypatch):
    reference = asset()
    class Storage:
        def verify_object(self, _): pass
        def presigned_read_url(self, *_args, **_kwargs): return "http://private.example.test/object"

    monkeypatch.setattr(backend, "_authorize", lambda: None)
    monkeypatch.setattr(backend, "_dsn", lambda: "read-only-dsn")
    monkeypatch.setattr(backend, "_load_single_video_asset", lambda *_: reference)
    monkeypatch.setattr(backend, "_storage_for", lambda _: Storage())
    with pytest.raises(RuntimeError, match="video preview unavailable"):
        backend.main(str(reference.video_id))
