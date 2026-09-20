"""Durable T3 orchestration for comments, L2 features, and L3 selection.

This module deliberately owns a *new* pipeline run.  It never updates the
L0/L1 source run: an L0/L1 run is immutable evidence of how a candidate was
found and scored.  The separate run also gives a restart a durable answer to
the important question "was the paid comment request definitely completed?".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.l0l1.comments import CommentCollectionSummary

from .comment_features import CommentFeatureExtractor, CommentFeatureSnapshot
from .promotion import MAX_TOP_N, L3PromotionGate, PromotionResult


COMMENT_PIPELINE_RULE_VERSION = "comment-pipeline-v1.0.0"
MAX_BATCH_VIDEOS = 20


class _CommentCollector(Protocol):
    def collect(
        self,
        video_platform_id: str,
        *,
        count: int = 20,
        max_pages: int = 1,
        max_items: int = 20,
        sample_reason: str = "top",
        triggered_by: str = "system",
    ) -> CommentCollectionSummary: ...


class _FeatureExtractor(Protocol):
    def extract(self, video_id: UUID | str) -> CommentFeatureSnapshot: ...


class _PromotionGate(Protocol):
    def promote(
        self,
        source_run_id: UUID | str,
        *,
        top_n: int,
        min_score: float = 0,
        quota_date: date | None = None,
        quota_key: str = "l3",
        triggered_by: str = "system",
    ) -> PromotionResult: ...


@dataclass(frozen=True, slots=True)
class CommentPipelineSettings:
    """Trusted scheduling settings, not fields accepted from a page request."""

    top_n: int
    min_score: float = 0
    quota_date: date | None = None
    quota_key: str = "l3"
    comment_count: int = 20
    max_pages: int = 1
    max_items: int = 20
    sample_reason: str = "top"


@dataclass(frozen=True, slots=True)
class CommentPipelineVideoResult:
    video_id: UUID
    platform_video_id: str
    outcome: str
    reason_code: str | None
    reused: bool


@dataclass(frozen=True, slots=True)
class CommentPipelineResult:
    pipeline_run_id: UUID
    source_run_id: UUID
    eligibility_run_id: UUID | None
    promotion: PromotionResult | None
    created: bool
    videos: tuple[CommentPipelineVideoResult, ...]


@dataclass(frozen=True, slots=True)
class _SourceVideo:
    video_id: UUID
    platform_video_id: str
    platform: str


class CommentPipeline:
    """Run comments -> deterministic L2 -> L3 promotion for one L0/L1 batch.

    ``worker_identity`` is intentionally constructor-only.  A UI caller cannot
    supply an actor, reviewer allowlist, or a different identity to obtain a
    more privileged execution path.  The Windmill wrapper must provide the
    controlled service identity when it constructs this service.
    """

    def __init__(
        self,
        dsn: str,
        *,
        collector: _CommentCollector,
        feature_extractor: _FeatureExtractor | None = None,
        promotion_gate: _PromotionGate | None = None,
        worker_identity: str,
        rule_version: str = COMMENT_PIPELINE_RULE_VERSION,
    ) -> None:
        if not dsn:
            raise ValueError("dsn is required")
        if not worker_identity or not worker_identity.strip():
            raise ValueError("worker_identity is required")
        if len(worker_identity) > 128:
            raise ValueError("worker_identity is too long")
        if not rule_version or len(rule_version) > 128:
            raise ValueError("rule_version is required and must be bounded")
        self.dsn = dsn
        self.collector = collector
        self.feature_extractor = feature_extractor or CommentFeatureExtractor(dsn)
        self.promotion_gate = promotion_gate or L3PromotionGate(dsn)
        self.worker_identity = worker_identity.strip()
        self.rule_version = rule_version

    def run(self, source_run_id: UUID | str, *, settings: CommentPipelineSettings) -> CommentPipelineResult:
        """Resume or execute one bounded source batch.

        A collector call is preceded by a durable ``collecting`` record.  If
        the process dies before it can prove a collection result was stored,
        the next run returns ``reconciliation_required`` rather than issuing a
        second potentially billable request.
        """

        source_id = _as_uuid(source_run_id, "source_run_id")
        _validate_settings(settings)
        fingerprint = _settings_fingerprint(source_id, self.rule_version, settings)

        with psycopg.connect(self.dsn) as lock_conn, lock_conn.cursor() as lock_cur:
            lock_key = _advisory_lock_key(source_id, self.rule_version)
            lock_cur.execute("select pg_advisory_lock(%s)", (lock_key,))
            try:
                source_videos, platform = self._load_and_validate_source(source_id)
                # Do not silently take the first 20: omitted source videos must
                # never become eligible by an old L2 state.
                if len(source_videos) > MAX_BATCH_VIDEOS:
                    raise ValueError(
                        f"source batch has {len(source_videos)} videos; hard limit is {MAX_BATCH_VIDEOS}"
                    )
                if not source_videos:
                    raise ValueError("source L0/L1 run has no scored video items")

                run_id, created = self._get_or_create_run(
                    source_id=source_id,
                    platform=platform,
                    fingerprint=fingerprint,
                    input_count=len(source_videos),
                )
                video_results = tuple(
                    self._process_video(run_id, item, settings)
                    for item in source_videos
                )
                successful = [item for item in video_results if item.outcome == "success"]
                blocked = [item for item in video_results if item.outcome != "success"]

                eligibility_run_id: UUID | None = None
                promotion: PromotionResult | None = None
                if successful:
                    eligibility_run_id = self._get_or_create_eligibility_run(
                        source_run_id=source_id,
                        comment_pipeline_run_id=run_id,
                        platform=platform,
                        successful_video_ids={item.video_id for item in successful},
                    )
                    # The independent eligibility run contains only complete T3
                    # items, so Gate cannot select a failed item merely because
                    # that video happened to have old L2 state.
                    promotion = self.promotion_gate.promote(
                        eligibility_run_id,
                        top_n=settings.top_n,
                        min_score=settings.min_score,
                        quota_date=settings.quota_date,
                        quota_key=settings.quota_key,
                        triggered_by=self.worker_identity,
                    )

                self._finish_run(
                    run_id,
                    source_run_id=source_id,
                    fingerprint=fingerprint,
                    successful=len(successful),
                    blocked=len(blocked),
                    eligibility_run_id=eligibility_run_id,
                    promotion=presentation_of(promotion),
                )
                return CommentPipelineResult(
                    pipeline_run_id=run_id,
                    source_run_id=source_id,
                    eligibility_run_id=eligibility_run_id,
                    promotion=promotion,
                    created=created,
                    videos=video_results,
                )
            finally:
                lock_cur.execute("select pg_advisory_unlock(%s)", (lock_key,))

    def _load_and_validate_source(self, source_run_id: UUID) -> tuple[list[_SourceVideo], str]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select run_type, platform, status
                from pipeline_run
                where id=%s
                """,
                (source_run_id,),
            )
            source = cur.fetchone()
            if source is None:
                raise ValueError("source pipeline run does not exist")
            run_type, declared_platform, status = source
            if run_type != "l0l1_discovery" or status != "success":
                raise ValueError("source must be a successful L0/L1 discovery run")
            if not declared_platform:
                raise ValueError("source L0/L1 run must declare a platform")
            cur.execute(
                """
                select v.id, v.platform_video_id, v.platform
                from pipeline_run_item i
                join source_video v on v.id=i.entity_id
                where i.run_id=%s
                  and i.entity_type='video'
                  and i.stage='L1'
                  and i.outcome='scored'
                order by v.id
                """,
                (source_run_id,),
            )
            videos = [_SourceVideo(*row) for row in cur.fetchall()]
        platforms = {item.platform for item in videos}
        if platforms and platforms != {declared_platform}:
            raise ValueError("source L0/L1 run platform does not match its video items")
        return videos, declared_platform

    def _get_or_create_run(
        self,
        *,
        source_id: UUID,
        platform: str,
        fingerprint: str,
        input_count: int,
    ) -> tuple[UUID, bool]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select id
                from pipeline_run
                where run_type='comment_l2_l3_batch'
                  and run_version=%s
                  and summary->>'source_run_id'=%s
                  and summary->>'settings_fingerprint'=%s
                order by started_at desc, id desc
                limit 1
                """,
                (self.rule_version, str(source_id), fingerprint),
            )
            existing = cur.fetchone()
            if existing is not None:
                return existing[0], False
            run_id = uuid4()
            cur.execute(
                """
                insert into pipeline_run(
                  id, run_type, run_version, platform, status, triggered_by,
                  input_count, summary
                ) values (%s,'comment_l2_l3_batch',%s,%s,'running',%s,%s,%s)
                """,
                (
                    run_id,
                    self.rule_version,
                    platform,
                    self.worker_identity,
                    input_count,
                    Jsonb(
                        {
                            "source_run_id": str(source_id),
                            "settings_fingerprint": fingerprint,
                            "worker_identity": self.worker_identity,
                            "external_calls": "per-video-comment-collection",
                            "llm_calls": 0,
                        }
                    ),
                ),
            )
            conn.commit()
        return run_id, True

    def _process_video(
        self,
        run_id: UUID,
        source: _SourceVideo,
        settings: CommentPipelineSettings,
    ) -> CommentPipelineVideoResult:
        existing = self._load_item(run_id, source.video_id)
        if existing and existing["outcome"] == "success" and existing["metadata"].get("extract_status") == "success":
            return CommentPipelineVideoResult(source.video_id, source.platform_video_id, "success", None, True)
        if existing and existing["metadata"].get("collection_status") == "collecting":
            self._upsert_item(
                run_id,
                source.video_id,
                stage="COMMENTS",
                outcome="reconciliation_required",
                reason_code="collection_outcome_unknown",
                metadata={"reconciliation_required": True},
            )
            return CommentPipelineVideoResult(
                source.video_id, source.platform_video_id, "reconciliation_required", "collection_outcome_unknown", True
            )

        collection_metadata: dict[str, Any]
        if existing and existing["metadata"].get("collection_status") == "success":
            collection_metadata = existing["metadata"]
        else:
            # Commit this state before the provider call.  A thrown exception is
            # not distinguishable from a completed remote billable request.
            self._upsert_item(
                run_id,
                source.video_id,
                stage="COMMENTS",
                outcome="collecting",
                reason_code=None,
                metadata={
                    "collection_status": "collecting",
                    "platform_video_id": source.platform_video_id,
                },
            )
            try:
                collection = self.collector.collect(
                    source.platform_video_id,
                    count=settings.comment_count,
                    max_pages=settings.max_pages,
                    max_items=settings.max_items,
                    sample_reason=settings.sample_reason,
                    triggered_by=self.worker_identity,
                )
            except Exception as exc:
                self._upsert_item(
                    run_id,
                    source.video_id,
                    stage="COMMENTS",
                    outcome="reconciliation_required",
                    reason_code="collection_outcome_unknown",
                    metadata={
                        "reconciliation_required": True,
                        "collection_error_type": type(exc).__name__,
                    },
                )
                return CommentPipelineVideoResult(
                    source.video_id, source.platform_video_id, "reconciliation_required", "collection_outcome_unknown", False
                )
            collection_metadata = {
                "collection_status": "success",
                "collection_run_id": str(collection.run_id),
                "comments_returned": collection.comments_returned,
                "observations_inserted": collection.observations_inserted,
                "external_pages": collection.external_pages,
                "estimated_api_cost_usd": collection.estimated_api_cost_usd,
                "cached": collection.cached,
            }
            self._upsert_item(
                run_id,
                source.video_id,
                stage="COMMENTS",
                outcome="collected",
                reason_code=None,
                metadata=collection_metadata,
            )

        try:
            snapshot = self.feature_extractor.extract(source.video_id)
        except Exception as exc:
            self._upsert_item(
                run_id,
                source.video_id,
                stage="L2",
                outcome="failed",
                reason_code="feature_extraction_failed",
                metadata={
                    **collection_metadata,
                    "extract_status": "failed",
                    "extract_error_type": type(exc).__name__,
                },
            )
            return CommentPipelineVideoResult(
                source.video_id, source.platform_video_id, "failed", "feature_extraction_failed", False
            )
        self._upsert_item(
            run_id,
            source.video_id,
            stage="L2",
            outcome="success",
            reason_code=None,
            metadata={
                **collection_metadata,
                "extract_status": "success",
                "feature_snapshot_id": snapshot.snapshot_id,
                "feature_fingerprint": snapshot.evidence_fingerprint,
            },
        )
        return CommentPipelineVideoResult(source.video_id, source.platform_video_id, "success", None, False)

    def _load_item(self, run_id: UUID, video_id: UUID) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select outcome, reason_code, metadata
                from pipeline_run_item
                where run_id=%s and entity_type='video' and entity_id=%s
                """,
                (run_id, video_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"outcome": row[0], "reason_code": row[1], "metadata": row[2] or {}}

    def _upsert_item(
        self,
        run_id: UUID,
        video_id: UUID,
        *,
        stage: str,
        outcome: str,
        reason_code: str | None,
        metadata: dict[str, Any],
    ) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into pipeline_run_item(
                  run_id, entity_type, entity_id, stage, outcome, reason_code, metadata
                ) values (%s,'video',%s,%s,%s,%s,%s)
                on conflict(run_id, entity_type, entity_id)
                do update set stage=excluded.stage, outcome=excluded.outcome,
                  reason_code=excluded.reason_code,
                  metadata=pipeline_run_item.metadata || excluded.metadata
                """,
                (run_id, video_id, stage, outcome, reason_code, Jsonb(metadata)),
            )
            conn.commit()

    def _get_or_create_eligibility_run(
        self,
        *,
        source_run_id: UUID,
        comment_pipeline_run_id: UUID,
        platform: str,
        successful_video_ids: set[UUID],
    ) -> UUID:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select entity_id, metadata->>'feature_fingerprint'
                from pipeline_run_item
                where run_id=%s and entity_type='video' and outcome='success'
                  and metadata->>'extract_status'='success'
                """,
                (comment_pipeline_run_id,),
            )
            features = {row[0]: row[1] for row in cur.fetchall() if row[0] in successful_video_ids}
            if set(features) != successful_video_ids or not all(features.values()):
                raise ValueError("eligibility requires persisted successful feature evidence")
            eligibility_fingerprint = _eligibility_fingerprint(comment_pipeline_run_id, features)
            cur.execute(
                """
                select id from pipeline_run
                where run_type='comment_l2_eligibility'
                  and summary->>'comment_pipeline_run_id'=%s
                  and summary->>'eligibility_fingerprint'=%s
                order by started_at desc, id desc
                limit 1
                """,
                (str(comment_pipeline_run_id), eligibility_fingerprint),
            )
            found = cur.fetchone()
            if found is not None:
                return found[0]
            run_id = uuid4()
            cur.execute(
                """
                insert into pipeline_run(
                  id, run_type, run_version, platform, status, triggered_by,
                  started_at, finished_at, input_count, output_count, summary
                ) values (%s,'comment_l2_eligibility',%s,%s,'success',%s,
                  now(),now(),%s,%s,%s)
                """,
                (
                    run_id,
                    self.rule_version,
                    platform,
                    self.worker_identity,
                    len(successful_video_ids),
                    len(successful_video_ids),
                    Jsonb(
                        {
                            "source_run_id": str(source_run_id),
                            "comment_pipeline_run_id": str(comment_pipeline_run_id),
                            "only_complete_t3_items": True,
                            "eligibility_fingerprint": eligibility_fingerprint,
                            "llm_calls": 0,
                            "external_calls": 0,
                        }
                    ),
                ),
            )
            for video_id in sorted(successful_video_ids, key=str):
                cur.execute(
                    """
                    insert into pipeline_run_item(
                      run_id, entity_type, entity_id, stage, outcome, metadata
                    ) values (%s,'video',%s,'L2','eligible',%s)
                    """,
                    (run_id, video_id, Jsonb({
                        "comment_pipeline_run_id": str(comment_pipeline_run_id),
                        "feature_fingerprint": features[video_id],
                    })),
                )
            conn.commit()
        return run_id

    def _finish_run(
        self,
        run_id: UUID,
        *,
        source_run_id: UUID,
        fingerprint: str,
        successful: int,
        blocked: int,
        eligibility_run_id: UUID | None,
        promotion: dict[str, Any] | None,
    ) -> None:
        # Failed/reconciliation-needed items intentionally leave the batch
        # resumable.  A subsequent execution sees durable item states rather
        # than relying on a provider's in-process response cache.
        status = "success" if blocked == 0 else "failed"
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update pipeline_run
                set status=%s, finished_at=now(), output_count=%s,
                    promoted_l2_count=%s,
                    summary=summary || %s,
                    error_summary=case when %s > 0 then %s else null end
                where id=%s
                """,
                (
                    status,
                    successful,
                    successful,
                    Jsonb(
                        {
                            "source_run_id": str(source_run_id),
                            "settings_fingerprint": fingerprint,
                            "successful_video_count": successful,
                            "blocked_video_count": blocked,
                            "eligibility_run_id": str(eligibility_run_id) if eligibility_run_id else None,
                            "promotion": promotion,
                        }
                    ),
                    blocked,
                    Jsonb({"blocked_video_count": blocked}) if blocked else Jsonb({}),
                    run_id,
                ),
            )
            conn.commit()


def _validate_settings(settings: CommentPipelineSettings) -> None:
    if not 1 <= settings.top_n <= MAX_TOP_N:
        raise ValueError(f"top_n must be between 1 and {MAX_TOP_N}")
    if not 0 <= settings.min_score <= 100:
        raise ValueError("min_score must be between 0 and 100")
    if not settings.quota_key or len(settings.quota_key) > 128:
        raise ValueError("quota_key is required and must be bounded")
    if not 1 <= settings.comment_count <= 100:
        raise ValueError("comment_count must be between 1 and 100")
    if not 1 <= settings.max_pages <= 10:
        raise ValueError("max_pages must be between 1 and 10")
    if not 1 <= settings.max_items <= 100:
        raise ValueError("max_items must be between 1 and 100")
    if not settings.sample_reason or len(settings.sample_reason) > 128:
        raise ValueError("sample_reason is required and must be bounded")


def _as_uuid(value: UUID | str, name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _settings_fingerprint(source_run_id: UUID, rule_version: str, settings: CommentPipelineSettings) -> str:
    payload = {
        "source_run_id": str(source_run_id),
        "rule_version": rule_version,
        "comment_count": settings.comment_count,
        "max_pages": settings.max_pages,
        "max_items": settings.max_items,
        "sample_reason": settings.sample_reason,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _eligibility_fingerprint(run_id: UUID, features: dict[UUID, str]) -> str:
    payload = {"run_id": str(run_id), "features": sorted((str(key), value) for key, value in features.items())}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _advisory_lock_key(source_run_id: UUID, rule_version: str) -> int:
    raw = hashlib.sha256(f"{source_run_id}:{rule_version}".encode()).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def presentation_of(result: PromotionResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "batch_id": str(result.batch_id),
        "pipeline_run_id": str(result.pipeline_run_id),
        "selected_count": result.selected_count,
        "created": result.created,
    }
