from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")


def _load(stem: str):
    spec = importlib.util.spec_from_file_location(f"{stem}_collaboration_test", BACKEND / f"{stem}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collaboration_backends_bind_only_research_db() -> None:
    for stem in ("get_project_collaboration", "mutate_project_collaboration"):
        assert (BACKEND / f"{stem}.yaml").read_text() == (
            "type: inline\nfields:\n  db:\n    type: static\n"
            "    value: $res:f/content_research/research_db\n"
        )
        source = (BACKEND / f"{stem}.py").read_text()
        assert "WM_END_USER_EMAIL" in source
        assert "credential_ref" not in source
        assert "raw_payload" not in source


def test_shared_video_requires_source_acceptance() -> None:
    migration = (ROOT / "db/migrations/026_share_only_accepted_video.sql").read_text()
    schema = (ROOT / "db/schema.sql").read_text()
    assert "inclusion_row.status = 'accepted'" in migration
    assert "inclusion_row.status = 'accepted'" in schema


def test_collaboration_page_is_project_only_and_shows_narrow_share_scope() -> None:
    app = (BACKEND.parent / "App.tsx").read_text()
    shell = (BACKEND.parent / "AppShell.tsx").read_text()
    page = (BACKEND.parent / "ProjectCollaboration.tsx").read_text()
    assert "view === 'collaboration' && scope.mode === 'project'" in app
    assert "projectMode || item.view !== 'collaboration'" in shell
    assert "backend.get_project_collaboration" in page
    assert "backend.mutate_project_collaboration" in page
    assert "审核正文、原始 Provider 响应、媒体、私有授权和费用" in page
    assert "data.project_id !== projectId" in page
    assert "version === requestVersion.current && currentProject.current === projectId" in page
    assert "待我方确认" in page
    assert "还没有可查看的共享依据" in page


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_collaboration_members_and_explicit_share(monkeypatch) -> None:
    assert DSN
    read = _load("get_project_collaboration")
    write = _load("mutate_project_collaboration")
    namespace = f"collaboration_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(
                """create extension if not exists pgcrypto;
                   create table source_account (
                     id uuid primary key default gen_random_uuid(),
                     platform text not null default 'douyin',
                     platform_account_id text not null default '',
                     nickname text, profile_url text, bio text, location_text text,
                     account_type text, certification_type text,
                     first_seen_at timestamptz not null default now(),
                     last_seen_at timestamptz not null default now());""",
                prepare=False,
            )
            conn.execute((ROOT / "db/migrations/021_project_account_foundation.sql").read_text(), prepare=False)
            conn.execute(
                """create table source_video (
                     id uuid primary key default gen_random_uuid(),
                     account_id uuid references source_account(id),
                     platform text not null default 'douyin', title text,
                     published_at timestamptz,
                     availability_status text not null default 'available');
                   create table project_video_inclusion (
                     project_id uuid references research_project(id),
                     video_id uuid references source_video(id),
                     status text not null default 'accepted',
                     last_seen_at timestamptz not null default now(),
                     primary key(project_id,video_id));
                   create view merged_video_metric as
                   select id as video_id, 100::bigint as play_count,
                     10::bigint as like_count, 2::bigint as comment_count,
                     now() as captured_at from source_video;""",
                prepare=False,
            )
            conn.execute((ROOT / "db/migrations/023_project_video_read_acl.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/025_project_collaboration.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/026_share_only_accepted_video.sql").read_text(), prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('shared-org','共享组织') returning id").fetchone()[0]
            other_org = conn.execute("insert into research_organization(slug,name) values('other-org','其它组织') returning id").fetchone()[0]
            source = conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,'source','来源','active') returning id", (org,)
            ).fetchone()[0]
            target = conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,'target','目标','active') returning id", (org,)
            ).fetchone()[0]
            foreign = conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,'foreign','跨组织','active') returning id", (other_org,)
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role) values
                   (%s,'a-owner@example.com','owner'),
                   (%s,'b-owner@example.com','owner'),
                   (%s,'b-viewer@example.com','viewer'),
                   (%s,'foreign-owner@example.com','owner')""",
                (source, target, target, foreign),
            )
            account = conn.execute("insert into source_account(nickname) values('公开账号') returning id").fetchone()[0]
            video = conn.execute("insert into source_video(account_id,title) values(%s,'公开视频') returning id", (account,)).fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id) values(%s,%s)", (source, video))

            db = {
                "host": "127.0.0.1", "port": psycopg.conninfo.conninfo_to_dict(DSN).get("port", 5432),
                "user": psycopg.conninfo.conninfo_to_dict(DSN).get("user", os.getenv("USER")),
                "password": psycopg.conninfo.conninfo_to_dict(DSN).get("password", ""),
                "dbname": psycopg.conninfo.conninfo_to_dict(DSN)["dbname"],
                "sslmode": "prefer", "options": f"-csearch_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "b-viewer@example.com")
            assert read.main(db, str(target))["shared_videos"] == []
            with pytest.raises(PermissionError):
                write.main(db, str(target), "member_set", member_email="outsider@example.com", role="viewer")

            monkeypatch.setenv("WM_END_USER_EMAIL", "a-owner@example.com")
            with pytest.raises(PermissionError):
                write.main(db, str(source), "share_offer", target_project_id=str(foreign))
            with pytest.raises(ValueError, match="last project owner"):
                write.main(db, str(source), "member_revoke", member_email="a-owner@example.com")
            with pytest.raises(ValueError, match="last project owner"):
                write.main(db, str(source), "member_set", member_email="a-owner@example.com", role="viewer")
            grant = write.main(db, str(source), "share_offer", target_project_id=str(target), expires_days=30)
            assert grant["status"] == "offered"
            assert conn.execute(
                "select project_shared_video_can_read(%s,%s,%s,%s)",
                (source, target, "b-viewer@example.com", video),
            ).fetchone()[0] is False

            monkeypatch.setenv("WM_END_USER_EMAIL", "b-owner@example.com")
            conn.execute("update research_project set status='paused' where id=%s", (source,))
            with pytest.raises(PermissionError, match="RESEARCH_PROJECT_SHARE_DENIED"):
                write.main(db, str(target), "share_accept", grant_id=grant["grant_id"])
            conn.execute("update research_project set status='active' where id=%s", (source,))
            write.main(db, str(target), "share_accept", grant_id=grant["grant_id"])
            monkeypatch.setenv("WM_END_USER_EMAIL", "b-viewer@example.com")
            videos = read.main(db, str(target))["shared_videos"]
            assert len(videos) == 1
            assert videos[0]["title"] == "公开视频"
            assert videos[0]["play_count"] == 100
            assert "raw_payload" not in videos[0]
            for inclusion_status in ("candidate", "shortlisted", "rejected", "archived"):
                conn.execute(
                    "update project_video_inclusion set status=%s where project_id=%s and video_id=%s",
                    (inclusion_status, source, video),
                )
                assert read.main(db, str(target))["shared_videos"] == []
                assert conn.execute(
                    "select project_shared_video_can_read(%s,%s,%s,%s)",
                    (source, target, "b-viewer@example.com", video),
                ).fetchone()[0] is False
            conn.execute(
                "update project_video_inclusion set status='accepted' where project_id=%s and video_id=%s",
                (source, video),
            )
            assert len(read.main(db, str(target))["shared_videos"]) == 1
            conn.execute("update source_video set availability_status='unavailable' where id=%s", (video,))
            assert read.main(db, str(target))["shared_videos"] == []
            conn.execute("update source_video set availability_status='available' where id=%s", (video,))
            conn.execute("update research_project set status='paused' where id=%s", (source,))
            assert conn.execute(
                "select project_shared_video_can_read(%s,%s,%s,%s)",
                (source, target, "b-viewer@example.com", video),
            ).fetchone()[0] is False
            conn.execute("update research_project set status='active' where id=%s", (source,))
            conn.execute(
                """update project_video_share_grant
                   set offered_at=now()-interval '40 days',
                       effective_until=now()-interval '10 days'
                   where id=%s""",
                (grant["grant_id"],),
            )
            assert conn.execute(
                "select project_shared_video_can_read(%s,%s,%s,%s)",
                (source, target, "b-viewer@example.com", video),
            ).fetchone()[0] is False
            conn.execute(
                "update project_video_share_grant set effective_until=now()+interval '10 days' where id=%s",
                (grant["grant_id"],),
            )
            with pytest.raises(PermissionError):
                read.main(db, str(source))

            monkeypatch.setenv("WM_END_USER_EMAIL", "b-owner@example.com")
            write.main(db, str(target), "member_revoke", member_email="b-viewer@example.com")
            monkeypatch.setenv("WM_END_USER_EMAIL", "b-viewer@example.com")
            with pytest.raises(PermissionError):
                read.main(db, str(target))
            monkeypatch.setenv("WM_END_USER_EMAIL", "b-owner@example.com")
            write.main(db, str(target), "member_set", member_email="b-viewer@example.com", role="viewer")
            monkeypatch.setenv("WM_END_USER_EMAIL", "b-viewer@example.com")
            assert len(read.main(db, str(target))["shared_videos"]) == 1

            monkeypatch.setenv("WM_END_USER_EMAIL", "a-owner@example.com")
            write.main(db, str(source), "member_set", member_email="colleague@example.com", role="researcher")
            assert read.main(db, str(source))["members"]
            write.main(db, str(source), "member_revoke", member_email="colleague@example.com")
            write.main(db, str(source), "share_revoke", grant_id=grant["grant_id"])
            monkeypatch.setenv("WM_END_USER_EMAIL", "b-viewer@example.com")
            assert read.main(db, str(target))["shared_videos"] == []
            monkeypatch.setenv("WM_END_USER_EMAIL", "outsider@example.com")
            with pytest.raises(PermissionError):
                read.main(db, str(target))
            assert conn.execute("select count(*) from project_access_event").fetchone()[0] >= 5
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
