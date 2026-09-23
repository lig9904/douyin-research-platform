from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location(
        "project_video_metric_acl_test", BACKEND / "get_video_metric_timeline.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_metric_timeline_requires_project_video_and_current_member(monkeypatch) -> None:
    assert DSN
    namespace = f"project_metric_{uuid4().hex}"
    backend = _load()
    real_connect = psycopg.connect
    with real_connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values ('metric-org','Metric Org') returning id"
            ).fetchone()[0]
            projects = [
                conn.execute(
                    """insert into research_project(organization_id,slug,name,status)
                       values (%s,%s,%s,'active') returning id""",
                    (org, f"project-{i}", f"Project {i}"),
                ).fetchone()[0]
                for i in (1, 2)
            ]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values (%s,'viewer@example.com','viewer','active')""",
                (projects[0],),
            )
            video = conn.execute(
                "insert into source_video(platform,platform_video_id) values ('douyin','metric-shared') returning id"
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type)
                   values (%s,%s,'manual')""",
                (projects[0], video),
            )
            conn.execute(
                """insert into metric_snapshot(video_id,provider,captured_at,play_count)
                   values (%s,'fixture',now(),123)""",
                (video,),
            )
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            monkeypatch.setattr(
                backend.psycopg, "connect",
                lambda **kwargs: real_connect(scoped_dsn, row_factory=kwargs.get("row_factory")),
            )
            db = {"host": "unused", "port": 5432, "user": "unused", "password": "unused", "dbname": "unused"}
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            result = backend.main(db, str(video), project_id=str(projects[0]))
            assert result["total"] == 1 and result["items"][0]["play_count"] == 123
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                backend.main(db, str(video), project_id=str(projects[1]))
            monkeypatch.setattr(backend, "_get_legacy_allowlist", lambda: "")
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                backend.main(db, str(video))
            monkeypatch.setattr(backend, "_get_legacy_allowlist", lambda: "viewer@example.com")
            assert backend.main(db, str(video))["total"] == 1
            conn.execute(
                """update research_project_member set effective_from=now()-interval '2 hours'
                   where project_id=%s and actor_id='viewer@example.com'""",
                (projects[0],),
            )
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status,effective_from)
                   values (%s,'viewer@example.com','viewer','revoked',now()-interval '1 hour')""",
                (projects[0],),
            )
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                backend.main(db, str(video), project_id=str(projects[0]))
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
