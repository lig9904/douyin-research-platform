from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

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
    assert "核准档案版本" in page and "profile_id" in page
    assert "行动复盘" in (BACKEND.parent / "AppShell.tsx").read_text()
    preregistration = (ROOT / "db/migrations/033_project_experiment_contract.sql").read_text()
    assert "experiment contract is immutable" in preregistration
    assert "requires linked action and real platform provenance" in preregistration
    evidence = (ROOT / "db/migrations/035_project_decision_evidence.sql").read_text()
    assert "project_decision_card_evidence_ref" in evidence
    assert "requires locally accepted available video" in evidence
    assert "is append-only" in evidence


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
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/028_project_decision_loop.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/028_project_decision_loop.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/033_project_experiment_contract.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/033_project_experiment_contract.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/034_project_experiment_timeline.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/034_project_experiment_timeline.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/035_project_decision_evidence.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/035_project_decision_evidence.sql").read_text(), prepare=False)
            local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
            today = local_now.date()
            yesterday = today - timedelta(days=1)
            published_at = datetime.combine(yesterday, time(0, 1), ZoneInfo("Asia/Shanghai"))
            fixture_card_created_at = published_at - timedelta(days=1)
            org = conn.execute("insert into research_organization(slug,name) values('loop-org','闭环组织') returning id").fetchone()[0]
            project = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'loop-a','项目 A','active') returning id", (org,)).fetchone()[0]
            other = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'loop-b','项目 B','active') returning id", (org,)).fetchone()[0]
            conn.execute("insert into research_project_member(project_id,actor_id,role) values(%s,'writer@example.com','owner'),(%s,'viewer@example.com','viewer'),(%s,'analyst@example.com','analyst'),(%s,'other@example.com','owner'),(%s,'researcher@example.com','researcher')", (project, project, project, other, project))
            accepted = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','8888888888888888888','本项目已接受') returning id").fetchone()[0]
            shared_only = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','9999999999999999999','别的项目公开参考') returning id").fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','local','accepted'),(%s,%s,'manual','other','accepted')", (project, accepted, other, shared_only))
            # Model a 028 row carried forward into 033: it remains readable,
            # but cannot acquire a retrospective experiment verdict.
            conn.execute("alter table project_decision_card disable trigger trg_project_decision_card_experiment_contract")
            try:
                legacy = conn.execute(
                    """insert into project_decision_card
                       (project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                        owner_actor,decision,status,created_by)
                       values (%s,%s,'旧卡','旧依据','旧差异','writer@example.com','observe','observing',
                               'writer@example.com') returning id""",
                    (project, accepted),
                ).fetchone()[0]
            finally:
                conn.execute("alter table project_decision_card enable trigger trg_project_decision_card_experiment_contract")
            with pytest.raises(psycopg.Error, match="legacy project decision card"):
                conn.execute("update project_decision_card set status='reviewed' where id=%s", (legacy,))
            subject = conn.execute("insert into research_subject(project_id,name,subject_type) values(%s,'测试主体','ip') returning id", (project,)).fetchone()[0]
            profile = conn.execute(
                """insert into research_subject_profile_version
                   (project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest,content_fingerprint)
                   values (%s,%s,'ip_narrative',1,'{"current_facts":["测试主体已确认"]}'::jsonb,'cleared','internal:test-profile',%s,%s)
                   returning id""",
                (project, subject, "a" * 64, "b" * 64),
            ).fetchone()[0]
            conn.execute("update research_subject_profile_version set status='approved',approved_by='writer@example.com' where id=%s", (profile,))
            db = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1", "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user, "password": conninfo.get("password", ""), "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"), "options": f"-c search_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "writer@example.com")
            with pytest.raises(ValueError, match="legacy card"):
                mutate.main(db, str(project), "record_review", {"card_id": str(legacy), "observation_id": str(uuid4()), "verdict": "supported", "conclusion": "旧结论", "evidence": "旧依据", "next_action": "旧动作"}, str(uuid4()))
            payload = {"source_video_id": str(accepted), "subject_id": str(subject), "profile_id": str(profile), "hypothesis": "短视频开头的真实细节会提升停留", "reference_point": "开头三秒先给现场细节", "adaptation_difference": "我方用自有场景，不复制原视频人物或台词", "owner_actor": "writer@example.com", "decision": "adopt", "evaluation_metric": "首日新增关注", "success_rule": "比同账号同期基线至少提高 10%", "observation_window_days": 1, "comparison_basis": "同账号同类型内容最近 5 条", "confounder_plan": "记录投流、活动和发布时间差异"}
            create_key = str(uuid4())
            created = mutate.main(db, str(project), "create_card", payload, create_key)
            assert created["changed"] and created["status"] == "active"
            with pytest.raises(psycopg.Error, match="project_decision_card_experiment_contract_check"):
                conn.execute(
                    """insert into project_decision_card
                       (project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                        owner_actor,decision,status,created_by,evaluation_metric,success_rule,
                        observation_window_days,comparison_basis,confounder_plan)
                       values (%s,%s,'空白指标','参考','差异','writer@example.com','observe',
                               'observing','writer@example.com','   ','预设成功规则',2,'同期对照','记录干扰')""",
                    (project, accepted),
                )
            assert mutate.main(db, str(project), "create_card", payload, create_key)["idempotent_replay"] is True
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute("update project_decision_card set created_at=%s where id=%s", (fixture_card_created_at, created["card_id"]))
            assert conn.execute("select count(*) from project_decision_card_profile_binding where decision_card_id=%s", (created["card_id"],)).fetchone()[0] == 1
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute("update project_decision_card set success_rule='事后改规则' where id=%s", (created["card_id"],))
            with pytest.raises(ValueError, match="requires an approved"):
                mutate.main(db, str(project), "create_card", {**payload, "profile_id": None}, str(uuid4()))
            with pytest.raises(ValueError, match="current approved"):
                mutate.main(db, str(project), "create_card", {**payload, "profile_id": str(uuid4())}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "researcher@example.com")
            with pytest.raises(PermissionError, match="ADOPTION_DENIED"):
                mutate.main(db, str(project), "create_card", payload, str(uuid4()))
            observed = mutate.main(db, str(project), "create_card", {**payload, "subject_id": None, "profile_id": None, "decision": "observe", "owner_actor": "researcher@example.com"}, str(uuid4()))
            assert observed["status"] == "observing"
            excluded = mutate.main(db, str(project), "create_card", {**payload, "subject_id": None, "profile_id": None, "decision": "exclude"}, str(uuid4()))
            rejected_publication = {"decision_card_id": excluded["card_id"], "publication_date": today.isoformat(), "published_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "title": "不得发布的排除卡", "content_reference": "internal:rejected", "platform": "douyin", "account_reference": "public-account-001", "platform_content_id": "6666666666666666666", "content_version": "script-approved-v1", "distribution_mode": "organic"}
            with pytest.raises(ValueError, match="requires an active"):
                mutate.main(db, str(project), "create_publication", rejected_publication, str(uuid4()))
            with pytest.raises(psycopg.Error, match="requires active preregistered"):
                conn.execute(
                    """insert into project_publication_record
                       (project_id,decision_card_id,publication_date,published_at,title,content_reference,status,created_by,
                        platform,account_reference,platform_content_id,content_version,distribution_mode)
                       values (%s,%s,%s,now(),'不得发布的排除卡','internal:rejected','published',
                               'writer@example.com','douyin','public-account-001','6666666666666666666',
                               'script-approved-v1','organic')""",
                    (project, excluded["card_id"], today),
                )
            mutate.main(db, str(project), "set_card_status", {"card_id": observed["card_id"], "status": "archived"}, str(uuid4()))
            with pytest.raises(ValueError, match="requires an active"):
                mutate.main(db, str(project), "create_publication", {**rejected_publication, "decision_card_id": observed["card_id"]}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "writer@example.com")
            second = mutate.main(db, str(project), "create_card", {**payload, "hypothesis": "同一参考也可验证另一条策略假设"}, str(uuid4()))
            assert second["card_id"] != created["card_id"]
            another_accepted = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','8888888888888888887','另一条已接受视频') returning id").fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','local','accepted')", (project, another_accepted))
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute("update project_decision_card set source_video_id=%s where id=%s", (another_accepted, created["card_id"]))
            counterexample = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','8888888888888888886','低效反例') returning id").fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','local','accepted')", (project, counterexample))
            refs = [
                {"video_id": str(another_accepted), "role": "comparable", "reason": "同类开头，比较节奏"},
                {"video_id": str(counterexample), "role": "counterexample", "reason": "铺垫过长，作为反例"},
            ]
            refs_payload = {**payload, "hypothesis": "多案例与反例形成对照", "evidence_refs": refs}
            refs_key = str(uuid4())
            ref_card = mutate.main(db, str(project), "create_card", refs_payload, refs_key)
            assert mutate.main(db, str(project), "create_card", refs_payload, refs_key)["idempotent_replay"] is True
            assert conn.execute("select count(*) from project_decision_card_evidence_ref where decision_card_id=%s", (ref_card["card_id"],)).fetchone()[0] == 2
            assert "evidence_refs" in conn.execute("select changed_fields from project_decision_card_event where decision_card_id=%s and action='created'", (ref_card["card_id"],)).fetchone()[0]
            with pytest.raises(psycopg.Error, match="must be bound when the card is created"):
                conn.execute(
                    """insert into project_decision_card_evidence_ref
                       (project_id,decision_card_id,position,video_id,role,reason)
                       values (%s,%s,1,%s,'counterexample','事后追加的反例')""",
                    (project, created["card_id"], another_accepted),
                )
            for bad_refs, error in [
                ([refs[0], refs[0]], "unique"),
                ([{**refs[0], "video_id": str(accepted)}], "primary video"),
                ([{**refs[0], "video_id": str(shared_only)}], "MUTATION_UNAVAILABLE"),
                ([{**refs[0], "role": "primary"}], "role is invalid"),
                (refs * 7, "at most 12"),
            ]:
                with pytest.raises((ValueError, RuntimeError), match=error):
                    mutate.main(db, str(project), "create_card", {**refs_payload, "evidence_refs": bad_refs}, str(uuid4()))
            def assert_direct_evidence_rejected(ref_video, expected):
                conn.execute("begin")
                try:
                    fresh_card = conn.execute(
                        """insert into project_decision_card
                           (project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                            owner_actor,decision,status,created_by,evaluation_metric,success_rule,
                            observation_window_days,comparison_basis,confounder_plan)
                           values (%s,%s,'数据库直接建卡','参考','差异','writer@example.com','observe',
                                   'observing','writer@example.com','新增关注','高于基线',7,'同类','无')
                           returning id""",
                        (project, accepted),
                    ).fetchone()[0]
                    with pytest.raises(psycopg.Error, match=expected):
                        conn.execute(
                            """insert into project_decision_card_evidence_ref
                               (project_id,decision_card_id,position,video_id,role,reason)
                               values (%s,%s,1,%s,'comparable','直接 SQL 绕过')""",
                            (project, fresh_card, ref_video),
                        )
                finally:
                    conn.execute("rollback")

            assert_direct_evidence_rejected(shared_only, "requires locally accepted available video")
            assert_direct_evidence_rejected(accepted, "cannot repeat primary video")
            with pytest.raises(psycopg.Error, match="append-only"):
                conn.execute("update project_decision_card_evidence_ref set reason='篡改' where decision_card_id=%s", (ref_card["card_id"],))
            with pytest.raises(psycopg.Error, match="append-only"):
                conn.execute("delete from project_decision_card_evidence_ref where decision_card_id=%s", (ref_card["card_id"],))
            conn.execute("update source_video set availability_status='unavailable' where id=%s", (another_accepted,))
            with pytest.raises(RuntimeError, match="MUTATION_UNAVAILABLE"):
                mutate.main(db, str(project), "create_card", {**refs_payload, "hypothesis": "不可用对照不能新建"}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            ref_read = next(card for card in read.main(db, str(project))["cards"] if card["id"] == ref_card["card_id"])
            assert [item["role"] for item in ref_read["evidence_refs"]] == ["comparable", "counterexample"]
            assert ref_read["evidence_refs"][0]["video_title"] == "另一条已接受视频"
            assert ref_read["evidence_refs"][0]["reference_withdrawn"] is True
            assert ref_read["evidence_refs"][1]["reference_withdrawn"] is False
            monkeypatch.setenv("WM_END_USER_EMAIL", "writer@example.com")
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
            publication_payload = {"decision_card_id": card_id, "publication_date": yesterday.isoformat(), "published_at": published_at.isoformat(), "title": "我方测试内容", "content_reference": "internal:content-001", "platform": "douyin", "account_reference": "public-account-001", "platform_content_id": "7777777777777777777", "content_version": "script-approved-v1", "distribution_mode": "organic"}
            with pytest.raises(ValueError, match="follow preregistration"):
                mutate.main(db, str(project), "create_publication", publication_payload, str(uuid4()))
            with pytest.raises(psycopg.Error, match="must follow preregistration"):
                conn.execute(
                    """insert into project_publication_record
                       (project_id,decision_card_id,publication_date,published_at,title,content_reference,
                        status,created_by,platform,account_reference,platform_content_id,content_version,distribution_mode)
                       values (%s,%s,%s,%s,'倒填发布','internal:backfill','published','writer@example.com',
                               'douyin','public-account-001','7777777777777777779','script-approved-v1','organic')""",
                    (project, card_id, yesterday, published_at),
                )
            # Integration clock fixture: emulate a card genuinely created before
            # yesterday's publication, without weakening the production trigger.
            conn.execute("alter table project_decision_card disable trigger trg_project_decision_card_experiment_contract")
            try:
                conn.execute("update project_decision_card set created_at=%s where id=%s", (fixture_card_created_at, card_id))
            finally:
                conn.execute("alter table project_decision_card enable trigger trg_project_decision_card_experiment_contract")
            publication = mutate.main(db, str(project), "create_publication", publication_payload, str(uuid4()))
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute("update project_publication_record set content_version='v2' where id=%s", (publication["publication_id"],))
            with pytest.raises(RuntimeError, match="MUTATION_UNAVAILABLE"):
                mutate.main(db, str(project), "create_publication", publication_payload, str(uuid4()))
            metrics = {"impressions": 1000, "engagements": 80, "likes": 50, "comments": 10, "shares": 8, "follows": 2, "conversions": 1}
            evidence = {"source": "manual", "source_reference": "internal:daily-sheet-001", "source_reported_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "source_version_or_digest": "manual-v1", "measurement_scope": "发布后24小时累计"}
            saved = mutate.main(db, str(project), "record_daily_metric", {"publication_id": publication["publication_id"], "metric_date": today.isoformat(), "metrics": metrics, **evidence}, str(uuid4()))
            assert saved["version"] == 1
            incomplete_report = mutate.main(db, str(project), "record_daily_metric", {"publication_id": publication["publication_id"], "metric_date": today.isoformat(), "metrics": metrics, **{**evidence, "source_reference": "internal:early-report", "source_reported_at": (published_at + timedelta(hours=12)).isoformat()}}, str(uuid4()))
            with pytest.raises(ValueError, match="observation window is not complete"):
                mutate.main(db, str(project), "record_review", {"card_id": card_id, "observation_id": incomplete_report["observation_id"], "verdict": "supported", "conclusion": "过早结论", "evidence": "观察窗未满", "next_action": "等待"}, str(uuid4()))
            windowed = mutate.main(db, str(project), "create_card", {**payload, "hypothesis": "至少观察两天才能复盘", "observation_window_days": 2}, str(uuid4()))
            conn.execute("alter table project_decision_card disable trigger trg_project_decision_card_experiment_contract")
            try:
                conn.execute("update project_decision_card set created_at=%s where id=%s", (fixture_card_created_at, windowed["card_id"]))
            finally:
                conn.execute("alter table project_decision_card enable trigger trg_project_decision_card_experiment_contract")
            windowed_publication = mutate.main(db, str(project), "create_publication", {**publication_payload, "decision_card_id": windowed["card_id"], "platform_content_id": "7777777777777777778"}, str(uuid4()))
            early_metric = mutate.main(db, str(project), "record_daily_metric", {"publication_id": windowed_publication["publication_id"], "metric_date": today.isoformat(), "metrics": metrics, **evidence}, str(uuid4()))
            with pytest.raises(ValueError, match="observation window is not complete"):
                mutate.main(db, str(project), "record_review", {"card_id": windowed["card_id"], "observation_id": early_metric["observation_id"], "verdict": "supported", "conclusion": "提前宣称成功", "evidence": "只有第一天数据", "next_action": "等待观察"}, str(uuid4()))
            corrected = mutate.main(db, str(project), "record_daily_metric", {"publication_id": publication["publication_id"], "metric_date": today.isoformat(), "metrics": {**metrics, "impressions": 1100, "likes": None}, **{**evidence, "source_reference": "internal:daily-sheet-001-revision", "source_version_or_digest": "manual-v2", "measurement_scope": "发布后24小时累计，补录"}}, str(uuid4()))
            assert corrected["version"] == 3 and corrected["observation_id"] != saved["observation_id"]
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
            assert conn.execute("select count(*) from research_project_member where project_id=%s and actor_id='writer@example.com' and status='active'", (project,)).fetchone()[0] == 1
            reviewed = mutate.main(db, str(project), "record_review", {"card_id": card_id, "observation_id": saved["observation_id"], "verdict": "inconclusive", "conclusion": "保留现场开头的做法，但缩短铺垫", "evidence": "首日汇总曝光1000、互动80、转化1；缺同账号对照", "next_action": "下周制作两条不同开头版本"}, str(uuid4()))
            assert reviewed["status"] == "reviewed"
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            result = read.main(db, str(project))
            legacy_read = next(card for card in result["cards"] if card["id"] == str(legacy))
            assert legacy_read["evaluation_metric"] is None and legacy_read["review_verdict"] is None
            reviewed_card = next(card for card in result["cards"] if card["id"] == card_id)
            assert reviewed_card["source_reference_withdrawn"] is True
            assert reviewed_card["review_conclusion"] == "保留现场开头的做法，但缩短铺垫"
            assert reviewed_card["review_verdict"] == "inconclusive"
            assert reviewed_card["review_observation_id"] == saved["observation_id"]
            assert reviewed_card["review_observation_version"] == 1
            assert reviewed_card["review_metric_snapshot"]["impressions"] == 1000
            assert reviewed_card["review_metric_snapshot"]["source_reference"] == "internal:daily-sheet-001"
            assert reviewed_card["profile_binding"]["profile_version_no"] == 1
            assert reviewed_card["profile_binding"]["profile_current_status"] == "approved"
            assert reviewed_card["evaluation_metric"] == "首日新增关注"
            assert len(result["approved_profiles"]) == 1
            assert result["approved_profiles"][0]["summary"]["current_facts"] == ["测试主体已确认"]
            assert not ({"source_reference", "source_digest", "approved_by"} & result["approved_profiles"][0].keys())
            assert not ({"profile_source_digest", "profile_content_fingerprint", "bound_by"} & reviewed_card["profile_binding"].keys())
            conn.execute("update research_subject_profile_version set status='revoked' where id=%s", (profile,))
            assert next(card for card in read.main(db, str(project))["cards"] if card["id"] == card_id)["profile_binding"]["profile_current_status"] == "revoked"
            reviewed_publication = next(
                item for item in result["publications"]
                if item["id"] == publication["publication_id"]
            )
            assert reviewed_publication["daily_observations"][0]["impressions"] == 1100
            assert reviewed_publication["platform_content_id"] == "7777777777777777777"
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
            conn.execute("update research_project_member set status='revoked' where project_id=%s and actor_id='viewer@example.com'", (project,))
            with pytest.raises(PermissionError):
                read.main(db, str(project))
            monkeypatch.setenv("WM_END_USER_EMAIL", "analyst@example.com")
            with pytest.raises(PermissionError):
                mutate.main(db, str(project), "set_card_status", {"card_id": card_id, "status": "adopted"}, str(uuid4()))
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_033_upgrade_keeps_legacy_cards_readable_without_backfilling_goals() -> None:
    assert DSN
    namespace = f"legacy_loop_{uuid4().hex}"
    bootstrap_032 = (ROOT / "db/schema.sql").read_text().split(
        "-- Fresh-volume bootstrap parity with migration 033.", 1
    )[0]
    migration = (ROOT / "db/migrations/033_project_experiment_contract.sql").read_text()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(bootstrap_032, prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('legacy-org','旧组织') returning id").fetchone()[0]
            project = conn.execute("insert into research_project(organization_id,slug,name) values(%s,'legacy-project','旧项目') returning id", (org,)).fetchone()[0]
            conn.execute("insert into research_project_member(project_id,actor_id,role) values(%s,'writer@example.com','owner')", (project,))
            video = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','8888888888888888881','旧公开视频') returning id").fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','legacy','accepted')", (project, video))
            old_card = conn.execute(
                """insert into project_decision_card
                   (project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                    owner_actor,decision,status,created_by)
                   values (%s,%s,'既有假设','既有参考','既有差异','writer@example.com','observe',
                           'observing','writer@example.com') returning id""",
                (project, video),
            ).fetchone()[0]
            conn.execute(migration, prepare=False)
            conn.execute(migration, prepare=False)
            timeline = (ROOT / "db/migrations/034_project_experiment_timeline.sql").read_text()
            conn.execute(timeline, prepare=False)
            conn.execute(timeline, prepare=False)
            assert conn.execute(
                "select status,evaluation_metric,success_rule,review_verdict from project_decision_card where id=%s",
                (old_card,),
            ).fetchone() == ("observing", None, None, None)
            with pytest.raises(psycopg.Error, match="legacy project decision card"):
                conn.execute("update project_decision_card set status='reviewed' where id=%s", (old_card,))
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
