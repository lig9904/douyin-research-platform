"""Discover approved first submissions; execution remains in existing workers.

No provider calls, budget writes, approval writes, or new dispatch ledger.
The injected executor is an internal fixed-path Windmill dependency.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import make_conninfo

from .l2.live_asr_service import live_asr_task_key
from .l2.media_review import IDENTITY_SOURCE, assert_reviewed_delivery
from .l3.evidence import L3EvidenceAssembler
from .l3.live_service import LiveArkConfiguration, live_ark_task_key, validate_live_ark_configuration
from .l3.results import L3_ANALYSIS_TYPE, L3_SCHEMA_VERSION
from .media_assets import MediaAssetStore

SUBMISSION_BATCH = 5
WORKERS = {"asr": "f/content_research/analysis/run_reviewed_asr",
           "l3": "f/content_research/analysis/run_reviewed_l3"}


@contextmanager
def scheduler_slot(dsn):
    """Keep one worker free for children across all analysis parent scripts.

    Contenders exit immediately, never wait while occupying another worker.
    This session lock is distinct from provider execution/accounting locks and
    is released by PostgreSQL on connection loss or process termination.
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        acquired = conn.execute("select pg_try_advisory_lock(%s)", (724901830215,)).fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("select pg_advisory_unlock(%s)", (724901830215,))


def run_scheduled(stage):
    """Internal runtime for two fixed, no-argument Windmill entrypoints.

    Selection uses the same operator-owned configuration as the child workers.
    Worker identity, configuration, review and accounting are revalidated by
    those workers. No approval, provider, URL or price is a public argument.
    """
    import wmill

    if stage not in WORKERS:
        raise ValueError("unsupported analysis stage")
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
                           password=db["password"], dbname=db["dbname"],
                           sslmode=db.get("sslmode", "prefer"))
        if stage == "asr":
            media = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
            configuration = dict(public_origin=media["public_endpoint"])
        else:
            cfg = json.loads(wmill.get_variable("f/content_research/l3_worker_config"))
            if set(cfg) != {"ark", "prompt_version", "max_daily_requests", "max_daily_cost_cny"}:
                raise ValueError("invalid fixed configuration")
            configuration = dict(ark=LiveArkConfiguration(**cfg["ark"]),
                                 prompt_version=cfg["prompt_version"])
        with scheduler_slot(dsn) as acquired:
            if not acquired:
                return {"status": "deferred", "reason": "analysis_scheduler_busy"}
            counts = dispatch_reviewed(dsn, stage=stage, **configuration,
                execute_worker=lambda path, args: wmill.run_script(
                    path=path, args=args, timeout=370 if stage == "l3" else 310, verbose=False))
    except Exception:
        raise RuntimeError(f"{stage.upper()} reviewed dispatch unavailable; inspect fixed configuration and database") from None
    if counts["failed"] or counts["attention"] or counts["blocked"]:
        raise RuntimeError(f"{stage.upper()} reviewed dispatch requires attention: "
            f"dispatched={counts['dispatched']}, failed={counts['failed']}, "
            f"attention={counts['attention']}, blocked={counts['blocked']}") from None
    return counts


@contextmanager
def _candidates(dsn, stage):
    # Stream the complete eligible set. A fixed prefix can permanently starve
    # new approvals behind old completed or stale entries. The server cursor
    # bounds client memory, and is closed even when the submission batch fills.
    with psycopg.connect(dsn) as conn, conn.cursor(name="reviewed_candidates") as cur:
        conn.execute("set transaction read only")
        cur.itersize = 100
        if stage == "asr":
            cur.execute("""with latest as (
                select distinct on (asset_id) * from asr_media_review
                order by asset_id,approved_at desc,review_version desc)
                select a.video_id,a.id,r.review_version,a.content_sha256
                from latest r join media_asset a on a.id=r.asset_id
                join source_video v on v.id=a.video_id
                where r.active and r.identity_source=%s and v.research_level>=2
                  and a.kind='audio' and a.content_type='audio/wav'
                order by r.approved_at,a.id""", (IDENTITY_SOURCE,))
        else:
            cur.execute("""with latest as (
            select distinct on (video_id) id,video_id,value,created_at from human_annotation
            where annotation_type='l3_privacy_review' order by video_id,created_at desc,id desc)
            select r.video_id,r.id,r.value->>'version',r.value->>'evidence_fingerprint',r.value->>'evidence_version'
            from latest r join source_video v on v.id=r.video_id
            where r.value->'reviewed'='true'::jsonb and r.value->>'reviewer_identity_source'=%s
              and v.research_level>=2 and exists(select 1 from research_promotion_decision p
                where p.video_id=r.video_id and p.target_level=3 and p.outcome='selected')
            order by r.created_at,r.id""", (IDENTITY_SOURCE,))
        yield cur


