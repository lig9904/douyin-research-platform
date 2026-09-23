from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/023_project_video_read_acl.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_project_video_read_acl_is_security_invoker_and_fail_closed() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "function project_actor_can_read(p_project_id uuid, p_actor text)" in source
        assert "function project_video_can_read(" in source
        assert "security invoker" in source
        assert "member.effective_from <= now()" in source
        assert "order by member.effective_from desc" in source
        assert "limit 1" in source
        assert "member_row.status = 'active'" in source
        assert "inclusion_row.status <> 'archived'" in source
        assert "coalesce(" in source
        assert "security definer" not in source.lower()
        assert "insert into project_video_inclusion" not in source.lower()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_video_read_acl_replays_after_schema_and_denies_wrong_scope() -> None:
    assert DSN
    namespace = f"project_video_acl_{uuid4().hex}"
    schema = SCHEMA.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(schema, prepare=False)
            conn.execute(migration, prepare=False)

            org_a = conn.execute(
                "insert into research_organization(slug, name) values ('org-a', '组织 A') returning id"
            ).fetchone()[0]
            org_b = conn.execute(
                "insert into research_organization(slug, name) values ('org-b', '组织 B') returning id"
            ).fetchone()[0]
            project_a = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-a', '项目 A', 'active') returning id""",
                (org_a,),
            ).fetchone()[0]
            project_b = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-b', '项目 B', 'active') returning id""",
                (org_b,),
            ).fetchone()[0]
            project_paused = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-paused', '暂停项目', 'paused') returning id""",
                (org_a,),
            ).fetchone()[0]
            video = conn.execute(
                "insert into source_video(platform, platform_video_id) values ('douyin', 'shared-video') returning id"
            ).fetchone()[0]
            archived_video = conn.execute(
                "insert into source_video(platform, platform_video_id) values ('douyin', 'archived-video') returning id"
            ).fetchone()[0]

            for project_id, actor_id in (
                (project_a, 'alice@example.com'),
                (project_b, 'bob@example.com'),
                (project_paused, 'paul@example.com'),
            ):
                conn.execute(
                    """insert into research_project_member(project_id, actor_id, role, status, effective_from)
                       values (%s, %s, 'viewer', 'active', now() - interval '2 days')""",
                    (project_id, actor_id),
                )
            # This later revoked row must win; the function may never fall back
            # to the preceding active history row.
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role, status, effective_from)
                   values (%s, 'revoked@example.com', 'viewer', 'active', now() - interval '2 days'),
                          (%s, 'revoked@example.com', 'viewer', 'revoked', now() - interval '1 day')""",
                (project_a, project_a),
            )
            conn.execute(
                """insert into research_project_member(
                     project_id, actor_id, role, status, effective_from, effective_until
                   ) values (%s, 'expired@example.com', 'viewer', 'active', now() - interval '2 days', now() - interval '1 day')""",
                (project_a,),
            )

            for project_id, video_id, status in (
                (project_a, video, 'candidate'),
                (project_b, video, 'accepted'),
                (project_a, archived_video, 'archived'),
                (project_paused, video, 'candidate'),
            ):
                conn.execute(
                    """insert into project_video_inclusion(project_id, video_id, source_type, status)
                       values (%s, %s, 'manual', %s)""",
                    (project_id, video_id, status),
                )

            def can_read(project_id, actor_id, video_id=None) -> bool:
                if video_id is None:
                    return conn.execute(
                        "select project_actor_can_read(%s, %s)", (project_id, actor_id)
                    ).fetchone()[0]
                return conn.execute(
                    "select project_video_can_read(%s, %s, %s)", (project_id, actor_id, video_id)
                ).fetchone()[0]

            assert can_read(project_a, 'alice@example.com', video) is True
            assert can_read(project_b, 'bob@example.com', video) is True
            assert can_read(project_b, 'alice@example.com', video) is False
            assert can_read(project_a, 'alice@example.com', archived_video) is False
            assert can_read(project_paused, 'paul@example.com', video) is False
            assert can_read(project_a, 'revoked@example.com', video) is False
            assert can_read(project_a, 'expired@example.com', video) is False
            assert can_read(project_a, None, video) is False
            assert can_read(None, 'alice@example.com', video) is False
            assert can_read(project_a, 'alice@example.com', uuid4()) is False
            assert can_read(uuid4(), 'alice@example.com') is False
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
