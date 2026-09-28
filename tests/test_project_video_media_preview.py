"""The project playback boundary must never fall back to global reviewer access."""

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


PATH = Path(__file__).parents[1] / (
    "windmill/f/content_research/research_dashboard.raw_app/backend/"
    "project_video_media_preview.py"
)
SPEC = importlib.util.spec_from_file_location("project_video_media_preview_backend", PATH)
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


def asset(video, **changes):
    digest = "a" * 64
    values = dict(
        id=uuid4(), video_id=video, kind="video",
        storage_location="research-media-v1", bucket="private-media",
        object_key=backend.PrivateS3MediaStorage.object_key(digest),
        content_sha256=digest, size_bytes=100, content_type="video/mp4",
        source_response_id=123, parent_asset_id=None,
    )
    values.update(changes)
    return backend.MediaAssetReference(**values)


def test_contract_only_accepts_project_and_video_with_fixed_database_resource():
    assert tuple(backend.main.__annotations__) == ("db", "project_id", "video_id", "return")
    assert "value: $res:f/content_research/research_db" in PATH.with_suffix(".yaml").read_text()
    assert PATH.with_suffix(".lock").read_text().strip() == (
        PATH.with_name("video_media_preview.lock").read_text().strip()
    )
    source = PATH.read_text()
    assert "project_actor_can_read(project_row.id,%s)" in source
    assert "inclusion_row.status in ('candidate','shortlisted','accepted')" in source
    assert "video_row.availability_status='available'" in source
    assert "project_video_share" not in source


def test_missing_actor_stops_before_database_and_secret(monkeypatch):
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    monkeypatch.setattr(backend, "_dsn", lambda _: pytest.fail("database read"))
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda _: pytest.fail("storage secret read"),
    ))
    with pytest.raises(RuntimeError, match="PROJECT_VIDEO_PREVIEW_UNAVAILABLE"):
        backend.main({}, str(uuid4()), str(uuid4()))


def test_acl_denial_does_not_read_asset_or_storage(monkeypatch):
    project, video = uuid4(), uuid4()
    queries = []

    class Cursor:
        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, sql, params=None):
            queries.append((sql, params))
            if "media_asset" in sql:
                pytest.fail("denied actor reached media asset")
            return Cursor()

    monkeypatch.setattr(backend.psycopg, "connect", lambda *_, **__: Connection())
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda _: pytest.fail("storage secret read"),
    ))
    with pytest.raises(PermissionError, match="PROJECT_VIDEO_PREVIEW_UNAVAILABLE"):
        backend._asset("dsn", project, video, "viewer@example.test")
    assert queries[0][0].lower() == "set transaction isolation level repeatable read read only"
    assert queries[1][1] == (project, video, "viewer@example.test")


def test_authorized_missing_media_does_not_read_storage_secret(monkeypatch):
    monkeypatch.setenv("WM_END_USER_EMAIL", "VIEWER@example.test")
    monkeypatch.setattr(backend, "_dsn", lambda _: "dsn")
    monkeypatch.setattr(backend, "_asset", lambda *args: None)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda _: pytest.fail("storage secret read"),
    ))
    assert backend.main({}, str(uuid4()), str(uuid4())) == {"has_media": False}


@pytest.mark.parametrize("changes", [
    {"video_id": uuid4()}, {"kind": "audio"},
    {"content_sha256": "A" * 64}, {"object_key": "wrong"},
    {"size_bytes": 0}, {"content_type": "audio/wav"},
])
def test_bad_asset_fails_before_storage_secret(monkeypatch, changes):
    video = uuid4()
    monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.test")
    monkeypatch.setattr(backend, "_dsn", lambda _: "dsn")
    monkeypatch.setattr(backend, "_asset", lambda *args: asset(video, **changes))
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda _: pytest.fail("storage secret read"),
    ))
    with pytest.raises(RuntimeError, match="PROJECT_VIDEO_PREVIEW_UNAVAILABLE"):
        backend.main({}, str(uuid4()), str(video))


def test_verified_private_object_gets_only_short_https_url(monkeypatch):
    video = uuid4()
    reference = asset(video)
    calls = []
    storage = SimpleNamespace(
        verify_object=lambda obj: calls.append(("verify", obj.key)),
        presigned_read_url=lambda key, *, expires_in: (
            calls.append(("sign", key, expires_in)) or
            "https://minio.example.test/private?X-Amz-Signature=opaque"
        ),
    )
    monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.test")
    monkeypatch.setattr(backend, "_dsn", lambda _: "dsn")
    monkeypatch.setattr(backend, "_asset", lambda *args: reference)
    monkeypatch.setattr(backend, "_storage", lambda _: storage)
    result = backend.main({}, str(uuid4()), str(video))
    assert result == {
        "has_media": True,
        "playback_url": "https://minio.example.test/private?X-Amz-Signature=opaque",
        "expires_in_seconds": 300,
    }
    assert calls == [("verify", reference.object_key), ("sign", reference.object_key, 300)]
    assert "bucket" not in result and "object_key" not in result


