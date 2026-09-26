# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@34ce0706b2b937c04bac732413d5809ac271ed9c", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Dispatch listened approvals and grant-bound, explicitly unlistened audio."""
from __future__ import annotations

import json
import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.project_analysis.asr_backend import ProjectASRService
from douyin_research.providers.volcengine_asr import VOLCENGINE_ASR_PROVIDER
from douyin_research.reviewed_dispatch import scheduler_slot

_WORKER_PATH = "f/content_research/project_asr/dispatch_reviewed_asr"
_BATCH_SIZE = 12


def _standing_candidates(dsn, media):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        return conn.execute("""select grant_row.project_id,inclusion_row.video_id,asset.id
               from project_asr_standing_grant grant_row
               join research_project project_row on project_row.id=grant_row.project_id and project_row.status='active'
               join research_organization org_row on org_row.id=project_row.organization_id and org_row.status='active'
               join project_video_inclusion inclusion_row on inclusion_row.project_id=grant_row.project_id
                 and inclusion_row.status='accepted'
               join source_video video_row on video_row.id=inclusion_row.video_id
                 and video_row.platform='douyin' and video_row.availability_status='available'
                 and video_row.platform_video_id ~ '^[0-9]+$'
                 and position('#' in video_row.source_url)=0
                 and split_part(video_row.source_url,'?',1) in
                   ('https://www.douyin.com/video/' || video_row.platform_video_id,
                    'https://douyin.com/video/' || video_row.platform_video_id,
                    'https://www.iesdouyin.com/share/video/' || video_row.platform_video_id || '/',
                    'https://iesdouyin.com/share/video/' || video_row.platform_video_id || '/')
               join lateral (select audio.id,audio.content_sha256
                 from media_asset audio
                 join media_asset parent on parent.id=audio.parent_asset_id
                   and parent.video_id=video_row.id and parent.kind='video'
                   and parent.content_type='video/mp4'
                   and parent.storage_location=audio.storage_location and parent.bucket=audio.bucket
                 where audio.video_id=video_row.id and audio.kind='audio'
                   and audio.content_type='audio/wav'
                   and audio.storage_location=%s and audio.bucket=%s
                   and (parent.source_response_id is null or audio.source_response_id is null
                        or parent.source_response_id=audio.source_response_id)
                   and exists (select 1 from pipeline_run_item item
                     join pipeline_run run on run.id=item.run_id
                     where item.entity_type='video' and item.entity_id=video_row.id
                       and item.stage='media' and item.outcome in ('success','reused')
                       and run.run_type='media_ingestion' and run.status='success'
                       and run.platform='douyin'
                       and item.metadata->'asset_ids' @> jsonb_build_array(parent.id::text,audio.id::text))
                 order by audio.created_at desc,audio.id desc limit 1) asset on true
               where grant_row.status='active' and grant_row.provider=%s
                 and not exists (select 1 from project_asr_execution_job job
                   where job.project_id=grant_row.project_id and job.video_id=video_row.id
                     and job.media_fingerprint=asset.content_sha256)
                 and not exists (select 1 from project_asr_media_review review
                   where review.project_id=grant_row.project_id and review.asset_id=asset.id
                     and review.standing_grant_id=grant_row.id)
                 and not exists (select 1 from project_asr_media_review listened
                   where listened.project_id=grant_row.project_id and listened.asset_id=asset.id
                     and listened.authorization_kind='listened' and listened.status='approved')
               order by grant_row.authorized_at,asset.id limit %s""",
            (media["storage_location"], media["bucket"], VOLCENGINE_ASR_PROVIDER, _BATCH_SIZE)).fetchall()


def _pending(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        return conn.execute(
            """select review.project_id,review.video_id,review.id
               from project_asr_media_review review
               join project_video_inclusion inclusion_row
                 on inclusion_row.project_id=review.project_id and inclusion_row.video_id=review.video_id
               join source_video video_row on video_row.id=review.video_id
               join research_project project_row on project_row.id=review.project_id
               join research_organization org_row on org_row.id=project_row.organization_id
               left join project_asr_standing_grant grant_row
                 on grant_row.id=review.standing_grant_id and grant_row.project_id=review.project_id
               where review.status='approved' and inclusion_row.status='accepted'
                 and video_row.availability_status='available'
                 and project_row.status='active' and org_row.status='active'
                 and (review.authorization_kind='listened' or
                   (review.authorization_kind='standing_grant' and grant_row.status='active'
                    and grant_row.provider=%s))
                 and not exists (
                   select 1 from project_asr_execution_job job
                   where job.media_review_id=review.id
                 )
               order by review.reviewed_at,review.id limit %s""",
            (VOLCENGINE_ASR_PROVIDER, _BATCH_SIZE),
        ).fetchall()


def main() -> dict:
    import wmill

    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
                            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        with scheduler_slot(dsn) as acquired:
            if not acquired:
                return {"status": "deferred", "reason": "analysis_scheduler_busy"}
            worker = json.loads(wmill.get_variable("f/content_research/project_asr_worker_config"))
            media = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
            service = ProjectASRService(dsn, delivery_origin=media["public_endpoint"],
                                        trusted_worker_actor=worker["service_reviewer_email"],
                                        trusted_storage_location=media["storage_location"],
                                        trusted_bucket=media["bucket"])
            needs_attention = 0
            for project_id, video_id, asset_id in _standing_candidates(dsn, media):
                try:
                    service.authorize_standing_asset(project_id=project_id, video_id=video_id, asset_id=asset_id)
                except (PermissionError, ValueError):
                    # One stale candidate must not starve listened approvals.
                    needs_attention += 1
            rows = _pending(dsn)
            submitted = completed = 0
            for project_id, video_id, review_id in rows:
                try:
                    result = wmill.run_script(
                        path=_WORKER_PATH,
                        args={"project_id": str(project_id), "video_id": str(video_id),
                              "media_review_id": str(review_id)},
                        timeout=90, verbose=False,
                    )
                except Exception:
                    needs_attention += 1
                    continue
                if result.get("status") in {"submitted", "running"}:
                    submitted += 1
                elif result.get("status") == "completed":
                    completed += 1
                else:
                    needs_attention += 1
    except Exception:
        raise RuntimeError("project ASR reviewed dispatch unavailable") from None
    if needs_attention:
        raise RuntimeError(f"project ASR reviewed dispatch requires attention: {needs_attention}")
    return {"selected": len(rows), "submitted": submitted, "completed": completed,
            "needs_attention": 0}
