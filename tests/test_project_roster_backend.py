from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
MIGRATION = ROOT / "db/migrations/021_project_account_foundation.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _load(stem: str):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_project_roster_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_project_roster_backends_are_inline_and_bind_only_research_db() -> None:
    for stem in ("get_my_projects", "get_project_accounts"):
        metadata = (BACKEND / f"{stem}.yaml").read_text(encoding="utf-8")
        assert metadata == (
            "type: inline\nfields:\n  db:\n    type: static\n"
            "    value: $res:f/content_research/research_db\n"
        )


def test_project_l3_capability_bit_fails_closed_without_exposing_roster(monkeypatch) -> None:
    roster = _load("get_my_projects")
    wmill = types.ModuleType("wmill")
    wmill.get_variable = lambda _path: '["owner@example.com", "other@example.com"]'  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "wmill", wmill)
    assert roster._central_l3_reviewer("owner@example.com") is True
    assert roster._central_l3_reviewer("viewer@example.com") is False
    wmill.get_variable = lambda _path: '["owner@example.com", "UPPER@example.com"]'  # type: ignore[attr-defined]
    assert roster._central_l3_reviewer("owner@example.com") is False
    wmill.get_variable = lambda _path: 'invalid-json'  # type: ignore[attr-defined]
    assert roster._central_l3_reviewer("owner@example.com") is False


