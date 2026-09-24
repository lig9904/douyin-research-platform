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
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")


def _load(name: str):
    path = BACKEND / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_{uuid4().hex}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_decision_loop_contract_is_project_local_and_strict() -> None:
    migration = (ROOT / "db/migrations/028_project_decision_loop.sql").read_text()
    assert "project_decision_card requires locally accepted project video" in migration
    assert "project_video_share_grant" not in migration.split("create table if not exists project_decision_card", 1)[1].split("create table if not exists project_decision_card_event", 1)[0]
    assert "project_publication_metric_observation" in migration
    assert "append-only" in migration
    mutate = (BACKEND / "mutate_project_decision_loop.py").read_text()
    assert "_WRITER_ROLES" in mutate and "RESEARCH_ACTION_IDEMPOTENCY_CONFLICT" in mutate
    assert "metric_date cannot be in the future" in mutate
    assert "owner_actor must be an active project member" in mutate
    page = (BACKEND.parent / "DecisionLoop.tsx").read_text()
    assert "跨项目共享视频仅供阅读，不能在此建卡" in page
    assert "backend.mutate_project_decision_loop" in page
    assert "行动复盘" in (BACKEND.parent / "AppShell.tsx").read_text()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_decision_loop_requires_local_accepted_video_and_audits_outcomes(monkeypatch) -> None:
    assert DSN
    mutate = _load("mutate_project_decision_loop")
    read = _load("get_project_decision_loop")
    namespace = f"decision_loop_{uuid4().hex}"
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/028_project_decision_loop.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/028_project_decision_loop.sql").read_text(), prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('loop-org','闭环组织') returning id").fetchone()[0]
            project = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'loop-a','项目 A','active') returning id", (org,)).fetchone()[0]
            other = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'loop-b','项目 B','active') returning id", (org,)).fetchone()[0]
            conn.execute("insert into research_project_member(project_id,actor_id,role) values(%s,'writer@example.com','researcher'),(%s,'viewer@example.com','viewer'),(%s,'analyst@example.com','analyst'),(%s,'other@example.com','owner')", (project, project, project, other))
            accepted = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','8888888888888888888','本项目已接受') returning id").fetchone()[0]
            shared_only = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','9999999999999999999','别的项目公开参考') returning id").fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','local','accepted'),(%s,%s,'manual','other','accepted')", (project, accepted, other, shared_only))
            db = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1", "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user, "password": conninfo.get("password", ""), "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"), "options": f"-c search_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "writer@example.com")
            payload = {"source_video_id": str(accepted), "subject_id": None, "hypothesis": "短视频开头的真实细节会提升停留", "reference_point": "开头三秒先给现场细节", "adaptation_difference": "我方用自有场景，不复制原视频人物或台词", "owner_actor": "writer@example.com", "decision": "adopt"}
            create_key = str(uuid4())
            created = mutate.main(db, str(project), "create_card", payload, create_key)
            assert created["changed"] and created["status"] == "active"
            assert mutate.main(db, str(project), "create_card", payload, create_key)["idempotent_replay"] is True
            second = mutate.main(db, str(project), "create_card", {**payload, "hypothesis": "同一参考也可验证另一条策略假设"}, str(uuid4()))
            assert second["card_id"] != created["card_id"]
            bad = {**payload, "source_video_id": str(shared_only)}
            with pytest.raises(Exception):
                mutate.main(db, str(project), "create_card", bad, str(uuid4()))
            with pytest.raises(ValueError, match="active project member"):
                mutate.main(db, str(project), "create_card", {**payload, "owner_actor": "outside@example.com"}, str(uuid4()))
            conn.execute("update source_video set availability_status='unavailable' where id=%s", (accepted,))
            with pytest.raises(Exception):
                mutate.main(db, str(project), "create_card", {**payload, "hypothesis": "不可用公开源不可作为新卡来源"}, str(uuid4()))
            conn.execute("update source_video set availability_status='available' where id=%s", (accepted,))
            card_id = created["card_id"]
            with pytest.raises(ValueError, match="transition"):
                mutate.main(db, str(project), "set_card_status", {"card_id": card_id, "status": "observing"}, str(uuid4()))
            advanced = mutate.main(db, str(project), "set_card_status", {"card_id": card_id, "status": "adopted"}, str(uuid4()))
            assert advanced == {"changed": True, "card_id": card_id, "status": "adopted"}
            with pytest.raises(psycopg.Error, match="active project member"):
                conn.execute("update project_decision_card set owner_actor='outside@example.com' where id=%s", (card_id,))
            publication = mutate.main(db, str(project), "create_publication", {"decision_card_id": card_id, "publication_date": "2026-09-24", "title": "我方测试内容", "content_reference": "internal:content-001"}, str(uuid4()))
            metrics = {"impressions": 1000, "engagements": 80, "likes": 50, "comments": 10, "shares": 8, "follows": 2, "conversions": 1}
            evidence = {"source": "manual", "source_reference": "internal:daily-sheet-001", "source_reported_at": "2026-09-24T10:00:00+00:00", "source_version_or_digest": "manual-v1", "measurement_scope": "发布后24小时累计"}
            saved = mutate.main(db, str(project), "record_daily_metric", {"publication_id": publication["publication_id"], "metric_date": "2026-09-24", "metrics": metrics, **evidence}, str(uuid4()))
            assert saved["version"] == 1
            corrected = mutate.main(db, str(project), "record_daily_metric", {"publication_id": publication["publication_id"], "metric_date": "2026-09-24", "metrics": {**metrics, "impressions": 1100, "likes": None}, **{**evidence, "source_reference": "internal:daily-sheet-001-revision", "source_version_or_digest": "manual-v2", "measurement_scope": "发布后24小时累计，补录"}}, str(uuid4()))
            assert corrected["version"] == 2 and corrected["observation_id"] != saved["observation_id"]
            assert conn.execute("select likes from project_publication_metric_observation where id=%s", (corrected["observation_id"],)).fetchone()[0] is None
            with pytest.raises(psycopg.Error, match="append-only"):
                conn.execute("update project_publication_metric_observation set impressions=1 where id=%s", (saved["observation_id"],))
            with pytest.raises(ValueError, match="future"):
                mutate.main(db, str(project), "record_daily_metric", {"publication_id": publication["publication_id"], "metric_date": "2099-01-01", "metrics": metrics, **evidence}, str(uuid4()))
            with pytest.raises(psycopg.Error, match="exact local observation snapshot"):
                conn.execute(
                    """update project_decision_card set status='reviewed', review_conclusion='伪造',
                       review_evidence='伪造', next_action='伪造', reviewed_by='writer@example.com',
                       reviewed_at=now(), review_observation_id=%s, review_observation_version=999,
                       review_metric_snapshot='{}'::jsonb where id=%s""",
                    (saved["observation_id"], card_id),
                )
            conn.execute("delete from project_video_inclusion where project_id=%s and video_id=%s", (project, accepted))
            reviewed = mutate.main(db, str(project), "record_review", {"card_id": card_id, "observation_id": saved["observation_id"], "conclusion": "保留现场开头的做法，但缩短铺垫", "evidence": "首日汇总曝光1000、互动80、转化1", "next_action": "下周制作两条不同开头版本"}, str(uuid4()))
            assert reviewed["status"] == "reviewed"
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            result = read.main(db, str(project))
            reviewed_card = next(card for card in result["cards"] if card["id"] == card_id)
            assert reviewed_card["source_reference_withdrawn"] is True
            assert reviewed_card["review_conclusion"] == "保留现场开头的做法，但缩短铺垫"
            assert reviewed_card["review_observation_id"] == saved["observation_id"]
            assert reviewed_card["review_observation_version"] == 1
            assert reviewed_card["review_metric_snapshot"]["impressions"] == 1000
            assert reviewed_card["review_metric_snapshot"]["source_reference"] == "internal:daily-sheet-001"
            assert result["publications"][0]["daily_observations"][0]["impressions"] == 1100
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute("update project_decision_card set review_conclusion='篡改' where id=%s", (card_id,))
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute("delete from project_decision_card where id=%s", (card_id,))
            reloaded = read.main(db, str(project))
            restored_card = next(card for card in reloaded["cards"] if card["id"] == card_id)
            assert restored_card["review_observation_id"] == saved["observation_id"]
            assert restored_card["review_metric_snapshot"]["impressions"] == 1000
            assert {event["action"] for event in result["events"]} >= {"created", "status_changed", "reviewed"}
            monkeypatch.setenv("WM_END_USER_EMAIL", "writer@example.com")
            with pytest.raises(ValueError, match="immutable"):
                mutate.main(db, str(project), "update_card", {"card_id": card_id, "changes": {"hypothesis": "不应覆盖已复盘假设"}}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "other@example.com")
            with pytest.raises(PermissionError):
                read.main(db, str(project))
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            with pytest.raises(PermissionError):
                mutate.main(db, str(project), "set_card_status", {"card_id": card_id, "status": "adopted"}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "analyst@example.com")
            with pytest.raises(PermissionError):
                mutate.main(db, str(project), "set_card_status", {"card_id": card_id, "status": "adopted"}, str(uuid4()))
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
