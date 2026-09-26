from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/manage_project_case_review.py"
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location("project_case_review_test", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload(video_key: str, **changes):
    value = {
        "status": "partial",
        "source_reference": f"https://www.douyin.com/video/{video_key}",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "video_coverage": "partial",
        "audio_coverage": "partial",
        "audio_not_applicable_reason": None,
        "key_event_complete": None,
        "verified_facts": "已核对原帖开头内容",
        "evidence_gaps": "尚未看完",
        "counterevidence": "",
        "comparability_note": "",
    }
    value.update(changes)
    return value


def test_validation_and_schema_contract(monkeypatch) -> None:
    module = _load()
    key = "1234567890123456789"
    assert module._normalize(_payload(key))["status"] == "partial"
    with pytest.raises(ValueError, match="missing key event"):
        module._normalize(_payload(key, key_event_complete=False))
    assert module._normalize(_payload(key, status="insufficient", key_event_complete=False))["status"] == "insufficient"
    with pytest.raises(ValueError, match="complete requires"):
        module._normalize(_payload(key, status="complete", video_coverage="complete", audio_coverage="complete",
                                   key_event_complete=True))
    full = _payload(key, status="complete", video_coverage="complete", audio_coverage="complete",
                    key_event_complete=True, comparability_note="对象、场景、时长可比")
    assert module._normalize(full)["status"] == "complete"
    with pytest.raises(ValueError, match="specific no-audio"):
        module._normalize({**full, "audio_coverage": "not_applicable", "audio_not_applicable_reason": "没听到声音所以不需要"})
    assert module._normalize({**full, "audio_coverage": "not_applicable",
                              "audio_not_applicable_reason": "原片确实无声，本次比较只根据画面事实"})["status"] == "complete"
    with pytest.raises(ValueError, match="source_reference"):
        module._normalize(_payload(key, source_reference="http://www.douyin.com/video/1234567890123456789"))
    with pytest.raises(ValueError, match="source_reference"):
        module._normalize(_payload(key, source_reference=f"https://www.douyin.com/video/{key};other"))
    for platform, valid, invalid in [
        ("douyin", f"https://www.douyin.com/video/{key}", f"https://www.douyin.com/video/{key}0"),
        ("xiaohongshu", f"https://www.xiaohongshu.com/explore/{key}", "https://www.xiaohongshu.com/explore/wrong"),
        ("kuaishou", f"https://www.kuaishou.com/short-video/{key}", "https://www.kuaishou.com/short-video/wrong"),
        ("bilibili", f"https://www.bilibili.com/video/{key}", "https://www.bilibili.com/video/wrong"),
    ]:
        source = {"platform": platform, "platform_video_id": key, "source_url": None}
        assert module._source_reference_matches(valid, source)
        assert not module._source_reference_matches(invalid, source)
        assert not module._source_reference_matches(f"https://evil.example/video/{key}", source)
        assert not module._source_reference_matches(f"https://{next(iter(module._PLATFORM_HOSTS[platform]))}/profile/{key}", source)
        profile = f"https://{next(iter(module._PLATFORM_HOSTS[platform]))}/user/profile/other"
        assert not module._source_reference_matches(profile, {**source, "source_url": profile})
    assert module._normalize(_payload(key, status="insufficient", video_coverage="none", audio_coverage="none",
                                     verified_facts=""))["verified_facts"] == ""
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(PermissionError):
        module._actor()
    migration = (ROOT / "db/migrations/037_project_case_review.sql").read_text()
    schema = (ROOT / "db/schema.sql").read_text()
    for script in (migration, schema):
        assert "create table if not exists project_video_case_review" in script
        assert "project_video_case_review is append-only" in script
        assert script.count("requires current complete project case review") >= 2
    assert "references project_video_inclusion" not in migration
    assert BACKEND.with_suffix(".yaml").read_text().startswith("type: inline\nfields:\n  db:\n    type: static\n")
    release = (ROOT / "scripts/test-server-release.sh").read_text()
    assert "verify_project_case_review_contract" in release
    assert "project_case_review_037" in release
    assert "case_review_source_counts" in release


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_037_release_verifier_detects_disabled_append_only_trigger() -> None:
    assert DSN
    source = (ROOT / "scripts/test-server-release.sh").read_text()
    section = source.split("verify_project_case_review_contract() {", 1)[1].split("\n}\n", 1)[0]
    query = section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]
    namespace = f"case_release_{uuid4().hex}"
    query = query.replace("public.", f"{namespace}.").replace("schemaname='public'", f"schemaname='{namespace}'").replace("nspname='public'", f"nspname='{namespace}'")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(), prepare=False)
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("alter table project_video_case_review disable trigger trg_project_video_case_review_append_only")
            assert "037.append_only" in conn.execute(query).fetchone()[0]
            conn.execute("alter table project_publication_record disable trigger trg_project_publication_current_evidence")
            assert "037.publication_gate" in conn.execute(query).fetchone()[0]
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_037_upgrade_preserves_legacy_card_and_gates_only_new_cards() -> None:
    assert DSN
    namespace = f"case_upgrade_{uuid4().hex}"
    baseline_036 = (ROOT / "db/schema.sql").read_text().split(
        "-- Fresh-volume bootstrap parity with migration 037.", 1
    )[0]
    migration = (ROOT / "db/migrations/037_project_case_review.sql").read_text()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(baseline_036, prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('upgrade-org','升级组织') returning id").fetchone()[0]
            project = conn.execute("insert into research_project(organization_id,slug,name) values(%s,'upgrade-project','升级项目') returning id", (org,)).fetchone()[0]
            conn.execute("insert into research_project_member(project_id,actor_id,role) values(%s,'owner@example.com','owner')", (project,))
            video = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','1234567890123456789','旧公开样本') returning id").fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,status) values(%s,%s,'manual','accepted')", (project, video))
            card_sql = """insert into project_decision_card
                (project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                 owner_actor,decision,status,created_by,evaluation_metric,success_rule,
                 observation_window_days,comparison_basis,confounder_plan)
                values (%s,%s,'假设','参考','差异','owner@example.com','observe','observing',
                        'owner@example.com','关注','高于基线',7,'同期内容','活动变化') returning id"""
            old_card = conn.execute(card_sql, (project, video)).fetchone()[0]
            conn.execute(migration, prepare=False)
            conn.execute(migration, prepare=False)
            assert conn.execute("select id from project_decision_card where id=%s", (old_card,)).fetchone()[0] == old_card
            with pytest.raises(psycopg.Error, match="requires current complete project case review"):
                conn.execute(card_sql, (project, video))
            review_id = conn.execute("""insert into project_video_case_review
                (project_id,video_id,version_no,status,source_reference,observed_at,video_coverage,
                 audio_coverage,key_event_complete,verified_facts,comparability_note,reviewed_by)
                values (%s,%s,1,'complete','https://www.douyin.com/video/1234567890123456789',
                        now(),'complete','complete',true,'完整画面及声音已核对','场景和受众可比','owner@example.com')
                returning id""", (project, video)).fetchone()[0]
            new_card = conn.execute(card_sql, (project, video)).fetchone()[0]
            assert new_card != old_card
            assert conn.execute("select source_case_review_id from project_decision_card where id=%s", (new_card,)).fetchone()[0] == review_id
            with pytest.raises(psycopg.Error, match="bound project case review is immutable"):
                conn.execute("update project_decision_card set source_case_review_id=null where id=%s", (new_card,))
            conn.execute("""insert into project_video_case_review
                (project_id,video_id,version_no,status,source_reference,observed_at,video_coverage,
                 audio_coverage,key_event_complete,verified_facts,comparability_note,reviewed_by)
                values (%s,%s,2,'insufficient','https://www.douyin.com/video/1234567890123456789',
                        now(),'partial','none',false,'','此前完整性判断已被推翻','owner@example.com')""",
                (project, video))
            with pytest.raises(psycopg.Error, match="requires current complete project case review"):
                conn.execute(card_sql, (project, video))
            assert conn.execute("select source_case_review_id from project_decision_card where id=%s", (new_card,)).fetchone()[0] == review_id
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_append_only_versions_acl_and_decision_gate(monkeypatch) -> None:
    assert DSN
    module = _load()
    namespace = f"case_review_{uuid4().hex}"
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(), prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('case-org','Case 组织') returning id").fetchone()[0]
            project_a, project_b = [conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,%s,%s,'active') returning id",
                (org, slug, name)).fetchone()[0] for slug, name in (("case-a", "A"), ("case-b", "B"))]
            conn.execute("""insert into research_project_member(project_id,actor_id,role) values
                         (%s,'researcher-a@example.com','researcher'),
                         (%s,'viewer-a@example.com','viewer'),
                         (%s,'owner-b@example.com','owner')""", (project_a, project_a, project_b))
            key = "1234567890123456789"
            video = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin',%s,'Case 原帖') returning id", (key,)).fetchone()[0]
            conn.execute("""insert into project_video_inclusion(project_id,video_id,source_type,status)
                            values (%s,%s,'manual','accepted')""", (project_a, video))
            db = {"host": conninfo.get("host") or conn.info.host or "127.0.0.1",
                  "port": int(conninfo.get("port") or conn.info.port or 5432),
                  "user": conninfo.get("user") or conn.info.user,
                  "password": conninfo.get("password", ""),
                  "dbname": conninfo.get("dbname") or conn.info.dbname,
                  "sslmode": conninfo.get("sslmode", "prefer"),
                  "options": f"-c search_path={namespace}"}
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer-a@example.com")
            assert module.main(db, str(project_a), str(video), "latest") == {"review": None}
            with pytest.raises(PermissionError):
                module.main(db, str(project_a), str(video), "create", _payload(key), str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner-b@example.com")
            with pytest.raises(PermissionError):
                module.main(db, str(project_a), str(video), "latest")
            monkeypatch.setenv("WM_END_USER_EMAIL", "researcher-a@example.com")
            assert module.main(db, str(project_a), str(video), "latest") == {"review": None}
            first_key = str(uuid4())
            first_payload = _payload(key)
            first = module.main(db, str(project_a), str(video), "create", first_payload, first_key)
            assert first["version_no"] == 1 and first["status"] == "partial"
            assert module.main(db, str(project_a), str(video), "create", first_payload, first_key)["idempotent_replay"]
            complete = _payload(key, status="complete", video_coverage="complete", audio_coverage="complete",
                                key_event_complete=True, comparability_note="对象和条件可比")
            second_key = str(uuid4())
            second = module.main(db, str(project_a), str(video), "create", complete, second_key)
            assert second["version_no"] == 2
            assert module.main(db, str(project_a), str(video), "latest")["review"]["status"] == "complete"
            xhs_key = "64abc123def4567890abc123"
            xhs_video = conn.execute("insert into source_video(platform,platform_video_id,title) values('xiaohongshu',%s,'小红书原帖') returning id", (xhs_key,)).fetchone()[0]
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,status) values(%s,%s,'manual','accepted')", (project_a, xhs_video))
            xhs_payload = _payload(xhs_key, source_reference=f"https://www.xiaohongshu.com/explore/{xhs_key}")
            assert module.main(db, str(project_a), str(xhs_video), "create", xhs_payload, str(uuid4()))["version_no"] == 1
            with pytest.raises(ValueError, match="source_reference does not match"):
                module.main(db, str(project_a), str(xhs_video), "create", _payload(xhs_key), str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer-a@example.com")
            read = module.main(db, str(project_a), str(video), "latest")["review"]
            assert read["reviewed_by"] == "researcher-a@example.com"
            with pytest.raises(psycopg.Error, match="append-only"):
                conn.execute("update project_video_case_review set status='partial' where id=%s", (second["review_id"],))
            conn.execute("delete from project_video_inclusion where project_id=%s and video_id=%s", (project_a, video))
            assert conn.execute("select count(*) from project_video_case_review where project_id=%s and video_id=%s", (project_a, video)).fetchone()[0] == 2
            monkeypatch.setenv("WM_END_USER_EMAIL", "researcher-a@example.com")
            assert module.main(db, str(project_a), str(video), "create", complete, second_key)["idempotent_replay"] is True
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
