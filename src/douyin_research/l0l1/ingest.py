"""Idempotent L0 ingestion into canonical research tables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.providers.normalizer import normalize_account_profile
from douyin_research.providers.types import AccountRef, VideoObservation


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
    project_id: UUID | None = None


@dataclass(slots=True)
class IngestResult:
    video_ids: list[UUID]
    new_video_ids: list[UUID]
    new_platform_video_ids: list[str]
    new_videos: int
    discovery_inserted: int
    metric_inserted: int
    new_project_video_ids: list[UUID] = field(default_factory=list)


class L0L1Store:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ingest_verified_account_profile(
        self, account: AccountRef, *, endpoint_key: str,
        project_id: UUID, video_platform_id: str, actor: str,
    ) -> tuple[UUID, bool]:
        """Record a bound public profile only for an already-known stable account.

        No nickname/UID fallback or new account creation is permitted here.
        The raw provider envelope remains in external_api_response; the metric
        row carries only its reference and an idempotency key.
        """
        if (
            endpoint_key != "douyin.app.user_profile"
            or account.provider != "tikhub" or account.platform != "douyin"
            or not account.sec_user_id
            or account.platform_account_id != account.sec_user_id
            or account.follower_count is None or account.follower_count < 0
            or not account.raw_ref
            or not account.raw_ref.startswith("external_api_response:")
        ):
            raise ValueError("verified account profile is required")
        raw_id = account.raw_ref.removeprefix("external_api_response:")
        if not raw_id.isdecimal() or len(raw_id) > 20:
            raise ValueError("invalid account profile raw reference")
        key = "account-profile:" + hashlib.sha256(
            (account.platform + ":" + account.platform_account_id + ":" + account.raw_ref)
            .encode("utf-8")
        ).hexdigest()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """select account.platform_account_id
                   from research_project project
                   join research_organization organization
                     on organization.id=project.organization_id
                   join research_project_member member
                     on member.project_id=project.id and member.actor_id=%s
                    and member.role in ('owner','admin') and member.status='active'
                    and member.effective_from <= now()
                    and (member.effective_until is null or member.effective_until > now())
                   join project_video_inclusion inclusion
                     on inclusion.project_id=project.id and inclusion.status='accepted'
                   join source_video video on video.id=inclusion.video_id
                   join source_account account on account.id=video.account_id
                   where project.id=%s and project.status='active'
                     and organization.status='active'
                     and not exists (
                       select 1 from research_project_member newer
                       where newer.project_id=member.project_id
                         and newer.actor_id=member.actor_id
                         and newer.effective_from <= now()
                         and newer.effective_from > member.effective_from)
                     and video.platform='douyin' and account.platform='douyin'
                     and video.platform_video_id=%s
                   for share of project, organization, member, inclusion, video, account""",
                (actor, project_id, video_platform_id),
            )
            scope_row = cur.fetchone()
            if scope_row is None or scope_row[0] != account.platform_account_id:
                raise PermissionError("accepted project account identity is required")
            cur.execute(
                """select response_body, requested_at from external_api_response
                   where id=%s and provider=%s and platform=%s and endpoint_key=%s
                     and http_status=200 and response_code='200'""",
                (int(raw_id), account.provider, account.platform, endpoint_key),
            )
            raw_row = cur.fetchone()
            if raw_row is None:
                raise ValueError("verified account profile raw response is unavailable")
            confirmed = normalize_account_profile(
                raw_row[0], requested_sec_user_id=account.sec_user_id,
                raw_ref=account.raw_ref, observed_at=raw_row[1],
            )
            if confirmed.follower_count != account.follower_count:
                raise ValueError("account profile differs from raw response")
            cur.execute(
                """select id from source_account
                   where platform=%s and platform_account_id=%s
                   for share""",
                (account.platform, account.platform_account_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("account profile identity is not in the canonical catalogue")
            account_id = row[0]
            cur.execute(
                """insert into account_metric_snapshot(
                     account_id,provider,source_endpoint,observation_key,
                     captured_at,follower_count,raw_metrics
                   ) values (%s,%s,%s,%s,%s,%s,%s)
                   on conflict do nothing returning id""",
                (
                    account_id, account.provider, endpoint_key, key,
                    raw_row[1], account.follower_count,
                    Jsonb({"raw_ref": account.raw_ref, "identity_verified": True}),
                ),
            )
            inserted = cur.fetchone() is not None
            conn.commit()
        return account_id, inserted

    def create_run(
        self,
        run_type: str,
        run_version: str,
        triggered_by: str = "system",
        *,
        platform: str | None = None,
        project_id: UUID | None = None,
    ) -> UUID:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            if project_id is not None:
                cur.execute(
                    """select 1 from research_project p
                       join research_organization o on o.id = p.organization_id
                       where p.id = %s and p.status = 'active' and o.status = 'active'
                       for share of p, o""",
                    (project_id,),
                )
                if cur.fetchone() is None:
                    raise PermissionError("research project is unavailable")
            cur.execute(
                """
                insert into pipeline_run(run_type, run_version, platform, triggered_by, project_id)
                values (%s, %s, %s, %s, %s)
                returning id
                """,
                (run_type, run_version, platform, triggered_by, project_id),
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
        api_cost: float | None = None,
        cost_currency: str | None = None,
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
                    api_cost=coalesce(%s,api_cost),
                    cost_currency=coalesce(%s,cost_currency),
                    summary=summary || %s
                where id=%s
                """,
                (
                    status,
                    input_count,
                    output_count,
                    promoted_l1_count,
                    api_cost,
                    cost_currency,
                    Jsonb(summary or {}),
                    run_id,
                ),
            )
            conn.commit()

    def platform_video_ids(self, video_ids: Iterable[UUID]) -> set[str]:
        """Resolve canonical IDs for a scoped, already-ingested candidate set."""
        identifiers = list(dict.fromkeys(video_ids))
        if not identifiers:
            return set()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select platform_video_id from source_video where id=any(%s::uuid[])",
                (identifiers,),
            )
            return {str(row[0]) for row in cur.fetchall()}

    def ingest(
        self,
        observations: Iterable[VideoObservation],
        context: DiscoveryContext,
    ) -> IngestResult:
        observations = list(observations)
        video_ids: list[UUID] = []
        new_video_ids: list[UUID] = []
        new_project_video_ids: list[UUID] = []
        new_platform_video_ids: list[str] = []
        new_videos = 0
        discovery_inserted = 0
        metric_inserted = 0

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select project_id from pipeline_run where id = %s for share",
                (context.run_id,),
            )
            run_scope = cur.fetchone()
            if run_scope is None or run_scope[0] != context.project_id:
                raise ValueError("discovery context does not match the run project")
            if context.project_id is not None:
                cur.execute(
                    """select 1 from research_project p
                       join research_organization o on o.id = p.organization_id
                       where p.id = %s and p.status = 'active' and o.status = 'active'
                       for share of p, o""",
                    (context.project_id,),
                )
                if cur.fetchone() is None:
                    raise PermissionError("research project is unavailable")
            for obs in observations:
                self._validate_observation(obs)
                account_id = None
                if obs.account is not None:
                    account_id = self._upsert_account(cur, obs, context)

                video_id, inserted = self._upsert_video(cur, obs, account_id, context)
                if context.project_id is not None and context.record_discovery:
                    cur.execute(
                        """insert into project_video_inclusion(
                             project_id, video_id, source_run_id, source_type
                           ) values (%s, %s, %s, 'pipeline_run')
                           on conflict (project_id, video_id) do nothing
                           returning video_id""",
                        (context.project_id, video_id, context.run_id),
                    )
                    if cur.fetchone() is not None:
                        new_project_video_ids.append(video_id)
                    else:
                        cur.execute(
                            """update project_video_inclusion
                               set last_seen_at = now()
                               where project_id = %s and video_id = %s""",
                            (context.project_id, video_id),
                        )
                new_videos += int(inserted)
                video_ids.append(video_id)
                if inserted:
                    new_video_ids.append(video_id)
                    new_platform_video_ids.append(obs.video.platform_video_id)

                self._upsert_lineage(cur, "video", video_id, context.provider)

                if context.record_discovery:
                    discovery_key = _obs_key(
                        str(context.run_id),
                        "discovery",
                        context.source_type,
                        context.source_key,
                        obs.video.platform,
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
                        obs.video.platform,
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
                                "platform": obs.video.platform,
                                "source_type": context.source_type,
                                "source_key": context.source_key,
                            }
                        ),
                    ),
                )

            conn.commit()

        return IngestResult(
            video_ids=list(dict.fromkeys(video_ids)),
            new_video_ids=list(dict.fromkeys(new_video_ids)),
            new_platform_video_ids=list(dict.fromkeys(new_platform_video_ids)),
            new_videos=new_videos,
            discovery_inserted=discovery_inserted,
            metric_inserted=metric_inserted,
            new_project_video_ids=list(dict.fromkeys(new_project_video_ids)),
        )

    def set_new_candidate_flags(
        self,
        run_id: UUID,
        new_video_ids: Iterable[UUID],
    ) -> None:
        """Bind scheduled novelty to this immutable discovery run.

        Every L1 item remains scored evidence.  The explicit flag lets a
        scheduled downstream worker select only entities first inserted by
        this run, without changing manual replay behavior or relying on wall
        clock comparisons.
        """
        identifiers = list(dict.fromkeys(new_video_ids))
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update pipeline_run_item
                set metadata=metadata || %s
                where run_id=%s and entity_type='video' and stage='L1'
                """,
                (Jsonb({"new_candidate": False}), run_id),
            )
            if identifiers:
                cur.execute(
                    """
                    update pipeline_run_item
                    set metadata=metadata || %s
                    where run_id=%s and entity_type='video' and stage='L1'
                      and entity_id=any(%s)
                    """,
                    (Jsonb({"new_candidate": True}), run_id, identifiers),
                )
            conn.commit()

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
                account.platform,
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
    def _validate_observation(obs: VideoObservation) -> None:
        if not obs.video.platform:
            raise ValueError("video.platform is required")
        if obs.account is not None and obs.account.platform != obs.video.platform:
            raise ValueError(
                f"account platform {obs.account.platform} != video platform {obs.video.platform}"
            )
        if obs.metrics is not None and obs.metrics.platform != obs.video.platform:
            raise ValueError(
                f"metric platform {obs.metrics.platform} != video platform {obs.video.platform}"
            )

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
