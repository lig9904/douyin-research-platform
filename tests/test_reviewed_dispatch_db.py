"""Real PostgreSQL selector checks; synthetic assets, no external calls."""
import os
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from douyin_research import reviewed_dispatch as dispatch
from douyin_research.l2.media_review import asset_fingerprint
from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, StoredMediaObject

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")


@pytest.fixture
def reviewed_asset():
    video = uuid4()
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into source_video(id,platform,platform_video_id,research_level) "
                     "values(%s,'douyin',%s,2)", (video, f"dispatch-{video}"))
    try:
        asset = MediaAssetStore(DSN).record(video_id=video, kind="audio", storage_location="test",
            bucket="private", stored=StoredMediaObject(key=PrivateS3MediaStorage.object_key("b" * 64), sha256="b" * 64,
                                                       size=100, content_type="audio/wav"))
        with psycopg.connect(DSN) as conn:
            conn.execute("""insert into asr_media_review(asset_id,review_version,asset_fingerprint,
                delivery_origin,reviewed_by,identity_source,approved_at)
                values(%s,'v1',%s,'https://media.example','synthetic',%s,now()-interval '1 minute')""",
                (asset.id, asset_fingerprint(asset, "https://media.example"), dispatch.IDENTITY_SOURCE))
        yield asset
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from source_video where id=%s", (video,))


def selected(asset):
    with dispatch._candidates(DSN, "asr") as rows:
        return [row for row in rows if row[1] == asset.id]


def test_real_cursor_selects_latest_approval_and_not_revoked_fallback(reviewed_asset):
    asset = reviewed_asset
    assert selected(asset) == [(asset.video_id, asset.id, "v1", "b" * 64)]
    with psycopg.connect(DSN) as conn:
        conn.execute("""insert into asr_media_review(asset_id,review_version,asset_fingerprint,
            delivery_origin,reviewed_by,identity_source,active)
            values(%s,'v2',%s,'https://media.example','synthetic',%s,false)""",
            (asset.id, asset_fingerprint(asset, "https://media.example"), dispatch.IDENTITY_SOURCE))
    assert selected(asset) == []


def test_real_cursor_excludes_low_research_level(reviewed_asset):
    assert selected(reviewed_asset)
    with psycopg.connect(DSN) as conn:
        conn.execute("update source_video set research_level=1 where id=%s", (reviewed_asset.video_id,))
    assert selected(reviewed_asset) == []


@pytest.fixture
def promoted_video(reviewed_asset):
    video = reviewed_asset.video_id
    run, batch = uuid4(), uuid4()
    with psycopg.connect(DSN) as conn:
        conn.execute("""insert into pipeline_run(id,run_type,run_version,platform,status)
            values(%s,'synthetic-dispatch','test','douyin','success')""", (run,))
        conn.execute("""insert into research_promotion_batch(id,pipeline_run_id,source_run_id,
            quota_date,platform,quota_key,target_level,rule_version,requested_top_n,min_score,
            candidate_fingerprint,selected_count)
            values(%s,%s,%s,current_date,'douyin',%s,3,'test',1,0,'synthetic',1)""",
            (batch, run, run, str(batch)))
        conn.execute("""insert into research_promotion_decision(batch_id,video_id,quota_date,
            target_level,outcome,reason_code) values(%s,%s,current_date,3,'selected','synthetic')""",
            (batch, video))
    try:
        yield video
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from pipeline_run where id=%s", (run,))


def add_l3_review(video, version, *, reviewed=True, trusted=True):
    value = dict(version=version, reviewed=reviewed, evidence_fingerprint="c" * 64,
                 evidence_version="evidence-v1", reviewer_identity_source=(
                     dispatch.IDENTITY_SOURCE if trusted else "untrusted"))
    with psycopg.connect(DSN) as conn:
        return conn.execute("""insert into human_annotation(video_id,annotation_type,value)
            values(%s,'l3_privacy_review',%s) returning id""", (video, Jsonb(value))).fetchone()[0]


def selected_l3(video):
    with dispatch._candidates(DSN, "l3") as rows:
        return [row for row in rows if row[0] == video]


@pytest.mark.parametrize("trusted,reviewed", [(True, False), (False, True)])
def test_l3_latest_invalid_review_cannot_fall_back(promoted_video, trusted, reviewed):
    video = promoted_video
    approval = add_l3_review(video, "v1")
    assert selected_l3(video) == [(video, approval, "v1", "c" * 64, "evidence-v1")]
    add_l3_review(video, "v2", trusted=trusted, reviewed=reviewed)
    assert selected_l3(video) == []


def test_l3_requires_persisted_selected_promotion(reviewed_asset):
    add_l3_review(reviewed_asset.video_id, "v1")
    assert selected_l3(reviewed_asset.video_id) == []


@pytest.mark.parametrize("stage,task_type", [("asr", "asr_transcription"), ("l3", "l3_structured_research")])
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_old_key_orphan_cost_blocks_new_configuration(reviewed_asset, stage, task_type, status):
    video = reviewed_asset.video_id
    fingerprint = reviewed_asset.content_sha256
    with psycopg.connect(DSN) as conn:
        conn.execute("""insert into research_task_cost(task_key,task_type,task_version,video_id,
            status,input_fingerprint,cost_currency,cost_basis)
            values(%s,%s,'old-version',%s,%s,%s,'CNY','unknown')""",
            (f"old-model:{video}", task_type, video, status, fingerprint))
    assert dispatch._existing(DSN, stage, f"new-model:{video}", video, fingerprint) == "attention"
    assert dispatch._existing(DSN, stage, f"new-content:{video}", video, "d" * 64) is None


@pytest.mark.parametrize("stage,status,expected", [
    ("asr", "submitting", "attention"), ("asr", "submitted", "existing"),
    ("asr", "running", "existing"), ("asr", "completed", "existing"),
    ("asr", "failed", "attention"), ("l3", "running", "attention"),
    ("l3", "completed", "existing"), ("l3", "failed", "attention")])
def test_old_model_job_blocks_new_configuration(reviewed_asset, stage, status, expected):
    video = reviewed_asset.video_id
    fingerprint = reviewed_asset.content_sha256
    key = f"previous-model:{video}"
    with psycopg.connect(DSN) as conn:
        if stage == "asr":
            conn.execute("""insert into asr_execution_job(id,task_key,video_id,provider,model_id,
                model_revision,engine_version,source_fingerprint,media_ref_fingerprint,status,
                cost_currency,budget_date,budget_key)
                values(%s,%s,%s,'synthetic','previous-model','old','old',%s,'synthetic',%s,
                'CNY',current_date,'synthetic')""", (uuid4(), key, video, fingerprint, status))
        else:
            conn.execute("""insert into l3_execution_job(id,task_key,video_id,provider,model_id,
                model_revision,prompt_version,schema_version,input_fingerprint,status,
                cost_currency,budget_date,budget_key)
                values(%s,%s,%s,'synthetic','previous-model','old','old','old',%s,%s,
                'CNY',current_date,'synthetic')""", (uuid4(), key, video, fingerprint, status))
    assert dispatch._existing(DSN, stage, f"new-model:{video}", video, fingerprint) == expected
    assert dispatch._existing(DSN, stage, f"new-content:{video}", video, "d" * 64) is None
