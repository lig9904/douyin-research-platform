"""Idempotent L0 ingestion into canonical research tables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.providers.types import VideoObservation


@dataclass(slots=True)
class DiscoveryContext:
    run_id: UUID
    source_type: str
    source_key: str
    provider: str
    request_fingerprint: str
    source_count: int | None = None
    ranks: dict[str, int] | None = None
    record_discovery: bool = True


@dataclass(slots=True)
class IngestResult:
    video_ids: list[UUID]
    new_videos: int
    discovery_inserted: int
    metric_inserted: int


class L0L1Store:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def create_run(self, run_type: str, run_version: str, triggered_by: str = "system") -> UUID:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into pipeline_run(run_type, run_version, triggered_by)
                values (%s, %s, %s)
                returning id
                """,
                (run_type, run_version, triggered_by),
            )
            run_id = cur.fetchone()[0]
            conn.commit()
        return run_id

    def finish_run(
        self,
        run_id: UUID,
        *,
        status: str = "success",
        input_count: int | None = None,
        output_count: int | None = None,
        promoted_l1_count: int | None = None,
        summary: dict | None = None,
    ) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update pipeline_run
                set status=%s,
                    finished_at=now(),
                    input_count=coalesce(%s,input_count),
                    output_count=coalesce(%s,output_count),
                    promoted_l1_count=coalesce(%s,promoted_l1_count),
                    summary=summary || %s
                where id=%s
                """,
                (
                    status,
                    input_count,
                    output_count,
                    promoted_l1_count,
                    Jsonb(summary or {}),
                    run_id,
                ),
            )
            conn.commit()

    def ingest(
        self,
        observations: Iterable[VideoObservation],
        context: DiscoveryContext,
    ) -> IngestResult:
        observations = list(observations)
        video_ids: list[UUID] = []
        new_videos = 0
        discovery_inserted = 0
        metric_inserted = 0

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            for obs in observations:
                account_id = None
                if obs.account is not None:
                    account_id = self._upsert_account(cur, obs, context)

                video_id, inserted = self._upsert_video(cur, obs, account_id, context)
                new_videos += int(inserted)
                video_ids.append(video_id)

                self._upsert_lineage(cur, "video", video_id, context.provider)

                if context.record_discovery:
                    discovery_key = _obs_key(
                        str(context.run_id),
                        "discovery",
                        context.source_type,
                        context.source_key,
                        obs.video.platform_video_id,
                    )
                    rank = (context.ranks or {}).get(obs.video.platform_video_id)
                    cur.execute(
                        """
                        insert into discovery_event(
                          video_id, provider, source_type, source_key, observation_key,
                          discovered_at, rank_value, rule_version, metadata
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict do nothing
                        """,
                        (
                            video_id,
                            context.provider,
                            context.source_type,
                            context.source_key,
                            discovery_key,
                            obs.video.observed_at or _utcnow(),
                            rank,
                            "discovery-v1.0.0",
                            Jsonb(
                                {
                                    "run_id": str(context.run_id),
                                    "request_fingerprint": context.request_fingerprint,
                                    "source_count": context.source_count,
                                }
                            ),
                        ),
                    )
                    discovery_inserted += cur.rowcount

                if obs.metrics is not None:
                    metric_key = _obs_key(
                        str(context.run_id),
                        "metric",
                        obs.metrics.source_endpoint,
                        obs.video.platform_video_id,
                    )
                    cur.execute(
                        """
                        insert into metric_snapshot(
                          video_id, provider, source_endpoint, observation_key, captured_at,
                          play_count, like_count, comment_count, share_count, collect_count,
                          author_follower_count, raw_metrics
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict do nothing
                        """,
                        (
                            video_id,
                            obs.metrics.provider,
                            obs.metrics.source_endpoint,
                            metric_key,
                            obs.metrics.captured_at,
                            obs.metrics.play_count,
                            obs.metrics.like_count,
                            obs.metrics.comment_count,
                            obs.metrics.share_count,
                            obs.metrics.collect_count,
                            obs.metrics.author_follower_count,
                            Jsonb({"metric_status": obs.metrics.metric_status}),
                        ),
                    )
                    metric_inserted += cur.rowcount

                cur.execute(
                    """
                    insert into pipeline_run_item(
                      run_id, entity_type, entity_id, stage, outcome, reason_code, metadata
                    )
                    values (%s,'video',%s,'L0','ingested',null,%s)
                    on conflict(run_id, entity_type, entity_id)
                    do update set metadata = pipeline_run_item.metadata || excluded.metadata
                    """,
                    (
                        context.run_id,
                        video_id,
                        Jsonb(
                            {
                                "source_type": context.source_type,
                                "source_key": context.source_key,
                            }
                        ),
                    ),
                )

            conn.commit()

        return IngestResult(
            video_ids=list(dict.fromkeys(video_ids)),
            new_videos=new_videos,
            discovery_inserted=discovery_inserted,
            metric_inserted=metric_inserted,
        )

    def _upsert_account(self, cur, obs: VideoObservation, context: DiscoveryContext) -> UUID:
        account = obs.account
        assert account is not None
        cur.execute(
            """
            insert into source_account(
              platform, platform_account_id, nickname, profile_url, first_seen_at, last_seen_at
            )
            values (%s,%s,%s,%s,%s,%s)
            on conflict(platform, platform_account_id)
            do update set
              nickname=coalesce(excluded.nickname, source_account.nickname),
              profile_url=coalesce(excluded.profile_url, source_account.profile_url),
              last_seen_at=greatest(source_account.last_seen_at, excluded.last_seen_at)
            returning id
            """,
            (
                account.platform,
                account.platform_account_id,
                account.nickname,
                account.profile_url,
                account.observed_at or _utcnow(),
                account.observed_at or _utcnow(),
            ),
        )
        account_id = cur.fetchone()[0]
        self._upsert_lineage(cur, "account", account_id, context.provider)

        if account.follower_count is not None:
            key = _obs_key(
                str(context.run_id),
                "account_metric",
                context.source_type,
                account.platform_account_id,
            )
            cur.execute(
                """
                insert into account_metric_snapshot(
                  account_id, provider, source_endpoint, observation_key, captured_at,
                  follower_count, raw_metrics
                )
                values (%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                """,
                (
                    account_id,
                    context.provider,
                    context.source_key,
                    key,
                    account.observed_at or _utcnow(),
                    account.follower_count,
                    Jsonb({}),
                ),
            )
        return account_id

    def _upsert_video(
        self,
        cur,
        obs: VideoObservation,
        account_id: UUID | None,
        context: DiscoveryContext,
    ) -> tuple[UUID, bool]:
        v = obs.video
        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, account_id, title, description, source_url,
              published_at, duration_ms, availability_status, first_seen_at, last_seen_at
            )
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict(platform, platform_video_id)
            do update set
              account_id=coalesce(source_video.account_id, excluded.account_id),
              title=coalesce(excluded.title, source_video.title),
              description=coalesce(excluded.description, source_video.description),
              source_url=coalesce(excluded.source_url, source_video.source_url),
              published_at=coalesce(source_video.published_at, excluded.published_at),
              duration_ms=coalesce(excluded.duration_ms, source_video.duration_ms),
              availability_status=excluded.availability_status,
              last_seen_at=greatest(source_video.last_seen_at, excluded.last_seen_at)
            returning id, (xmax = 0) as inserted
            """,
            (
                v.platform,
                v.platform_video_id,
                account_id,
                v.title,
                v.description,
                v.source_url,
                v.published_at,
                v.duration_ms,
                v.availability_status,
                v.observed_at or _utcnow(),
                v.observed_at or _utcnow(),
            ),
        )
        return cur.fetchone()

    @staticmethod
    def _upsert_lineage(cur, entity_type: str, entity_id: UUID, provider: str) -> None:
        cur.execute(
            """
            insert into provider_entity_lineage(
              provider, entity_type, entity_id, source_mode,
              first_observed_at, last_observed_at, observation_count
            )
            values (%s,%s,%s,'api',now(),now(),1)
            on conflict(provider, entity_type, entity_id)
            do update set
              last_observed_at=now(),
              observation_count=provider_entity_lineage.observation_count+1
            """,
            (provider, entity_type, entity_id),
        )


def _obs_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
