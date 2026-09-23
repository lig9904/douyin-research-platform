from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


@pytest.fixture(autouse=True)
def legacy_reader_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "fixture@example.com")


def load_backend():
    path = Path(
        "windmill/f/content_research/research_dashboard.raw_app/"
        "backend/get_video_library.py"
    )
    spec = importlib.util.spec_from_file_location("get_video_library", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._get_legacy_allowlist = lambda: "fixture@example.com"
    return module


def resource_from_dsn(dsn: str) -> dict:
    u = urlparse(dsn)
    return {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 5432,
        "user": u.username or "",
        "password": u.password or "",
        "dbname": u.path.lstrip("/"),
        "sslmode": "disable",
    }


def clear_and_seed() -> str:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "analysis_run",
            "research_task_cost",
            "video_comment",
            "video_score",
            "collection_item",
            "collection",
            "pipeline_run_item",
            "metric_snapshot",
            "discovery_event",
            "account_metric_snapshot",
            "provider_entity_lineage",
            "external_signal",
            "external_api_call",
            "external_api_response",
            "pipeline_run",
            "source_video",
            "source_account",
            "daily_budget",
        ):
            cur.execute(f"delete from {table}")

        cur.execute(
            """
            insert into source_account(platform, platform_account_id, nickname)
            values ('douyin','acct-video-lib','渔海老张')
            returning id
            """
        )
        account_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, account_id, title, description,
              source_url, published_at, duration_ms, research_level,
              monitoring_status, monitoring_priority, first_seen_at, last_seen_at
            )
            values (
              'douyin','video-lib-1',%s,
              '秦皇岛海边惊现龙王祭坛？',
              '当地渔民讲述百年传说',
              'https://example.com/video-lib-1',
              now()-interval '2 days',158000,1,'observe',88,
              now()-interval '2 days',now()-interval '1 hour'
            )
            returning id
            """,
            (account_id,),
        )
        video_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into account_metric_snapshot(
              account_id, provider, source_endpoint, observation_key,
              follower_count, captured_at
            )
            values (%s,'tikhub','fixture','vl-account-metric',123000,now())
            """,
            (account_id,),
        )

        cur.execute(
            """
            insert into metric_snapshot(
              video_id, provider, source_endpoint, observation_key,
              play_count, like_count, comment_count, share_count,
              author_follower_count, captured_at
            )
            values (
              %s,'tikhub','fixture','vl-video-metric',
              5286000,321000,28000,46000,123000,now()
            )
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into video_score(video_id, score_type, score, rule_version, components)
            values (%s,'priority',88,'test','{}'::jsonb)
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into discovery_event(
              video_id, provider, source_type, source_key, observation_key,
              discovered_at, rank_value, rule_version, metadata
            )
            values
              (%s,'tikhub','low_fan','low_fan_24h','vl-discovery-1',
               now()-interval '3 hours',1,'test',
               '{"run_id":"private-run","request_fingerprint":"private-fingerprint"}'::jsonb),
              (%s,'tikhub','search','秦皇岛海边传说','vl-discovery-2',
               now()-interval '2 hours',2,'test','{}'::jsonb)
            """,
            (video_id, video_id),
        )

        cur.execute(
            """
            insert into video_comment(
              video_id, provider, platform_comment_id, text_content,
              like_count, captured_at
            )
            values
              (%s,'tikhub','comment-1','这也太神奇了！',23000,now()),
              (%s,'tikhub','comment-2','我爷爷也说过这个地方',8421,now())
            """,
            (video_id, video_id),
        )

        cur.execute(
            """
            insert into collection(name, description, created_by)
            values ('重点专题','测试专题','tester')
            returning id
            """
        )
        collection_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into collection_item(collection_id, video_id, note)
            values (%s,%s,'测试收藏')
            """,
            (collection_id, video_id),
        )
        conn.commit()

    return str(video_id)


def test_video_library_completed_transcript_is_bound_and_unknown_cost_stays_null() -> None:
    from douyin_research.l2.transcripts import TaskCost, TranscriptEvidence, TranscriptEvidenceStore

    assert DSN
    video_id = clear_and_seed()
    backend = load_backend()
    resource = resource_from_dsn(DSN)
    assert backend.main(resource, selected_video_id=video_id)["detail"]["asr_transcript"] is None
    with psycopg.connect(DSN) as conn:
        conn.execute("update source_video set research_level=2 where id=%s", (video_id,))
    record = TranscriptEvidenceStore(DSN).ingest(
        video_id, task_key="transcript-ui-test",
        evidence=TranscriptEvidence(
            asr_provider="volcengine-doubao-asr", model_id="bigmodel", model_revision="2.0",
            engine_version="volc.seedasr.auc", source_fingerprint="view-input-v1",
            text="字" * 12001, audio_duration_ms=28723, quality_status="usable",
        ),
        cost=TaskCost(api_cost=None, asr_cost=None),
    )
    result = backend.main(resource, selected_video_id=video_id)["detail"]["asr_transcript"]
    assert result["text"] == "字" * 12000 and result["truncated"] is True
    assert result["cost"]["asr_cost"] is None and result["cost"]["total_cost"] is None
    assert result["cost"]["basis"] == "unknown"
    assert result["provider"] == "volcengine-doubao-asr"
    assert "source_fingerprint" not in result and "metadata" not in result
    with psycopg.connect(DSN) as conn:
        conn.execute("update research_task_cost set input_fingerprint='other' where id=%s", (record.task_cost_id,))
    assert backend.main(resource, selected_video_id=video_id)["detail"]["asr_transcript"] is None
    with psycopg.connect(DSN) as conn:
        conn.execute("update research_task_cost set input_fingerprint='view-input-v1',status='failed' where id=%s", (record.task_cost_id,))
    assert backend.main(resource, selected_video_id=video_id)["detail"]["asr_transcript"] is None


def test_video_library_filters_and_detail() -> None:
    assert DSN
    video_id = clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        research_level=1,
        source_type="low_fan",
        priority_min=80,
        status="observe",
        play_min=1000000,
        play_max=-1,
        follower_min=100000,
        follower_max=200000,
        collected="yes",
        query="龙王祭坛",
        page=1,
        page_size=10,
        sort="priority_desc",
        selected_video_id=video_id,
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["title"] == "秦皇岛海边惊现龙王祭坛？"
    assert float(item["priority"]) == pytest.approx(88)
    assert set(item["sources"]) == {"low_fan", "search"}
    assert item["collection_count"] == 1

    detail = result["detail"]
    assert detail["id"] == video_id
    assert len(detail["evidence"]) == 2
    assert all("metadata" not in item for item in detail["evidence"])
    assert len(detail["comments"]) == 2
    assert detail["source_url"] == "https://example.com/video-lib-1"


def test_video_library_numeric_ranges_exclude_null_but_keep_reported_zero() -> None:
    assert DSN
    video_id = clear_and_seed()
    module = load_backend()
    resource = resource_from_dsn(DSN)
    filters = {
        "play_min": 0,
        "play_max": 0,
        "follower_min": 0,
        "follower_max": 0,
    }

    with psycopg.connect(DSN) as conn:
        conn.execute(
            """
            update metric_snapshot
            set play_count=null, author_follower_count=null
            where video_id=%s
            """,
            (video_id,),
        )
    missing = module.main(resource, **filters)
    assert missing["total"] == 0
    assert missing["items"] == []

    with psycopg.connect(DSN) as conn:
        conn.execute(
            """
            update metric_snapshot
            set play_count=0, author_follower_count=0
            where video_id=%s
            """,
            (video_id,),
        )
    reported_zero = module.main(resource, **filters)
    assert reported_zero["total"] == 1
    assert reported_zero["items"][0]["play_count"] == 0
    assert reported_zero["items"][0]["author_follower_count"] == 0


def test_video_library_query_and_pagination_empty_state() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        query="不存在的关键词",
        page=1,
        page_size=10,
    )

    assert result["total"] == 0
    assert result["items"] == []
    assert result["detail"] == {}


def test_video_library_all_platform_mode() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="all",
        days=30,
        page=1,
        page_size=10,
    )

    assert result["total"] == 1
    assert result["items"][0]["platform"] == "douyin"
    keys = [x["key"] for x in result["platforms"]]
    assert "douyin" in keys and "kuaishou" in keys


def test_video_library_returns_latest_completed_l3_public_result_only() -> None:
    assert DSN
    video_id = clear_and_seed()
    module = load_backend()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into research_task_cost(
              task_key, task_type, task_version, video_id, status,
              input_fingerprint, api_cost, asr_cost, llm_cost,
              cost_currency, cost_basis
            ) values (
              'video-library-l3-old', 'l3_structured_research', 'v1', %s,
              'completed', 'private-old-input', 0, 0, 0.12, 'CNY', 'actual'
            ) returning id
            """,
            (video_id,),
        )
        old_cost_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into analysis_run(
              video_id, analysis_type, analysis_level, status,
              model, model_revision, prompt_version, schema_version,
              input_fingerprint, input_refs, output, cost_amount, cost_currency,
              task_cost_id, created_at
            ) values (
              %s, 'l3_structured_research', 'L3', 'completed',
              'old-model', 'old-rev', 'old-prompt', 'old-schema',
              'private-old-input', '{"task_key":"old-task"}'::jsonb,
              '{"narrative_structure":["old public result"]}'::jsonb,
              0.12, 'CNY', %s, now()-interval '2 hours'
            )
            """,
            (video_id, old_cost_id),
        )
        cur.execute(
            """
            insert into research_task_cost(
              task_key, task_type, task_version, video_id, status,
              input_fingerprint, output_fingerprint, api_cost, asr_cost, llm_cost,
              cost_currency, cost_basis, metadata
            ) values (
              'video-library-l3-new', 'l3_structured_research',
              'l3-research-v1.0.0', %s, 'completed',
              'private-new-input', 'private-output-fingerprint',
              null, 0, null, 'CNY', 'mixed',
              '{"provider_request_id":"private"}'::jsonb
            ) returning id
            """,
            (video_id,),
        )
        new_cost_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into analysis_run(
              video_id, analysis_type, analysis_level, status,
              model, model_revision, prompt_version, schema_version,
              input_fingerprint, output_fingerprint, input_refs, output,
              cost_amount, cost_currency, task_cost_id, created_at
            ) values (
              %s, 'l3_structured_research', 'L3', 'completed',
              'safe-model', 'revision-2', 'prompt-v2', 'l3-research-v1.0.0',
              'private-new-input', 'private-output-fingerprint',
              '{"fingerprint":"private","task_key":"private-task","raw_text":"never return"}'::jsonb,
              '{
                "narrative_structure":["公开叙事结论"],
                "hook_functions":["公开 Hook 结论"],
                "comment_semantics":[{"raw_comment":"must not leak"}],
                "limitations":["样本有限"],
                "mechanism_hypotheses_are_inferences":true,
                "privacy_reviewed":true,
                "input_fingerprint":"must not leak",
                "task_key":"must not leak",
                "internal_error":"must not leak"
              }'::jsonb,
              null, 'CNY', %s, now()-interval '1 hour'
            )
            """,
            (video_id, new_cost_id),
        )
        # Neither failed L3 nor a completed different L3 analysis type may replace it.
        cur.execute(
            """
            insert into analysis_run(
              video_id, analysis_type, analysis_level, status, output, created_at
            ) values
              (%s, 'l3_structured_research', 'L3', 'failed', '{"internal_error":"no"}'::jsonb, now()),
              (%s, 'other_l3_summary', 'L3', 'completed', '{"narrative_structure":["wrong L3 type"]}'::jsonb, now()),
              (%s, 'l2_summary', 'L2', 'completed', '{"narrative_structure":["no"]}'::jsonb, now())
            """,
            (video_id, video_id, video_id),
        )
        conn.commit()

    result = module.main(resource_from_dsn(DSN), selected_video_id=video_id)
    l3 = result["detail"]["l3_analysis"]
    assert l3["model"] == "safe-model"
    assert l3["model_revision"] == "revision-2"
    assert l3["prompt_version"] == "prompt-v2"
    assert l3["schema_version"] == "l3-research-v1.0.0"
    assert l3["output"]["narrative_structure"] == ["公开叙事结论"]
    assert l3["output"]["limitations"] == ["样本有限"]
    assert "comment_semantics" not in l3["output"]
    assert l3["cost"] == {
        "api_cost": None,
        "asr_cost": 0.0,
        "llm_cost": None,
        "total_cost": None,
        "currency": "CNY",
        "basis": "mixed",
    }
    assert "input_refs" not in l3
    assert "input_fingerprint" not in l3
    assert "output_fingerprint" not in l3
    assert "task_key" not in l3
    assert "internal_error" not in l3["output"]