def test_project_account_route_does_not_accept_actor_or_authorization_fields() -> None:
    module = _load("get_project_accounts")
    source = (BACKEND / "get_project_accounts.py").read_text(encoding="utf-8")
    assert tuple(module.main.__annotations__) == ("db", "project_id", "limit", "after_relation_id")
    assert "WM_END_USER_EMAIL" in source
    assert "credential_ref" not in source
    assert "account_authorization" not in source
    assert "metadata" not in source
    with pytest.raises(ValueError, match="PROJECT_ID_INVALID"):
        module._project_uuid("not-a-uuid")
    assert module._safe_limit(9999) == 100
    assert module._safe_limit(0) == 1
    assert module._safe_cursor(None) is None
    with pytest.raises(ValueError, match="PROJECT_ACCOUNT_CURSOR_INVALID"):
        module._safe_cursor("bad-cursor")


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_roster_enforces_actor_membership_org_project_and_relation_windows(monkeypatch) -> None:
    assert DSN
    get_my_projects = _load("get_my_projects")
    get_project_accounts = _load("get_project_accounts")
    namespace = f"project_roster_{uuid4().hex}"
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(
                """
                create extension if not exists pgcrypto;
                create table source_account (
                  id uuid primary key default gen_random_uuid(),
                  platform text not null default 'douyin',
                  platform_account_id text not null default '',
                  nickname text,
                  profile_url text,
                  bio text,
                  location_text text,
                  account_type text,
                  certification_type text,
                  first_seen_at timestamptz not null default now(),
                  last_seen_at timestamptz not null default now()
                );
                """,
                prepare=False,
            )
            conn.execute(migration, prepare=False)
            conn.execute(
                """
                create table source_video (
                  id uuid primary key default gen_random_uuid(),
                  account_id uuid references source_account(id)
                );
                create table project_video_inclusion (
                  project_id uuid not null references research_project(id),
                  video_id uuid not null references source_video(id),
                  status text not null default 'candidate',
                  last_seen_at timestamptz not null default now(),
                  primary key (project_id, video_id)
                );
                create table account_metric_snapshot (
                  id bigserial primary key,
                  account_id uuid not null references source_account(id),
                  captured_at timestamptz not null default now(),
                  follower_count bigint
                );
                """,
                prepare=False,
            )
            org = conn.execute(
                "insert into research_organization(slug, name) values ('roster-org', '名册组织') returning id"
            ).fetchone()[0]
            cross_org = conn.execute(
                "insert into research_organization(slug, name) values ('cross-org', '跨组织') returning id"
            ).fetchone()[0]
            suspended_org = conn.execute(
                "insert into research_organization(slug, name, status) values ('hidden-org', '隐藏组织', 'suspended') returning id"
            ).fetchone()[0]
            visible_project = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'visible-project', '可见项目', 'active') returning id""",
                (org,),
            ).fetchone()[0]
            cross_org_project = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'cross-org-project', '跨组织项目', 'active') returning id""",
                (cross_org,),
            ).fetchone()[0]
            unjoined_project = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'unjoined-project', '未加入项目', 'active') returning id""",
                (org,),
            ).fetchone()[0]
            hidden_project = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'hidden-project', '隐藏项目', 'active') returning id""",
                (suspended_org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'viewer@example.com', 'analyst')""",
                (visible_project,),
            )
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'viewer@example.com', 'viewer')""",
                (cross_org_project,),
            )
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role, effective_from)
                   values (%s, 'other@example.com', 'viewer', now() - interval '2 minutes')""",
                (unjoined_project,),
            )
            conn.execute(
                """insert into research_project_member(
                     project_id, actor_id, role, effective_from, effective_until
                   ) values (%s, 'viewer@example.com', 'viewer',
                     now() - interval '2 minutes', now() - interval '1 minute')""",
                (hidden_project,),
            )
            account = conn.execute(
                """insert into source_account(platform_account_id, nickname, profile_url, bio, account_type)
                   values ('public-1', '公开账号', 'https://example.test/a', '公开简介', 'creator') returning id"""
            ).fetchone()[0]
            expired_account = conn.execute(
                """insert into source_account(platform_account_id, nickname)
                   values ('expired-1', '过期关系账号') returning id"""
            ).fetchone()[0]
            subject = conn.execute(
                """insert into research_subject(project_id, name, subject_type)
                   values (%s, '项目主体', 'brand') returning id""",
                (visible_project,),
            ).fetchone()[0]
            relation = conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, subject_id, source_account_id, relation_type, task_roles, evidence_ref,
                     verification_status, verified_by
                   ) values (%s, 'roster.relation.visible.001', %s, %s, 'official', array['publish_channel'], 'evidence://visible',
                     'verified', 'owner@example.com') returning id""",
                (visible_project, subject, account),
            ).fetchone()[0]
            conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, source_account_id, relation_type, task_roles, evidence_ref,
                     verification_status, verified_by
                   ) values (%s, 'roster.relation.other.001', %s, 'competitor', array['benchmark_sample'],
                     'evidence://other-project', 'verified', 'owner@example.com')""",
                (unjoined_project, account),
            )
            conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, source_account_id, relation_type, task_roles, evidence_ref
                   ) values (%s, 'roster.relation.pending.001', %s, 'unverified_partner', array['research_reference'],
                     'evidence://pending-project')""",
                (unjoined_project, expired_account),
            )
            conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, source_account_id, relation_type, task_roles, evidence_ref,
                     effective_from, effective_until
                   ) values (%s, 'roster.relation.expired.001', %s, 'competitor', array['benchmark_sample'], 'evidence://expired',
                     now() - interval '2 minutes', now() - interval '1 minute')""",
                (visible_project, expired_account),
            )
            conn.execute(
                """insert into account_authorization(
                     organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                     provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                     credential_ref, granted_by, evidence_ref
                   ) values (%s, %s, 'roster.authorization.visible.001', %s, %s, 'provider', 'project_account', 'read_metrics',
                     array['metric'], array['read'], 'secret://not-returned', 'owner@example.com',
                     'evidence://grant')""",
                (org, visible_project, relation, account),
            )
            # The backend opens a second connection.  Reuse the complete test
            # DSN rather than assuming local peer authentication: GitHub's
            # PostgreSQL service requires the password in TEST_DATABASE_URL.
            conninfo = conninfo_to_dict(DSN)
            params = dict(
                host=conninfo.get("host") or conn.info.host or "127.0.0.1",
                port=int(conninfo.get("port") or conn.info.port or 5432),
                user=conninfo.get("user") or conn.info.user,
                password=conninfo.get("password", ""),
                dbname=conninfo.get("dbname") or conn.info.dbname,
                sslmode=conninfo.get("sslmode", "prefer"),
                options=f"-c search_path={namespace}",
            )
            monkeypatch.setenv("WM_END_USER_EMAIL", "VIEWER@example.com")
            projects = get_my_projects.main(params)
            assert {project["id"] for project in projects["projects"]} == {
                str(visible_project), str(cross_org_project),
            }
            visible = next(project for project in projects["projects"] if project["id"] == str(visible_project))
            assert visible["member_role"] == "analyst"
            cross_org_result = get_project_accounts.main(params, str(cross_org_project))
            assert cross_org_result["project"]["organization_id"] == str(cross_org)
            assert cross_org_result["accounts"] == []
            result = get_project_accounts.main(params, str(visible_project), limit=999)
            assert result["project"]["id"] == str(visible_project)
            assert len(result["accounts"]) == 1
            public = result["accounts"][0]
            assert public["nickname"] == "公开账号"
            assert public["subject_name"] == "项目主体"
            assert public["task_roles"] == ["publish_channel"]
            assert "credential_ref" not in public
            assert "metadata" not in public
            assert "evidence_ref" not in public
            assert "verified_by" not in public
            assert "purpose" not in public
            with pytest.raises(PermissionError, match="PROJECT_ACCESS_DENIED"):
                get_project_accounts.main(params, str(unjoined_project))
            with pytest.raises(PermissionError, match="PROJECT_ACCESS_DENIED"):
                get_project_accounts.main(params, str(hidden_project))
            monkeypatch.setenv("WM_END_USER_EMAIL", "other@example.com")
            other_projects = get_my_projects.main(params)
            assert [project["id"] for project in other_projects["projects"]] == [str(unjoined_project)]
            other_result = get_project_accounts.main(params, str(unjoined_project))
            assert len(other_result["accounts"]) == 1
            assert other_result["accounts"][0]["account_id"] == str(account)
            assert other_result["accounts"][0]["relation_type"] == "competitor"
            assert other_result["accounts"][0]["task_roles"] == ["benchmark_sample"]
            with pytest.raises(PermissionError, match="PROJECT_ACCESS_DENIED"):
                get_project_accounts.main(params, str(visible_project))
            conn.execute(
                """insert into research_project_member(
                     project_id, actor_id, role, status, effective_from
                   ) values (%s, 'other@example.com', 'viewer', 'revoked',
                     now() - interval '1 minute')""",
                (unjoined_project,),
            )
            assert get_my_projects.main(params)["projects"] == []
            with pytest.raises(PermissionError, match="PROJECT_ACCESS_DENIED"):
                get_project_accounts.main(params, str(unjoined_project))
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_roster_returns_server_side_legacy_admin_without_leaking_allowlist(monkeypatch) -> None:
    """The legacy-admin hint cannot be supplied by a caller or break roster reads."""
    assert DSN
    get_my_projects = _load("get_my_projects")
    namespace = f"project_roster_admin_{uuid4().hex}"
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(
                """
                create extension if not exists pgcrypto;
                create table source_account (
                  id uuid primary key default gen_random_uuid(),
                  platform text not null default 'douyin',
                  platform_account_id text not null default '',
                  nickname text,
                  profile_url text,
                  bio text,
                  location_text text,
                  account_type text,
                  certification_type text,
                  first_seen_at timestamptz not null default now(),
                  last_seen_at timestamptz not null default now()
                );
                """,
                prepare=False,
            )
            conn.execute(migration, prepare=False)
            conn.execute(
                """
                create table source_video (
                  id uuid primary key default gen_random_uuid(),
                  account_id uuid references source_account(id)
                );
                create table project_video_inclusion (
                  project_id uuid not null references research_project(id),
                  video_id uuid not null references source_video(id),
                  status text not null default 'candidate',
                  last_seen_at timestamptz not null default now(),
                  primary key (project_id, video_id)
                );
                create table account_metric_snapshot (
                  id bigserial primary key,
                  account_id uuid not null references source_account(id),
                  captured_at timestamptz not null default now(),
                  follower_count bigint
                );
                """,
                prepare=False,
            )
            org = conn.execute(
                "insert into research_organization(slug, name) values ('admin-org', '管理员组织') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'admin-project', '管理员项目', 'active') returning id""",
                (org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'writer@example.com', 'owner'),
                          (%s, 'member@example.com', 'viewer')""",
                (project, project),
            )
            conninfo = conninfo_to_dict(DSN)
            params = dict(
                host=conninfo.get("host") or conn.info.host or "127.0.0.1",
                port=int(conninfo.get("port") or conn.info.port or 5432),
                user=conninfo.get("user") or conn.info.user,
                password=conninfo.get("password", ""),
                dbname=conninfo.get("dbname") or conn.info.dbname,
                sslmode=conninfo.get("sslmode", "prefer"),
                options=f"-c search_path={namespace}",
            )

            wmill = types.ModuleType("wmill")
            wmill.get_variable = lambda path: '["writer@example.com", "not-returned@example.com"]'  # type: ignore[attr-defined]
            monkeypatch.setitem(sys.modules, "wmill", wmill)
            monkeypatch.setenv("WM_END_USER_EMAIL", "WRITER@example.com")
            writer = get_my_projects.main(params)
            assert writer["legacy_admin"] is True
            assert [item["id"] for item in writer["projects"]] == [str(project)]
            assert "not-returned@example.com" not in repr(writer)

            monkeypatch.setenv("WM_END_USER_EMAIL", "member@example.com")
            member = get_my_projects.main(params)
            assert member["legacy_admin"] is False
            assert [item["id"] for item in member["projects"]] == [str(project)]

            def unavailable(_path: str) -> str:
                raise RuntimeError("variable service unavailable")

            wmill.get_variable = unavailable  # type: ignore[attr-defined]
            failed_variable = get_my_projects.main(params)
            assert failed_variable["legacy_admin"] is False
            assert [item["id"] for item in failed_variable["projects"]] == [str(project)]

            monkeypatch.delenv("WM_END_USER_EMAIL")
            with pytest.raises(PermissionError, match="RESEARCH_ACTION_IDENTITY_REQUIRED"):
                get_my_projects.main(params)
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