def _existing(dsn, stage, task_key, video_id, fingerprint):
    table = {"asr": "asr_execution_job", "l3": "l3_execution_job"}[stage]
    fingerprint_column = {"asr": "source_fingerprint", "l3": "input_fingerprint"}[stage]
    task_type = {"asr": "asr_transcription", "l3": L3_ANALYSIS_TYPE}[stage]
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        jobs = conn.execute(f"""select status from {table} where task_key=%s
            or (video_id=%s and {fingerprint_column}=%s)""",
            (task_key, video_id, fingerprint)).fetchall()
        cost = conn.execute("""select 1 from research_task_cost where task_key=%s
            or (video_id=%s and input_fingerprint=%s and task_type=%s) limit 1""",
            (task_key, video_id, fingerprint, task_type)).fetchone()
    if jobs:
        return "attention" if any(job[0] in {"failed", "submitting"}
            or (stage == "l3" and job[0] == "running") for job in jobs) else "existing"
    return "attention" if cost else None


def dispatch_reviewed(dsn, *, stage, execute_worker, public_origin=None, ark=None, prompt_version=None):
    """Process up to five new approved inputs; return only redacted counts.

    Fixed configuration comes from the calling server script, never public job
    arguments. Worker revalidation and paid locks remain authoritative after
    this read-only selection (including concurrent selectors).
    """
    if stage not in WORKERS:
        raise ValueError("unsupported analysis stage")
    if stage == "asr":
        origin = urlsplit(public_origin) if isinstance(public_origin, str) else None
        if (origin is None or origin.scheme != "https" or not origin.hostname
                or origin.username or origin.password or origin.query or origin.fragment
                or origin.path not in {"", "/"}):
            raise ValueError("fixed HTTPS public origin required")
    if stage == "l3":
        validate_live_ark_configuration(ark)
        if not isinstance(prompt_version, str) or not prompt_version.strip():
            raise ValueError("fixed prompt version required")
    with _candidates(dsn, stage) as rows:
        return _dispatch_rows(dsn, rows, stage=stage, execute_worker=execute_worker,
                              public_origin=public_origin, ark=ark, prompt_version=prompt_version)


def _dispatch_rows(dsn, rows, *, stage, execute_worker, public_origin, ark, prompt_version):
    counts = dict(examined=0, dispatched=0, existing=0, attention=0, blocked=0, failed=0)
    for row in rows:
        if counts["dispatched"] + counts["failed"] >= SUBMISSION_BATCH:
            break
        counts["examined"] += 1
        video, reference, review_version, fingerprint = row[:4]
        try:
            if stage == "asr":
                key = live_asr_task_key(video, fingerprint)
            else:
                key = live_ark_task_key(video, fingerprint, model_id=ark.model_id,
                    model_revision=ark.model_revision, prompt_version=prompt_version,
                    schema_version=L3_SCHEMA_VERSION)
            prior = _existing(dsn, stage, key, video, fingerprint)
            if prior:
                counts[prior] += 1
                continue
            if stage == "asr":
                asset = MediaAssetStore(dsn).get(video, reference)
                if asset is None:
                    raise ValueError("asset unavailable")
                assert_reviewed_delivery(dsn, video_id=video, asset_id=reference,
                    review_version=review_version, source_fingerprint=fingerprint,
                    media_url=f"{public_origin.rstrip('/')}/{asset.bucket}/{asset.object_key}")
                args = dict(video_id=str(video), asset_id=str(reference), review_version=review_version)
            else:
                evidence = L3EvidenceAssembler(dsn).assemble(video, privacy_reviewed=True,
                    privacy_review_version=review_version)
                if evidence.input_fingerprint != fingerprint or evidence.evidence_version != row[4]:
                    raise ValueError("approval stale")
                args = dict(approval_id=str(reference))
        except (ValueError, TypeError, AttributeError):
            counts["blocked"] += 1
            continue
        # No raw errors, payloads, URLs, provider references or credentials are
        # returned. A failed child retains its own authoritative execution row.
        try:
            result = execute_worker(WORKERS[stage], args)
            allowed = {"submitted", "running", "completed"} if stage == "asr" else {"completed"}
            if result.get("status") not in allowed or result.get("error_code"):
                raise RuntimeError("worker needs attention")
            counts["dispatched"] += 1
        except Exception:
            counts["failed"] += 1
    return counts