def test_video_library_l3_empty_state_excludes_incomplete_and_failed_records() -> None:
    assert DSN
    video_id = clear_and_seed()
    module = load_backend()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into source_video(platform, platform_video_id, research_level)
            values ('douyin', 'video-lib-other-cost-owner', 3)
            returning id
            """
        )
        other_video_id = cur.fetchone()[0]
        invalid_cost_ids = []
        for task_key, schema_version, fingerprint, cost_video_id in (
            (
                "video-library-l3-private",
                "l3-research-v1.0.0",
                "private-review-input",
                video_id,
            ),
            (
                "video-library-l3-wrong-schema",
                "wrong-schema",
                "wrong-schema-input",
                video_id,
            ),
            (
                "video-library-l3-cross-video",
                "l3-research-v1.0.0",
                "cross-video-input",
                other_video_id,
            ),
        ):
            cur.execute(
                """
                insert into research_task_cost(
                  task_key, task_type, task_version, video_id, status,
                  input_fingerprint, output_fingerprint,
                  api_cost, asr_cost, llm_cost, cost_currency, cost_basis
                ) values (
                  %s, 'l3_structured_research', %s, %s, 'completed',
                  %s, %s, 0, 0, 0.1, 'CNY', 'actual'
                ) returning id
                """,
                (
                    task_key,
                    schema_version,
                    cost_video_id,
                    fingerprint,
                    f"{fingerprint}-output",
                ),
            )
            invalid_cost_ids.append(cur.fetchone()[0])
        cur.execute(
            """
            insert into analysis_run(
              video_id, analysis_type, analysis_level, status,
              schema_version, input_fingerprint, output_fingerprint,
              output, task_cost_id, created_at
            )
            values
              (%s, 'l3_structured_research', 'L3', 'running', null, null, null,
               '{"narrative_structure":["running"]}'::jsonb, null, now()),
              (%s, 'l3_structured_research', 'L3', 'failed', null, null, null,
               '{"internal_error":"private"}'::jsonb, null, now()),
              (%s, 'l3_structured_research', 'L3', 'completed',
               'l3-research-v1.0.0', 'private-review-input',
               'private-review-input-output',
               '{"privacy_reviewed":false,"narrative_structure":["private"]}'::jsonb,
               %s, now()+interval '1 minute'),
              (%s, 'l3_structured_research', 'L3', 'completed',
               'wrong-schema', 'wrong-schema-input', 'wrong-schema-input-output',
               '{"privacy_reviewed":true,"narrative_structure":["wrong schema"]}'::jsonb,
               %s, now()+interval '2 minutes'),
              (%s, 'l3_structured_research', 'L3', 'completed',
               'l3-research-v1.0.0', 'orphan-input', 'orphan-output',
               '{"privacy_reviewed":true,"narrative_structure":["orphan"]}'::jsonb,
               null, now()+interval '3 minutes'),
              (%s, 'l3_structured_research', 'L3', 'completed',
               'l3-research-v1.0.0', 'cross-video-input', 'cross-video-input-output',
               '{"privacy_reviewed":true,"narrative_structure":["cross video"]}'::jsonb,
               %s, now()+interval '4 minutes')
            """,
            (
                video_id,
                video_id,
                video_id,
                invalid_cost_ids[0],
                video_id,
                invalid_cost_ids[1],
                video_id,
                video_id,
                invalid_cost_ids[2],
            ),
        )
        conn.commit()

    result = module.main(resource_from_dsn(DSN), selected_video_id=video_id)
    assert result["detail"]["l3_analysis"] is None
