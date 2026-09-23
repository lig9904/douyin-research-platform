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
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/get_video_library.py"
SCHEMA = ROOT / "db/schema.sql"
MIGRATIONS = (
    ROOT / "db/migrations/021_project_account_foundation.sql",
    ROOT / "db/migrations/022_project_task_ownership.sql",
    ROOT / "db/migrations/023_project_video_read_acl.sql",
)
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location("project_video_library_acl", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_video_library_requires_server_identity_and_static_legacy_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    source = BACKEND.read_text(encoding="utf-8")
    metadata = BACKEND.with_suffix(".yaml").read_text(encoding="utf-8")

    assert "WM_END_USER_EMAIL" in source
    assert "project_actor_can_read(%s::uuid, %s)" in source
    assert "project_video_can_read(%s::uuid, %s, %s::uuid)" in source
    assert "project_video_inclusion inclusion_row" in source
    assert "inclusion_row.status <> 'archived'" in source
    assert "Never reuse globally scoped ASR/L3 records in a project view." in source
    assert "legacy_reader_allowlist" not in metadata
    assert "wmill.get_variable(\"f/content_research/research_action_writers\")" in source
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
        module._actor()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_video_library_blocks_known_cross_project_video_and_preserves_legacy_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DSN
    module = _load()
    namespace = f"project_video_library_{uuid4().hex}"
    schema = SCHEMA.read_text(encoding="utf-8")
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(schema, prepare=False)
            for migration in MIGRATIONS:
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

            organization = conn.execute(
                "insert into research_organization(slug, name) values ('library-org', '视频库组织') returning id"
            ).fetchone()[0]
            project_a = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-a', '项目 A', 'active') returning id""",
                (organization,),
            ).fetchone()[0]
            project_b = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-b', '项目 B', 'active') returning id""",
                (organization,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'viewer@example.com', 'viewer')""",
                (project_a,),
            )
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'viewer@example.com', 'viewer')""",
                (project_b,),
            )
            video_a = conn.execute(
                """insert into source_video(platform, platform_video_id, title, last_seen_at)
                   values ('douyin', 'library-a', 'A 可见视频', now()) returning id"""
            ).fetchone()[0]
            video_b = conn.execute(
                """insert into source_video(platform, platform_video_id, title, last_seen_at)
                   values ('douyin', 'library-b', 'B 私有视频', now()) returning id"""
            ).fetchone()[0]
            for video_id, project_id, inclusion_source in (
                (video_a, project_a, "manual"),
                (video_b, project_b, "import"),
            ):
                conn.execute(
                    """insert into project_video_inclusion(project_id, video_id, source_type)
                       values (%s, %s, %s)""",
                    (project_id, video_id, inclusion_source),
                )
                conn.execute(
                    """insert into discovery_event(
                         video_id, provider, source_type, source_key, observation_key
                       ) values (%s, 'fixture', %s, %s, %s)""",
                    (video_id, f"private-discovery-{project_id}", f"key-{video_id}", f"observation-{video_id}"),
                )
            conn.execute(
                """insert into project_video_inclusion(project_id, video_id, source_type, source_ref, metadata)
                   values (%s, %s, 'import', 'B-private-ref', '{"owner":"B"}'::jsonb)""",
                (project_b, video_a),
            )
            conn.execute(
                """insert into video_score(video_id, score_type, score, rule_version)
                   values (%s, 'priority', 99, 'private-b-score')""",
                (video_a,),
            )
            collection_id = conn.execute(
                "insert into collection(name, created_by) values ('B 私有收藏', 'owner@example.com') returning id"
            ).fetchone()[0]
            conn.execute(
                "insert into collection_item(collection_id, video_id) values (%s, %s)",
                (collection_id, video_a),
            )

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
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: "viewer@example.com")
            scoped = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
            assert [item["id"] for item in scoped["items"]] == [str(video_a)]
            assert scoped["source_options"] == [{"value": "manual"}]
            item = scoped["items"][0]
            assert item["sources"] == ["manual"]
            assert item["research_level"] is None
            assert item["monitoring_status"] is None
            assert item["monitoring_priority"] is None
            assert item["account_id"] is None
            assert item["priority"] is None
            assert item["collection_count"] is None
            assert scoped["detail"]["id"] == str(video_a)
            assert scoped["detail"]["sources"] == ["manual"]
            assert scoped["detail"]["evidence"] == []
            assert scoped["detail"]["priority"] is None
            assert scoped["detail"]["collection_count"] is None
            assert scoped["detail"]["asr_transcript"] is None
            assert scoped["detail"]["l3_analysis"] is None
            assert "B-private-ref" not in str(scoped)
            assert "private-b-score" not in str(scoped)
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params, project_id=str(project_a), priority_min=1)
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params, project_id=str(project_a), sort="priority_desc")
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params, project_id=str(project_a), selected_video_id=str(video_b))
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: "")
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params)
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: (_ for _ in ()).throw(RuntimeError("missing variable")))
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params)
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: "viewer@example.com")
            legacy = module.main(params)
            assert {item["id"] for item in legacy["items"]} == {str(video_a), str(video_b)}
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