@pytest.mark.parametrize("url", ["http://minio.test/object", "https://user:pass@host/a", "https://host/a#part"])
def test_insecure_url_is_never_returned(monkeypatch, url):
    video = uuid4()
    monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.test")
    monkeypatch.setattr(backend, "_dsn", lambda _: "dsn")
    monkeypatch.setattr(backend, "_asset", lambda *args: asset(video))
    monkeypatch.setattr(backend, "_storage", lambda _: SimpleNamespace(
        verify_object=lambda _: None,
        presigned_read_url=lambda *_, **__: url,
    ))
    with pytest.raises(RuntimeError, match="PROJECT_VIDEO_PREVIEW_UNAVAILABLE"):
        backend.main({}, str(uuid4()), str(video))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required")
def test_postgres_project_media_acl_and_inclusion_statuses():
    dsn = os.environ["TEST_DATABASE_URL"]
    namespace = "project_media_preview_" + uuid4().hex
    root = PATH.parents[5]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute((root / "db/schema.sql").read_text(), prepare=False)
            for name in ("021_project_account_foundation", "022_project_task_ownership",
                         "023_project_video_read_acl"):
                admin.execute((root / "db/migrations" / f"{name}.sql").read_text(), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values('preview-org','预览') returning id"
            ).fetchone()[0]
            project_a = admin.execute(
                "insert into research_project(organization_id,slug,name,status) "
                "values(%s,'project-a','项目 A','active') returning id", (org,),
            ).fetchone()[0]
            project_b = admin.execute(
                "insert into research_project(organization_id,slug,name,status) "
                "values(%s,'project-b','项目 B','active') returning id", (org,),
            ).fetchone()[0]
            video = admin.execute(
                "insert into source_video(platform,platform_video_id,title,last_seen_at,availability_status) "
                "values('douyin','preview-video','公开样本',now(),'available') returning id"
            ).fetchone()[0]
            admin.execute(
                "insert into project_video_inclusion(project_id,video_id,source_type,status) "
                "values(%s,%s,'manual','candidate')", (project_a, video),
            )
            admin.execute(
                "insert into research_project_member(project_id,actor_id,role) "
                "values(%s,'viewer@example.test','viewer')", (project_a,),
            )
            digest = "a" * 64
            admin.execute(
                "insert into media_asset(video_id,kind,storage_location,bucket,object_key,"
                "content_sha256,size_bytes,content_type) values(%s,'video','research-media-v1',"
                "'private-media',%s,%s,100,'video/mp4')",
                (video, backend.PrivateS3MediaStorage.object_key(digest), digest),
            )
            isolated = make_conninfo(dsn, options=f"-c search_path={namespace}")
            assert backend._asset(isolated, project_a, video, "viewer@example.test").video_id == video
            with pytest.raises(PermissionError):
                backend._asset(isolated, project_b, video, "viewer@example.test")
            with pytest.raises(PermissionError):
                backend._asset(isolated, project_a, video, "outsider@example.test")
            for status in ("shortlisted", "accepted"):
                admin.execute(
                    "update project_video_inclusion set status=%s where project_id=%s and video_id=%s",
                    (status, project_a, video),
                )
                assert backend._asset(isolated, project_a, video, "viewer@example.test") is not None
            for status in ("rejected", "archived"):
                admin.execute(
                    "update project_video_inclusion set status=%s where project_id=%s and video_id=%s",
                    (status, project_a, video),
                )
                with pytest.raises(PermissionError):
                    backend._asset(isolated, project_a, video, "viewer@example.test")
            admin.execute(
                "update project_video_inclusion set status='candidate' where project_id=%s and video_id=%s",
                (project_a, video),
            )
            admin.execute("update research_project set status='paused' where id=%s", (project_a,))
            with pytest.raises(PermissionError):
                backend._asset(isolated, project_a, video, "viewer@example.test")
            admin.execute("update research_project set status='active' where id=%s", (project_a,))
            admin.execute("update research_organization set status='suspended' where id=%s", (org,))
            with pytest.raises(PermissionError):
                backend._asset(isolated, project_a, video, "viewer@example.test")
            admin.execute("update research_organization set status='active' where id=%s", (org,))
            admin.execute("update source_video set availability_status='unavailable' where id=%s", (video,))
            with pytest.raises(PermissionError):
                backend._asset(isolated, project_a, video, "viewer@example.test")
            admin.execute("update source_video set availability_status='available' where id=%s", (video,))
            admin.execute(
                "update research_project_member set status='revoked' "
                "where project_id=%s and actor_id='viewer@example.test'", (project_a,),
            )
            with pytest.raises(PermissionError):
                backend._asset(isolated, project_a, video, "viewer@example.test")
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
