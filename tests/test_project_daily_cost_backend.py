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
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/get_project_daily_cost.py"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location("project_daily_cost_backend", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_project_daily_cost_contract_is_project_scoped_and_keeps_unknown_amounts_unknown() -> None:
    source = BACKEND.read_text(encoding="utf-8")
    assert "project_research_task_cost" in source
    assert "project_actor_can_read" in source
    assert "with authorized_project as materialized" in source
    assert "cost.project_id" in source
    assert "unknown_task_count" in source
    assert "cost_status" in source
    assert "Asia/Shanghai" in source
    assert "supplier_daily_spend" not in source
    assert "coalesce(sum(cost.total_cost)" not in source
    assert BACKEND.with_suffix(".yaml").read_text(encoding="utf-8") == (
        "type: inline\nfields:\n  db:\n    type: static\n"
        "    value: $res:f/content_research/research_db\n"
    )


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_daily_cost_aggregates_known_subtotals_without_cross_project_fallback(monkeypatch) -> None:
    assert DSN
    module = _load()
    namespace = f"project_daily_cost_{uuid4().hex}"
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values ('cost-org','费用组织') returning id"
            ).fetchone()[0]
            project_a, project_b = [
                conn.execute(
                    """insert into research_project(organization_id,slug,name,status)
                       values (%s,%s,%s,'active') returning id""",
                    (org, slug, name),
                ).fetchone()[0]
                for slug, name in (("cost-a", "项目 A"), ("cost-b", "项目 B"))
            ]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status) values
                   (%s,'reader-a@example.com','viewer','active'),
                   (%s,'reader-b@example.com','viewer','active')""",
                (project_a, project_b),
            )
            video_a, video_b = [
                conn.execute(
                    """insert into source_video(platform,platform_video_id,title)
                       values ('douyin',%s,'费用测试公开视频') returning id""",
                    (f"cost-video-{suffix}",),
                ).fetchone()[0]
                for suffix in ("a", "b")
            ]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status) values
                   (%s,%s,'manual','accepted'), (%s,%s,'manual','accepted')""",
                (project_a, video_a, project_b, video_b),
            )

            def add_cost(project_id, video_id, suffix, task_type, basis, api, asr, llm, currency="USD"):
                conn.execute(
                    """insert into project_research_task_cost(
                         project_id,video_id,task_key,task_type,task_version,status,input_fingerprint,
                         api_cost,asr_cost,llm_cost,cost_currency,cost_basis
                       ) values (%s,%s,%s,%s,'test-v1','completed',%s,%s,%s,%s,%s,%s)""",
                    (project_id, video_id, f"daily-cost-{suffix}", task_type, "a" * 64, api, asr, llm, currency, basis),
                )

            add_cost(project_a, video_a, "actual", "asr_transcription", "actual", "0.2", "0.3", "0.5")
            add_cost(project_a, video_a, "estimated", "l3_structured_research", "estimated", "1", "0", "0")
            add_cost(project_a, video_a, "mixed", "l3_structured_research", "mixed", "0.5", "0", "0")
            add_cost(project_a, video_a, "unknown", "asr_transcription", "unknown", None, None, None)
            add_cost(project_a, video_a, "cny", "asr_transcription", "actual", "0.3", "0", "0", "CNY")
            add_cost(project_b, video_b, "other-project", "l3_structured_research", "actual", "99", "0", "0")

            db = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1",
                "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user,
                "password": conninfo.get("password", ""),
                "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"),
                "options": f"-c search_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "reader-a@example.com")
            result = module.main(db, str(project_a), days=1)
            assert result["project_id"] == str(project_a)
            assert result["project_name"] == "项目 A"
            assert result["role"] == "viewer"
            assert result["report_timezone"] == "Asia/Shanghai"
            assert result["days"] == 1
            assert len(result["records"]) == 2
            day = next(record for record in result["records"] if record["cost_currency"] == "USD")
            assert day == {
                **day,
                "cost_currency": "USD",
                "task_count": 4,
                "asr_task_count": 2,
                "l3_task_count": 2,
                "known_amount": 2.5,
                "actual_amount": 1.0,
                "estimated_amount": 1.0,
                "mixed_amount": 0.5,
                "unknown_task_count": 1,
                "cost_status": "partial",
            }
            assert "unknown_amount" not in day
            assert 99 not in day.values()
            cny = next(record for record in result["records"] if record["cost_currency"] == "CNY")
            assert cny["task_count"] == 1
            assert cny["known_amount"] == 0.3
            assert cny["actual_amount"] == 0.3
            assert cny["estimated_amount"] is None
            assert cny["mixed_amount"] is None
            assert cny["unknown_task_count"] == 0
            assert cny["cost_status"] == "complete"

            monkeypatch.setenv("WM_END_USER_EMAIL", "reader-b@example.com")
            with pytest.raises(PermissionError, match="RESEARCH_PROJECT_ACCESS_DENIED"):
                module.main(db, str(project_a), days=1)
            monkeypatch.setenv("WM_END_USER_EMAIL", "reader-a@example.com")
            with pytest.raises(ValueError, match="days"):
                module.main(db, str(project_a), days=0)
            with pytest.raises(ValueError, match="days"):
                module.main(db, str(project_a), days=True)
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
