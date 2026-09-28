from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/get_project_accounts.py"
SCHEMA = ROOT / "db/schema.sql"
CURSOR_MIGRATION = ROOT / "db/migrations/024_project_account_relation_cursor.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location("project_account_matrix_backend", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_project_account_matrix_does_not_select_private_or_provider_raw_fields() -> None:
    source = BACKEND.read_text(encoding="utf-8")
    migration = CURSOR_MIGRATION.read_text(encoding="utf-8")
    assert "included_public_video_count" in source
    assert "last_included_at" in source
    assert "latest_account_metric.follower_count" in source
    assert "latest_account_metric.captured_at as follower_captured_at" in source
    assert "inclusion_row.project_id = r.project_id" in source
    assert "inclusion_row.status <> 'archived'" in source
    assert "raw_metrics" not in source
    assert "source_endpoint" not in source
    assert "provider" not in source
    assert "evidence_ref" not in source
    assert "credential_ref" not in source
    assert "with authorized_project as materialized" in source
    assert "left join lateral (" in source
    # One read-only setup statement and one data statement: ACL and rows
    # must share a PostgreSQL statement snapshot during concurrent revocation.
    assert source.count("cur.execute(") == 2
    assert "idx_project_account_relation_verified_cursor" in migration
    assert "on project_account_relation(project_id, id)" in migration


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_account_matrix_isolates_shared_account_videos_and_revoked_member(monkeypatch) -> None:
    assert DSN
    module = _load()
    namespace = f"project_account_matrix_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            organization = conn.execute(
                "insert into research_organization(slug,name) values ('matrix-org','矩阵组织') returning id"
            ).fetchone()[0]
            project_a, project_b = [
                conn.execute(
                    """insert into research_project(organization_id,slug,name,status)
                       values (%s,%s,%s,'active') returning id""",
                    (organization, slug, name),
                ).fetchone()[0]
                for slug, name in (("matrix-a", "项目 A"), ("matrix-b", "项目 B"))
            ]
            for project_id in (project_a, project_b):
                conn.execute(
                    """insert into research_project_member(project_id,actor_id,role,status)
                       values (%s,'viewer@example.com','viewer','active')""",
                    (project_id,),
                )
            account_id = conn.execute(
                """insert into source_account(platform,platform_account_id,nickname)
                   values ('douyin','matrix-account','共享公开账号') returning id"""
            ).fetchone()[0]
            for project_id, key in ((project_a, "matrix.relation.a.001"), (project_b, "matrix.relation.b.001")):
                conn.execute(
                    """insert into project_account_relation(
                         project_id,idempotency_key,source_account_id,relation_type,task_roles,evidence_ref,
                         verification_status,verified_by
                       ) values (%s,%s,%s,'official',array['publish_channel'],'evidence://matrix',
                         'verified','owner@example.com')""",
                    (project_id, key, account_id),
                )
            video_a = conn.execute(
                """insert into source_video(platform,platform_video_id,account_id)
                   values ('douyin','matrix-video-a',%s) returning id""",
                (account_id,),
            ).fetchone()[0]
            video_b = conn.execute(
                """insert into source_video(platform,platform_video_id,account_id)
                   values ('douyin','matrix-video-b',%s) returning id""",
                (account_id,),
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,first_seen_at,last_seen_at)
                   values (%s,%s,'manual',now()-interval '1 hour',now()-interval '1 hour'),
                          (%s,%s,'manual',now(),now())""",
                (project_a, video_a, project_b, video_b),
            )
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values (%s,%s,'manual','archived')""",
                (project_a, video_b),
            )
            conn.execute(
                """insert into account_metric_snapshot(
                     account_id,provider,source_endpoint,captured_at,follower_count,raw_metrics
                   ) values
                     (%s,'fixture','private-endpoint',now()-interval '1 hour',100,'{"secret":true}'::jsonb),
                     (%s,'fixture','private-endpoint',now(),200,'{"secret":false}'::jsonb),
                     (%s,'fixture','private-endpoint',now()+interval '1 minute',null,'{"secret":"newer-empty"}'::jsonb)""",
                (account_id, account_id, account_id),
            )
            conninfo = conninfo_to_dict(DSN)
            params = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1",
                "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user,
                "password": conninfo.get("password", ""),
                "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"),
                "options": f"-c search_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "VIEWER@example.com")
            results = {}
            for project_id in (project_a, project_b):
                result = module.main(params, str(project_id))
                assert len(result["accounts"]) == 1
                row = result["accounts"][0]
                results[project_id] = row
                assert row["account_id"] == str(account_id)
                assert row["included_public_video_count"] == 1
                assert row["follower_count"] == 200
                assert row["follower_captured_at"]
                assert row["last_included_at"]
                assert "raw_metrics" not in row
                assert "source_endpoint" not in row
                assert "provider" not in row
                assert "evidence_ref" not in row
                assert "credential_ref" not in row
            assert results[project_a]["last_included_at"] < results[project_b]["last_included_at"]
            conn.execute(
                """insert into account_metric_snapshot(
                     account_id,provider,source_endpoint,observation_key,
                     captured_at,follower_count)
                   values (%s,'tikhub','douyin.app.user_profile',
                           'account-profile:matrix',now(),1755)""",
                (account_id,),
            )
            conn.execute(
                """insert into account_metric_snapshot(
                     account_id,provider,source_endpoint,observation_key,
                     captured_at,follower_count)
                   values (%s,'tikhub','douyin.app.one_video',
                           'video-author-zero',now()+interval '2 minutes',0)""",
                (account_id,),
            )
            for project_id in (project_a, project_b):
                assert module.main(params, str(project_id))["accounts"][0]["follower_count"] == 1755
            conn.execute(
                """insert into project_account_relation(
                     project_id,idempotency_key,source_account_id,relation_type,task_roles,evidence_ref,
                     verification_status,verified_by
                   ) values (%s,'matrix.relation.a.002',%s,'media_reference',array['research_reference'],
                     'evidence://matrix-2','verified','owner@example.com')""",
                (project_a, account_id),
            )
            first_page = module.main(params, str(project_a), limit=1)
            second_page = module.main(
                params, str(project_a), limit=1,
                after_relation_id=first_page["next_cursor"],
            )
            assert first_page["has_more"] is True and first_page["next_cursor"]
            assert second_page["has_more"] is False and second_page["next_cursor"] is None
            assert first_page["accounts"][0]["relation_id"] != second_page["accounts"][0]["relation_id"]
            assert {first_page["accounts"][0]["relation_type"], second_page["accounts"][0]["relation_type"]} == {"official", "media_reference"}
            conn.execute(
                "update source_account set nickname='变化后的昵称' where id=%s",
                (account_id,),
            )
            after_rename = module.main(
                params, str(project_a), limit=1,
                after_relation_id=first_page["next_cursor"],
            )
            assert after_rename["accounts"][0]["relation_id"] == second_page["accounts"][0]["relation_id"]
            conn.execute("set enable_seqscan=off")
            plan = "\n".join(row[0] for row in conn.execute(
                """explain select id from project_account_relation
                   where project_id=%s and verification_status='verified' and id>%s
                   order by id limit 1""",
                (project_a, first_page["next_cursor"]),
            ).fetchall())
            assert "idx_project_account_relation_verified_cursor" in plan
            conn.execute("set enable_seqscan=on")
            conn.execute(
                """update research_project_member
                   set effective_from=now()-interval '2 hours'
                   where project_id=%s and actor_id='viewer@example.com'""",
                (project_a,),
            )
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status,effective_from)
                   values (%s,'viewer@example.com','viewer','revoked',now()-interval '1 hour')""",
                (project_a,),
            )
            with pytest.raises(PermissionError, match="PROJECT_ACCESS_DENIED"):
                module.main(params, str(project_a))
            assert module.main(params, str(project_b))["accounts"][0]["included_public_video_count"] == 1
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
