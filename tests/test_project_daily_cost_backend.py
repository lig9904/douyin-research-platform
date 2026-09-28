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
    assert "project_las_analysis_receipt" in source
    assert "project_las_supplier_bill_item" in source
    assert "run.run_type in ('l0l1_discovery','comment_collection'," in source
    assert "'account_profile_refresh')" in source
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
                       (%s,'reader-b@example.com','viewer','active'),
                       (%s,'reviewer@example.com','owner','active')""",
                (project_a, project_b, project_a),
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
            add_cost(project_a, video_a, "unknown-number", "asr_transcription", "unknown", "9", "0", "0")
            add_cost(project_a, video_a, "cny", "asr_transcription", "actual", "0.3", "0", "0", "CNY")
            add_cost(project_b, video_b, "other-project", "l3_structured_research", "actual", "99", "0", "0")
            conn.execute(
                """insert into pipeline_run(run_type,project_id,status,api_cost,cost_currency,summary)
                   values
                   ('l0l1_discovery',%s,'success',0.002,'USD',
                    '{"api_cost_basis":"estimated","unknown_cost_calls":0}'::jsonb),
                   ('l0l1_discovery',%s,'success',0.001,'USD',
                    '{"api_cost_basis":"unknown","unknown_cost_calls":1}'::jsonb),
                   ('comment_collection',%s,'success',0.002,'USD',
                    '{"api_cost_basis":"estimated","unknown_cost_calls":0}'::jsonb),
                   ('account_profile_refresh',%s,'success',0.001,'USD',
                    '{"api_cost_basis":"estimated","unknown_cost_calls":0}'::jsonb),
                   ('video_statistics_refresh',%s,'success',0.001,'USD',
                    '{"api_cost_basis":"estimated","unknown_cost_calls":0}'::jsonb),
                   ('media_ingestion',%s,'success',100,'USD',
                    '{"api_cost_basis":"estimated"}'::jsonb),
                   ('l0l1_discovery',%s,'success',99,'USD',
                    '{"api_cost_basis":"actual","unknown_cost_calls":0}'::jsonb)""",
                (project_a, project_a, project_a, project_a, project_a, project_a, project_b),
            )
            # Real failed runs retain the pipeline_run default CNY, which must
            # not be presented as a known CNY charge for a USD provider.
            conn.execute(
                """insert into pipeline_run(run_type,project_id,status,api_cost,summary)
                   values ('l0l1_discovery',%s,'failed',0,'{}'::jsonb)""",
                (project_a,),
            )
            conn.execute(
                """insert into pipeline_run(
                     run_type,project_id,status,api_cost,cost_currency,summary)
                   values ('account_profile_refresh',%s,'failed',0,'USD',
                     '{"api_cost_basis":"unknown","unknown_cost_calls":1,
                       "known_estimated_cost_usd":0.001}'::jsonb)""",
                (project_a,),
            )
            conn.execute(
                """insert into pipeline_run(
                     run_type,project_id,status,cost_currency,summary)
                   values ('video_statistics_refresh',%s,'failed','USD',
                     '{"api_cost_basis":"estimated","unknown_cost_calls":0,
                       "known_estimated_cost_usd":0.001}'::jsonb)""",
                (project_a,),
            )
            asset = conn.execute("""insert into media_asset(video_id,kind,storage_location,bucket,
                object_key,content_sha256,size_bytes,content_type)
                values(%s,'audio','s3','test',%s,%s,64,'audio/wav') returning id""",
                (video_a, "sha256/aa/" + "a" * 64, "a" * 64)).fetchone()[0]
            review = conn.execute("""insert into project_asr_media_review(project_id,video_id,asset_id,
                review_version,media_fingerprint,asset_manifest_fingerprint,delivery_origin,
                identity_source,review_statement)
                values(%s,%s,%s,'v1',%s,%s,'test','windmill_end_user_email_allowlist_v1','{}')
                returning id""", (project_a, video_a, asset, "a" * 64, "b" * 64)).fetchone()[0]
            conn.execute("update project_asr_media_review set status='approved',reviewed_by='reviewer@example.com' where id=%s", (review,))
            conn.execute("""insert into project_asr_execution_job(task_key,project_id,video_id,
                media_review_id,reviewed_asset_id,review_version,media_fingerprint,
                asset_manifest_fingerprint,provider,model_id,model_revision,engine_version,
                source_fingerprint,status,provider_task_ref,submission_count,cost_currency)
                values(%s,%s,%s,%s,%s,'v1',%s,%s,'test','test','1','wav-v1',%s,
                'running','provider-ref',1,'USD')""",
                ("daily-unbilled-" + uuid4().hex, project_a, video_a, review, asset,
                 "a" * 64, "b" * 64, "a" * 64))
            video_asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,
                     object_key,content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','test',%s,%s,128,'video/mp4') returning id""",
                (video_a, "sha256/cc/" + "c" * 64, "c" * 64),
            ).fetchone()[0]
            receipt_ids = []
            for suffix, estimate in (("pending", "0.250000"), ("billed", "0.170000"),
                                     ("no-estimate", None)):
                attempt_id = conn.execute(
                    """insert into project_las_analysis_attempt(
                         project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                         provider,account_scope,cost_currency,provider_task_ref,submit_job_ref,status,
                         submission_count,authorization_ref,authorized_by,record_mode,
                         input_fingerprint)
                       values(%s,%s,%s,%s,%s,1,'volcengine','test-account','CNY',%s,%s,'completed',
                              1,'test-authorization','reviewer@example.com',
                              'historical_backfill',%s) returning id""",
                    (project_a, video_a, video_asset, "c" * 64, f"las-{suffix}",
                     f"provider-{suffix}", f"submit-{suffix}", "b" * 64),
                ).fetchone()[0]
                receipt_ids.append(conn.execute(
                    """insert into project_las_analysis_receipt(
                         attempt_id,project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                         provider,account_scope,
                         provider_task_ref,submit_job_ref,completion_job_ref,model_id,
                         operator_id,operator_version,template_id,status,business_code,
                         result_sha256,token_usages,estimated_cost,cost_currency,
                         pricing_version,executed_at,recorded_by,binding_basis)
                       values(%s,%s,%s,%s,%s,%s,1,'volcengine','test-account',%s,%s,%s,
                              'seed-2-lite','las_video_understanding','v1',
                              'omni_video_audio_captioning@v1','completed',0,%s,
                              '{"input_tokens":123}'::jsonb,%s,'CNY','trial-2026-09',
                              now(),'reviewer@example.com','reviewed_submission_and_result')
                       returning id""",
                    (attempt_id, project_a, video_a, video_asset, "c" * 64, f"las-{suffix}",
                     f"provider-{suffix}", f"submit-{suffix}", f"poll-{suffix}",
                     "d" * 64, estimate),
                ).fetchone()[0])
            conn.execute(
                """insert into project_las_supplier_bill_item(
                     receipt_id,project_id,video_id,provider,account_scope,
                     provider_task_ref,supplier_bill_ref,supplier_line_ref,
                     reconciliation_version,signed_amount,
                     cost_currency,reconciliation_status,billing_date,billing_timezone,
                     evidence_source,supplier_evidence_sha256,verified_by)
                   values(%s,%s,%s,'volcengine','test-account','provider-billed',
                          'bill-1','bill-line-1',1,0.19,'CNY','final',(now() at time zone 'Asia/Shanghai')::date,
                          'Asia/Shanghai','volcengine_bill_export',%s,'reviewer@example.com')""",
                (receipt_ids[1], project_a, video_a, "e" * 64),
            )
            # Two same-transaction correction lines share now(), so only the
            # explicit sequence may decide whether reconciliation is final.
            with conn.transaction():
                for version, line_ref, signed_amount, status in (
                    (2, "bill-line-2", "0.010000", "partial"),
                    (3, "bill-line-3", "-0.010000", "final"),
                ):
                    conn.execute(
                        """insert into project_las_supplier_bill_item(
                             receipt_id,project_id,video_id,provider,account_scope,
                             provider_task_ref,supplier_bill_ref,supplier_line_ref,
                             reconciliation_version,signed_amount,cost_currency,
                             reconciliation_status,billing_date,billing_timezone,
                             evidence_source,supplier_evidence_sha256,verified_by)
                           values(%s,%s,%s,'volcengine','test-account','provider-billed',
                                  'bill-1',%s,%s,%s,'CNY',%s,(now() at time zone 'Asia/Shanghai')::date,
                                  'Asia/Shanghai','volcengine_bill_export',%s,
                                  'reviewer@example.com')""",
                        (receipt_ids[1], project_a, video_a, line_ref, version,
                         signed_amount, status, "e" * 64),
                    )
            conn.execute(
                """insert into project_las_supplier_bill_item(
                     receipt_id,project_id,video_id,provider,account_scope,
                     provider_task_ref,supplier_bill_ref,supplier_line_ref,
                     reconciliation_version,signed_amount,cost_currency,
                     reconciliation_status,billing_date,billing_timezone,
                     evidence_source,supplier_evidence_sha256,verified_by)
                   values(%s,%s,%s,'volcengine','test-account','provider-no-estimate',
                          'bill-1','bill-line-free',1,0,'CNY','final',(now() at time zone 'Asia/Shanghai')::date,
                          'Asia/Shanghai','volcengine_bill_export',%s,
                          'reviewer@example.com')""",
                (receipt_ids[2], project_a, video_a, "e" * 64),
            )
            with pytest.raises(psycopg.Error, match="account, task and currency"):
                with conn.transaction():
                    conn.execute(
                        """insert into project_las_supplier_bill_item(
                             receipt_id,project_id,video_id,provider,account_scope,
                             provider_task_ref,supplier_bill_ref,supplier_line_ref,
                             reconciliation_version,signed_amount,cost_currency,
                             reconciliation_status,billing_date,billing_timezone,
                             evidence_source,supplier_evidence_sha256,verified_by)
                           values(%s,%s,%s,'volcengine','wrong-account','provider-billed',
                                  'bill-1','wrong-account-line',4,1,'CNY','final',(now() at time zone 'Asia/Shanghai')::date,
                                  'Asia/Shanghai','volcengine_bill_export',%s,
                                  'reviewer@example.com')""",
                        (receipt_ids[1], project_a, video_a, "e" * 64),
                    )
            las_review = conn.execute(
                """insert into project_las_video_review(
                     project_id,video_id,asset_id,asset_sha256,asset_manifest_fingerprint,
                     delivery_origin,review_version,review_statement,binding_scheme,
                     object_version_id)
                   values(%s,%s,%s,%s,%s,'https://media.example','las-video-v2',
                          '{"consent_statement":"I approve whole-video LAS analysis"}'::jsonb,
                          'versioned_bytes_v2','cost-test-version')
                   returning id""",
                (project_a, video_a, video_asset, "c" * 64, "d" * 64),
            ).fetchone()[0]
            conn.execute(
                """update project_las_video_review
                      set status='approved',reviewed_by='reviewer@example.com'
                    where id=%s""", (las_review,),
            )
            open_attempt = conn.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,status,authorization_ref,authorized_by,
                     record_mode,input_fingerprint,video_review_id,requested_by,object_version_id)
                   values(%s,%s,%s,%s,'las-timeout',1,'volcengine_las','test-account','CNY',
                          'prepared',%s,'reviewer@example.com',
                          'live_pre_dispatch',%s,%s,'reviewer@example.com',
                          'cost-test-version') returning id""",
                (project_a, video_a, video_asset, "c" * 64, str(las_review), "b" * 64,
                 las_review),
            ).fetchone()[0]
            conn.execute(
                """update project_las_analysis_attempt
                      set status='submitting',submission_count=1 where id=%s""",
                (open_attempt,),
            )
            conn.execute(
                """update project_las_analysis_attempt
                      set status='unknown',error_code='http_return_lost' where id=%s""",
                (open_attempt,),
            )
            with pytest.raises(psycopg.Error, match="submission may be claimed once"):
                with conn.transaction():
                    conn.execute(
                        """update project_las_analysis_attempt
                              set status='submitting',submission_count=0 where id=%s""",
                        (open_attempt,),
                    )
            with pytest.raises(psycopg.Error, match="video asset SHA-256"):
                with conn.transaction():
                    conn.execute(
                        """insert into project_las_analysis_receipt(
                             attempt_id,project_id,video_id,asset_id,asset_sha256,task_key,
                             attempt_no,provider,account_scope,
                             provider_task_ref,submit_job_ref,completion_job_ref,model_id,
                             operator_id,operator_version,template_id,status,business_code,
                             result_sha256,cost_currency,executed_at,recorded_by,binding_basis)
                           values(%s,%s,%s,%s,%s,'las-wrong',1,'volcengine','test-account','provider-wrong',
                                  'submit','poll','seed','las','v1','template','completed',0,
                                  %s,'CNY',now(),'reviewer@example.com',
                                  'reviewed_submission_and_result')""",
                        (attempt_id, project_a, video_a, video_asset, "f" * 64, "d" * 64),
                    )
            with pytest.raises(psycopg.Error, match="append-only"):
                with conn.transaction():
                    conn.execute(
                        "update project_las_analysis_receipt set estimated_cost=99 where id=%s",
                        (receipt_ids[0],),
                    )

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
            assert len(result["records"]) == 3
            day = next(record for record in result["records"] if record["cost_currency"] == "USD")
            assert day == {
                **day,
                "cost_currency": "USD",
                "task_count": 13,
                "asr_task_count": 4,
                "l3_task_count": 2,
                "las_task_count": 0,
                "discovery_run_count": 2,
                "comment_run_count": 1,
                "profile_run_count": 2,
                "statistics_run_count": 2,
                "known_amount": 2.508,
                "actual_amount": 1.0,
                "estimated_amount": 1.008,
                "mixed_amount": 0.5,
                "unknown_task_count": 6,
                "unbilled_job_count": 3,
                "las_pending_bill_count": 0,
                "cost_status": "partial",
            }
            assert "unknown_amount" not in day
            assert 99 not in day.values()
            cny = next(record for record in result["records"] if record["cost_currency"] == "CNY")
            assert cny["task_count"] == 5
            assert cny["las_task_count"] == 4
            assert cny["discovery_run_count"] == 0
            assert cny["comment_run_count"] == 0
            assert cny["profile_run_count"] == 0
            assert cny["statistics_run_count"] == 0
            assert cny["known_amount"] == 0.72
            assert cny["actual_amount"] == 0.3
            assert cny["estimated_amount"] == 0.42
            assert cny["mixed_amount"] is None
            assert cny["unknown_task_count"] == 4
            assert cny["unbilled_job_count"] == 1
            assert cny["las_pending_bill_count"] == 3
            assert cny["cost_status"] == "partial"
            assert len(result["las_receipts"]) == 3
            by_ref = {row["provider_task_ref"]: row for row in result["las_receipts"]}
            assert by_ref["provider-pending"]["registered_supplier_amount"] is None
            assert by_ref["provider-billed"]["registered_supplier_amount"] == 0.19
            assert by_ref["provider-billed"]["supplier_line_count"] == 3
            assert by_ref["provider-billed"]["supplier_reconciliation_status"] == "final"
            assert by_ref["provider-no-estimate"]["estimated_cost"] is None
            assert by_ref["provider-no-estimate"]["registered_supplier_amount"] == 0.0
            assert len(by_ref["provider-billed"]["supplier_billing_dates"]) == 1
            assert result["las_receipts_truncated"] is False
            assert len(result["las_supplier_daily"]) == 1
            assert result["las_supplier_daily"][0]["registered_line_amount"] == 0.19
            assert result["las_supplier_daily"][0]["supplier_line_count"] == 4
            assert len(result["las_open_attempts"]) == 1
            assert result["las_open_attempts"][0]["status"] == "unknown"
            assert result["las_open_attempts"][0]["provider_task_ref"] is None
            unpriced = next(record for record in result["records"] if record["cost_currency"] == "UNKNOWN")
            assert unpriced["task_count"] == 1
            assert unpriced["discovery_run_count"] == 1
            assert unpriced["comment_run_count"] == 0
            assert unpriced["profile_run_count"] == 0
            assert unpriced["statistics_run_count"] == 0
            assert unpriced["known_amount"] is None
            assert unpriced["unknown_task_count"] == 1
            assert unpriced["unbilled_job_count"] == 1
            assert unpriced["cost_status"] == "partial"

            monkeypatch.setenv("WM_END_USER_EMAIL", "reader-b@example.com")
            with pytest.raises(PermissionError, match="RESEARCH_PROJECT_ACCESS_DENIED"):
                module.main(db, str(project_a), days=1)
            monkeypatch.setenv("WM_END_USER_EMAIL", "reader-a@example.com")
            with pytest.raises(ValueError, match="days"):
                module.main(db, str(project_a), days=0)
            with pytest.raises(ValueError, match="days"):
                module.main(db, str(project_a), days=True)
            with pytest.raises(psycopg.Error):
                with conn.transaction():
                    conn.execute(
                        """insert into project_las_analysis_receipt(
                             attempt_id,project_id,video_id,asset_id,asset_sha256,task_key,
                             attempt_no,provider,account_scope,provider_task_ref,
                             submit_job_ref,completion_job_ref,model_id,operator_id,
                             operator_version,template_id,status,cost_currency,
                             executed_at,recorded_by,binding_basis)
                           values(%s,%s,%s,%s,%s,'las-timeout',1,'volcengine_las','test-account',
                                  'provider-late','submit-late','poll-late',
                                  'doubao-seed-2-0-lite-260428','las_video_understanding','v1',
                                  'omni_video_audio_captioning@v1','unknown','CNY',now(),'reviewer@example.com',
                                  'reviewed_submission_and_result')""",
                        (open_attempt, project_a, video_a, video_asset, "c" * 64),
                    )
            conn.execute(
                """update project_las_analysis_attempt
                      set status='completed',provider_task_ref='provider-late',
                          submit_job_ref='submit-late' where id=%s""",
                (open_attempt,),
            )
            conn.execute(
                """insert into project_las_analysis_receipt(
                     attempt_id,project_id,video_id,asset_id,asset_sha256,task_key,
                     attempt_no,provider,account_scope,provider_task_ref,
                     submit_job_ref,completion_job_ref,model_id,operator_id,
                     operator_version,template_id,status,business_code,result_sha256,
                     estimated_cost,pricing_version,cost_currency,executed_at,
                     recorded_by,binding_basis,object_version_id)
                   values(%s,%s,%s,%s,%s,'las-timeout',1,'volcengine_las','test-account',
                          'provider-late','submit-late','poll-late',
                          'doubao-seed-2-0-lite-260428','las_video_understanding','v1',
                          'omni_video_audio_captioning@v1','completed',0,%s,0.1,'test-price','CNY',now(),
                          'reviewer@example.com','reviewed_submission_and_result',
                          'cost-test-version')""",
                (open_attempt, project_a, video_a, video_asset, "c" * 64, "e" * 64),
            )
            resolved = module.main(db, str(project_a), days=1)
            assert resolved["las_open_attempts"] == []
            assert any(row["provider_task_ref"] == "provider-late" for row in resolved["las_receipts"])
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
