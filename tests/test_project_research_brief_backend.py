from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")


def _load(stem: str):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_project_scope_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_project_brief_backends_fail_closed_and_keep_legacy_explicit() -> None:
    mutate = _load("mutate_research_brief")
    read = _load("get_research_briefs")
    mutation_source = Path(mutate.__file__).read_text(encoding="utf-8")
    read_source = Path(read.__file__).read_text(encoding="utf-8")

    assert mutate._project_id(None) is None
    assert mutate._project_id("") is None
    with pytest.raises(mutate.ResearchBriefError, match="project_id is invalid"):
        mutate._project_id("not-a-uuid")
    with pytest.raises(mutate.ResearchBriefError, match="metadata depth only"):
        mutate._config(
            name="项目媒体", platform="douyin", source_type="keyword", target="渔岛",
            time_window_hours=24, max_items=1, depth="comments", cadence_hours=None,
            project_scoped=True,
        )
    assert '"project_id": project_id' in mutation_source
    assert "RESEARCH_PROJECT_ACCESS_DENIED" in mutation_source
    assert "project_id is not distinct from %s" in mutation_source
    assert "project_id is null or depth='metadata'" in mutation_source
    assert "select role, status, effective_until" in mutation_source
    assert "and (membership.effective_until is null or membership.effective_until > now())" in mutation_source
    assert "research_project_member" in read_source
    assert "RESEARCH_PROJECT_ACCESS_DENIED" in read_source
    assert "select status, effective_until" in read_source
    assert "project_id is null and %s::uuid is null and owner_actor=%s" in read_source


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_briefs_use_member_acl_revoke_and_legacy_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DSN
    mutate = _load("mutate_research_brief")
    read = _load("get_research_briefs")
    schema_name = f"project_brief_{uuid4().hex}"
    schema_sql = (ROOT / "db/schema.sql").read_text(encoding="utf-8")
    owner = "owner@example.com"
    collaborator = "researcher@example.com"
    viewer = "viewer@example.com"
    legacy_writer = "legacy@example.com"

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
            conn.execute(schema_sql, prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values ('brief-org','Brief Org') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'brief-project','Brief Project','active') returning id""",
                (org,),
            ).fetchone()[0]
            for actor, role in ((owner, "owner"), (collaborator, "researcher"), (viewer, "viewer")):
                conn.execute(
                    """insert into research_project_member(project_id,actor_id,role,status)
                       values (%s,%s,%s,'active')""",
                    (project, actor, role),
                )
        except Exception:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema_name)))
            raise

        scoped_dsn = make_conninfo(DSN, options=f"-c search_path={schema_name}")
        monkeypatch.setattr(mutate, "_dsn", lambda _db: scoped_dsn)
        monkeypatch.setenv("WM_END_USER_EMAIL", owner)
        created = mutate.main(
            {}, action="create", idempotency_key=str(uuid4()), project_id=str(project),
            name="协作任务", source_type="keyword", target="秦皇岛渔岛", max_items=1,
        )
        brief_id = created["brief_id"]

        monkeypatch.setenv("WM_END_USER_EMAIL", collaborator)
        updated = mutate.main(
            {}, action="update", idempotency_key=str(uuid4()), project_id=str(project),
            brief_id=brief_id, name="协作任务更新", source_type="keyword", target="秦皇岛渔岛",
            max_items=1,
        )
        assert updated["brief_id"] == brief_id

        real_connect = psycopg.connect
        monkeypatch.setattr(
            read.psycopg, "connect",
            lambda *args, **kwargs: real_connect(scoped_dsn, row_factory=kwargs.get("row_factory")),
        )
        visible = read.main({"host": "unused", "port": 5432, "user": "unused", "password": "unused", "dbname": "unused", "sslmode": "prefer"}, project_id=str(project))
        assert [brief["id"] for brief in visible["briefs"]] == [brief_id]

        monkeypatch.setenv("WM_END_USER_EMAIL", viewer)
        with pytest.raises(PermissionError, match="RESEARCH_PROJECT_ACCESS_DENIED"):
            mutate.main(
                {}, action="pause", idempotency_key=str(uuid4()), project_id=str(project), brief_id=brief_id,
            )

        monkeypatch.setenv("WM_END_USER_EMAIL", legacy_writer)
        legacy = mutate.main(
            {}, action="create", idempotency_key=str(uuid4()), writer_allowlist=legacy_writer,
            name="旧任务", source_type="low_fan", max_items=1,
        )
        legacy_visible = read.main({"host": "unused", "port": 5432, "user": "unused", "password": "unused", "dbname": "unused", "sslmode": "prefer"})
        assert [brief["id"] for brief in legacy_visible["briefs"]] == [legacy["brief_id"]]

        with real_connect(DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
            conn.execute(
                """update research_project_member set effective_from=now()-interval '2 hours'
                   where project_id=%s and actor_id=%s""",
                (project, collaborator),
            )
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status,effective_from)
                   values (%s,%s,'researcher','revoked',now()-interval '1 hour')""",
                (project, collaborator),
            )
        monkeypatch.setenv("WM_END_USER_EMAIL", collaborator)
        with pytest.raises(PermissionError, match="RESEARCH_PROJECT_ACCESS_DENIED"):
            read.main({"host": "unused", "port": 5432, "user": "unused", "password": "unused", "dbname": "unused", "sslmode": "prefer"}, project_id=str(project))

        with real_connect(DSN, autocommit=True) as conn:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema_name)))
