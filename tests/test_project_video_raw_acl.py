from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/get_video_raw_records.py"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")
PROJECT_READ_FUNCTION_READY = "create or replace function project_video_can_read" in SCHEMA.read_text(encoding="utf-8")


def _load_backend():
    spec = importlib.util.spec_from_file_location("get_video_raw_records_project_acl_test", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _db() -> dict[str, object]:
    return {
        "host": "unused", "port": 5432, "user": "unused", "password": "unused",
        "dbname": "unused", "sslmode": "prefer",
    }


def test_raw_records_require_server_identity_and_preflight_acl() -> None:
    backend = _load_backend()
    source = BACKEND.read_text(encoding="utf-8")
    yaml = BACKEND.with_suffix(".yaml").read_text(encoding="utf-8")

    assert backend._legacy_reader_allowed("reader@example.com", "reader@example.com")
    assert not backend._legacy_reader_allowed("reader@example.com", "other@example.com")
    assert "legacy_reader_allowlist" not in yaml
    assert "actor:" not in yaml
    assert "project_video_can_read" in source
    assert source.index("project_video_can_read") < source.index("from source_video")
    assert "VIDEO_RAW_RECORDS_ACCESS_DENIED" in source
    assert "from discovery_event" in source
    assert "if normalized_project_id is None:" in source


@pytest.mark.skipif(not DSN or not PROJECT_READ_FUNCTION_READY, reason="TEST_DATABASE_URL and project_video_can_read are required")
def test_raw_records_isolate_projects_and_hide_known_video_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DSN
    backend = _load_backend()
    schema_name = f"raw_acl_{uuid4().hex}"
    schema_sql = SCHEMA.read_text(encoding="utf-8")
    actor_a = "a-owner@example.com"
    actor_b = "b-owner@example.com"
    real_connect = psycopg.connect

    with real_connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
            conn.execute(schema_sql, prepare=False)
            org_a = conn.execute(
                "insert into research_organization(slug,name) values ('raw-org-a','Raw Org A') returning id"
            ).fetchone()[0]
            org_b = conn.execute(
                "insert into research_organization(slug,name) values ('raw-org-b','Raw Org B') returning id"
            ).fetchone()[0]
            project_a = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'raw-project-a','Raw Project A','active') returning id""", (org_a,)
            ).fetchone()[0]
            project_b = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'raw-project-b','Raw Project B','active') returning id""", (org_b,)
            ).fetchone()[0]
            for project_id, actor in ((project_a, actor_a), (project_b, actor_b)):
                conn.execute(
                    """insert into research_project_member(project_id,actor_id,role,status)
                       values (%s,%s,'owner','active')""", (project_id, actor),
                )
            account = conn.execute(
                """insert into source_account(platform,platform_account_id,nickname)
                   values ('douyin','raw-account','原始账号') returning id"""
            ).fetchone()[0]
            video_a = conn.execute(
                """insert into source_video(platform,platform_video_id,account_id,title)
                   values ('douyin','raw-video-a',%s,'项目A视频') returning id""", (account,)
            ).fetchone()[0]
            video_b = conn.execute(
                """insert into source_video(platform,platform_video_id,account_id,title)
                   values ('douyin','raw-video-b',%s,'项目B视频') returning id""", (account,)
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type)
                   values (%s,%s,'manual')""", (project_a, video_a),
            )
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type)
                   values (%s,%s,'manual')""", (project_b, video_b),
            )
            conn.execute(
                """insert into project_video_inclusion(
                     project_id,video_id,source_type,source_ref,metadata
                   ) values (%s,%s,'manual','B-only-source-ref','{"owner":"B-only"}'::jsonb)""",
                (project_b, video_a),
            )
            conn.execute(
                """insert into discovery_event(video_id,provider,source_type,source_key,observation_key,metadata)
                   values (%s,'test','private','B-only-source-key','B-only-observation','{"owner":"B-only"}'::jsonb)""",
                (video_a,),
            )
            conn.execute(
                """insert into metric_snapshot(video_id,provider,source_endpoint,observation_key,play_count,raw_metrics)
                   values (%s,'B-private-provider','B-private-endpoint','B-only-metric-observation',7,
                           '{"owner":"B-only"}'::jsonb)""",
                (video_a,),
            )
            conn.execute(
                """insert into video_comment(
                     video_id,provider,source_endpoint,platform_comment_id,text_content,sample_reason,
                     observation_count,raw_payload
                   ) values (%s,'B-private-provider','B-private-endpoint','B-only-comment','公开评论',
                             'B-only-sample',9,'{"owner":"B-only"}'::jsonb)""",
                (video_a,),
            )

            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={schema_name}")
            monkeypatch.setattr(
                backend.psycopg, "connect",
                lambda *args, **kwargs: real_connect(scoped_dsn),
            )
            monkeypatch.setenv("WM_END_USER_EMAIL", actor_a)
            allowed = backend.main(_db(), video_id=str(video_a), project_id=str(project_a))
            assert allowed["canonical"]["title"] == "项目A视频"
            assert "research_level" not in allowed["canonical"]
            assert "monitoring_status" not in allowed["canonical"]
            assert "first_seen_at" not in allowed["canonical"]
            assert "account_id" not in allowed["canonical"]
            assert "discoveries" not in allowed
            assert "provider_snapshots" not in allowed
            assert "metric_snapshots" not in allowed
            assert allowed["project_inclusion"]["source_type"] == "manual"
            assert set(allowed["project_inclusion"]) == {
                "source_type", "status", "first_seen_at", "last_seen_at",
            }
            assert allowed["merged_metrics"] is not None
            assert "B-only-metric-observation" not in str(allowed)
            assert "B-only-source-key" not in str(allowed)
            assert "B-only-sample" not in str(allowed)
            assert "B-private-endpoint" not in str(allowed)
            assert "raw_payload" not in allowed["comments"][0]
            assert "sample_reason" not in allowed["comments"][0]
            for denied_video in (video_b, uuid4()):
                with pytest.raises(PermissionError, match="VIDEO_RAW_RECORDS_ACCESS_DENIED"):
                    backend.main(_db(), video_id=str(denied_video), project_id=str(project_a))

            conn.execute(
                "update project_video_inclusion set status='archived' where project_id=%s and video_id=%s",
                (project_a, video_a),
            )
            with pytest.raises(PermissionError, match="VIDEO_RAW_RECORDS_ACCESS_DENIED"):
                backend.main(_db(), video_id=str(video_a), project_id=str(project_a))

            monkeypatch.setenv("WM_END_USER_EMAIL", "unlisted@example.com")
            monkeypatch.setattr(backend, "_get_legacy_allowlist", lambda: actor_a)
            with pytest.raises(PermissionError, match="VIDEO_RAW_RECORDS_ACCESS_DENIED"):
                backend.main(_db(), video_id=str(video_b))
        finally:
            # Use the saved connector because the backend module monkeypatches
            # the shared psycopg module object for its isolated endpoint calls.
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema_name)))
