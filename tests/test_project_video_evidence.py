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
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/manage_project_video_evidence.py"
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location("project_video_evidence_test", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_project_video_evidence_exact_public_reference_and_static_db(monkeypatch) -> None:
    module = _load()
    assert module._video_key("1234567890123456789") == "1234567890123456789"
    assert module._video_key("https://www.douyin.com/video/1234567890123456789") == "1234567890123456789"
    assert module._video_key("https://www.douyin.com/?modal_id=1234567890123456789") == "1234567890123456789"
    assert module._video_key("https://www.douyin.com/video/1234567890123456789?from=copy") == "1234567890123456789"
    assert module._video_key("https://www.douyin.com/user/abc?modal_id=1234567890123456789&from=copy") == "1234567890123456789"
    for value in ("https://evil.example/video/1234567890123456789", "http://www.douyin.com/video/1234567890123456789", "https://v.douyin.com/abc", "123", "https://www.douyin.com/video/1234567890123456789?modal_id=9999999999999999999"):
        with pytest.raises(ValueError):
            module._video_key(value)
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(PermissionError):
        module._actor()
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    for reviewed in (False, "true", 1):
        with pytest.raises(ValueError, match="content review confirmation is required"):
            module.main({}, str(uuid4()), "accept", "12345678", str(uuid4()), reviewed)
    assert BACKEND.with_suffix(".yaml").read_text() == (
        "type: inline\nfields:\n  db:\n    type: static\n"
        "    value: $res:f/content_research/research_db\n"
    )
    page = (BACKEND.parent.parent / "ProjectVideoEvidence.tsx").read_text()
    assert "backend.manage_project_video_evidence" in page
    assert "确认纳入本项目公开依据" in page
    assert "https://www.douyin.com/video/${candidate.platform_video_id}" in page
    assert 'rel="noopener noreferrer"' in page
    assert "若原视频无法打开，不要仅凭标题或指标判断是否可比" in page
    assert "content_review_confirmed: true" in page
    assert r"/^\d{8,32}$/" in page
    assert "disabled={!contentReviewed || candidate.project_status === 'accepted'}" in page
    assert "key={projectId}" in (BACKEND.parent.parent / "VideoLibrary.tsx").read_text()
    assert module._video_key("12345678") == "12345678"
    assert module._video_key("1" * 32) == "1" * 32


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_video_evidence_requires_manager_and_exact_acceptance(monkeypatch) -> None:
    assert DSN
    module = _load()
    namespace = f"project_video_evidence_{uuid4().hex}"
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(), prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('evidence-org','证据组织') returning id"
            ).fetchone()[0]
            a, b = [conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,%s,%s,'active') returning id",
                (org, slug, name),
            ).fetchone()[0] for slug, name in (("evidence-a", "项目 A"), ("evidence-b", "项目 B"))]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role) values
                   (%s,'owner-a@example.com','owner'),
                   (%s,'viewer-a@example.com','viewer'),
                   (%s,'owner-b@example.com','owner')""",
                (a, a, b),
            )
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values ('douyin','1234567890123456789','可核对的公开视频') returning id"""
            ).fetchone()[0]
            db = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1",
                "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user,
                "password": conninfo.get("password", ""),
                "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"),
                "options": f"-c search_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer-a@example.com")
            with pytest.raises(PermissionError):
                module.main(db, str(a), "preview", "1234567890123456789")
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner-a@example.com")
            assert module.main(db, str(a), "preview", "9999999999999999999") == {"found": False, "video": None}
            preview = module.main(db, str(a), "preview", "1234567890123456789")
            assert preview["found"] and preview["video"]["id"] == str(video)
            assert preview["video"]["project_status"] is None
            assert "source_url" not in preview["video"]
            assert "raw_payload" not in preview["video"]
            key = str(uuid4())
            with pytest.raises(ValueError, match="content review confirmation is required"):
                module.main(db, str(a), "accept", "1234567890123456789", key)
            with pytest.raises(ValueError, match="content review confirmation is required"):
                module.main(db, str(a), "accept", "1234567890123456789", key, "true")
            assert conn.execute("select count(*) from research_user_action where action_type='project_video.accept'").fetchone()[0] == 0
            result = module.main(db, str(a), "accept", "1234567890123456789", key, True)
            assert result["changed"] and result["video_id"] == str(video)
            assert module.main(db, str(a), "accept", "1234567890123456789", key, True)["idempotent_replay"] is True
            assert module.main(db, str(a), "accept", "1234567890123456789", str(uuid4()), True)["changed"] is False
            assert conn.execute(
                "select source_type,status,metadata->>'last_accepted_by',metadata->>'content_review_confirmed_by' from project_video_inclusion where project_id=%s and video_id=%s",
                (a, video),
            ).fetchone() == ("manual", "accepted", "owner-a@example.com", "owner-a@example.com")
            assert conn.execute(
                "select count(*) from research_user_action where action_type='project_video.accept'"
            ).fetchone()[0] == 2
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner-b@example.com")
            assert module.main(db, str(b), "preview", "1234567890123456789")["video"]["project_status"] is None
            assert conn.execute(
                "select project_shared_video_can_read(%s,%s,%s,%s)",
                (a, b, "owner-b@example.com", video),
            ).fetchone()[0] is False
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
