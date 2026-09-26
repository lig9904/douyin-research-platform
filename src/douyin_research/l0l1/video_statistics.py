"""Audited play-count verification for accepted project videos.

The statistics route is deliberately separate from video discovery: it cannot
create a video, change its author, or silently replace the original detail row.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Iterator
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.providers.normalizer import normalize_exact_video_statistics
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider
from douyin_research.providers.transport import ProviderTransport, TikHubTransport

from .account_profiles import _RecordingStore, _cost_summary
from .budget import DailyBudgetGuard
from .ingest import L0L1Store


_VIDEO_ID = re.compile(r"[0-9]{15,25}\Z")
_ACTOR = re.compile(r"[^\s@]{1,128}@[^\s@]{1,120}\Z")
_ENDPOINT = "douyin.app.video_statistics"
_BUDGET_KEY = "video_statistics_refresh"


@dataclass(frozen=True, slots=True)
class VideoStatisticsResult:
    run_id: UUID
    play_counts: dict[str, int]
    snapshots_inserted: int
    external_calls: int
    cached_calls: int
    estimated_api_cost_usd: float | None


def _accepted_videos(
    cur: psycopg.Cursor[Any], project_id: UUID, ids: tuple[str, ...],
    actor: str, *, lock: bool = False,
) -> dict[str, UUID]:
    cur.execute(
        """select video.platform_video_id, video.id
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
           where project.id=%s and project.status='active'
             and organization.status='active' and video.platform='douyin'
             and video.platform_video_id=any(%s::text[])
             and not exists (
               select 1 from research_project_member newer
               where newer.project_id=member.project_id
                 and newer.actor_id=member.actor_id
                 and newer.effective_from <= now()
                 and newer.effective_from > member.effective_from)
        """ + (
            # A new membership row obtains KEY SHARE on its project FK. UPDATE
            # blocks that insertion until this paid-call transaction finishes;
            # merely sharing the existing member row would not block it.
            " for update of project"
            " for share of organization, member, inclusion, video"
            if lock else ""
        ),
        (actor, project_id, list(ids)),
    )
    found = dict(cur.fetchall())
    if set(found) != set(ids):
        raise PermissionError("accepted project videos and manager role are required")
    return found


@contextmanager
def _paid_video_guard(
    dsn: str, project_id: UUID, ids: tuple[str, ...], actor: str,
) -> Iterator[None]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        _accepted_videos(cur, project_id, ids, actor, lock=True)
        yield


@contextmanager
def _statistics_cache_lock(dsn: str, ids: tuple[str, ...]) -> Iterator[None]:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 93018))",
            (",".join(ids),),
        )
        yield


def _insert_snapshots(
    dsn: str, *, project_id: UUID, ids: tuple[str, ...], actor: str,
    raw_ref: str, observations: list[Any],
) -> int:
    if not raw_ref.startswith("external_api_response:"):
        raise ValueError("statistics response is not persistently recorded")
    raw_id = raw_ref.removeprefix("external_api_response:")
    if not raw_id.isdecimal() or len(raw_id) > 20:
        raise ValueError("invalid statistics raw reference")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        videos = _accepted_videos(cur, project_id, ids, actor, lock=True)
        cur.execute(
            """select response_body, requested_at from external_api_response
               where id=%s and provider='tikhub' and platform='douyin'
                 and endpoint_key=%s and http_status=200 and response_code='200'""",
            (int(raw_id), _ENDPOINT),
        )
        raw = cur.fetchone()
        if raw is None:
            raise ValueError("statistics raw response is unavailable")
        verified = normalize_exact_video_statistics(
            raw[0], video_ids=ids, raw_ref=raw_ref, observed_at=raw[1],
        )
        expected = {
            item.video.platform_video_id: item.metrics.play_count
            for item in observations if item.metrics is not None
        }
        if expected != {
            item.video.platform_video_id: item.metrics.play_count
            for item in verified if item.metrics is not None
        }:
            raise ValueError("normalized statistics differ from raw response")
        inserted = 0
        for item in verified:
            metrics = item.metrics
            assert metrics is not None
            key = "video-statistics:" + hashlib.sha256(
                (item.video.platform_video_id + ":" + raw_ref).encode("utf-8")
            ).hexdigest()
            cur.execute(
                """insert into metric_snapshot(
                     video_id,provider,source_endpoint,observation_key,captured_at,
                     play_count,like_count,comment_count,share_count,collect_count,
                     raw_metrics)
                   values (%s,'tikhub',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict do nothing returning id""",
                (
                    videos[item.video.platform_video_id], _ENDPOINT, key,
                    raw[1], metrics.play_count, metrics.like_count,
                    metrics.comment_count, metrics.share_count,
                    metrics.collect_count,
                    Jsonb({"raw_ref": raw_ref, "exact_id_verified": True,
                           "metric_status": metrics.metric_status}),
                ),
            )
            inserted += int(cur.fetchone() is not None)
        conn.commit()
        return inserted


def refresh_project_video_statistics(
    *, dsn: str, project_id: UUID, video_platform_ids: Iterable[str],
    actor: str, api_key: str, transport: ProviderTransport | None = None,
) -> VideoStatisticsResult:
    """Refresh one or two accepted exact IDs in a single priced supplier call."""
    ids = tuple(video_platform_ids)
    if not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids) or any(
        not isinstance(value, str) or _VIDEO_ID.fullmatch(value) is None
        for value in ids
    ):
        raise ValueError("one or two distinct exact Douyin video IDs are required")
    if not isinstance(project_id, UUID):
        raise ValueError("project_id must be a UUID")
    if not isinstance(actor, str) or actor != actor.strip().lower() or (
        _ACTOR.fullmatch(actor) is None or len(actor) > 254
    ):
        raise PermissionError("authenticated project manager is required")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("TikHub secret is required")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        _accepted_videos(cur, project_id, ids, actor)
        cur.execute(
            """insert into daily_budget(
                 budget_date,provider,budget_key,max_requests,max_cost,cost_currency)
               values (%s,'tikhub',%s,null,null,'USD')
               on conflict(budget_date,provider,budget_key) do nothing""",
            (date.today(), _BUDGET_KEY),
        )
        conn.commit()

    run_store = L0L1Store(dsn)
    run_id = run_store.create_run(
        "video_statistics_refresh", "v1", triggered_by=actor,
        platform="douyin", project_id=project_id,
    )
    owned_transport = transport is None
    active_transport = transport or TikHubTransport(api_key, max_retries=0)
    provider_store = _RecordingStore(dsn, run_id=run_id, project_id=project_id)
    reserve = DailyBudgetGuard(dsn).make_before_external_call(
        provider="tikhub", budget_key=_BUDGET_KEY,
    )
    inserted = 0
    try:
        provider = TikHubDouyinProvider(
            transport=active_transport, store=provider_store,
            before_external_call=reserve,
            uncached_transport_guard=lambda _spec, requested: _paid_video_guard(
                dsn, project_id, requested or (), actor,
            ),
        )
        with _statistics_cache_lock(dsn, ids):
            page = provider.fetch_exact_video_statistics(ids)
        if page.raw_ref is None:
            raise ValueError("statistics raw reference is missing")
        inserted = _insert_snapshots(
            dsn, project_id=project_id, ids=ids, actor=actor,
            raw_ref=page.raw_ref, observations=page.items,
        )
        counts = {
            item.video.platform_video_id: item.metrics.play_count
            for item in page.items if item.metrics is not None
        }
        estimated, basis, unknown = _cost_summary(provider_store.calls)
        run_store.finish_run(
            run_id, status="success", input_count=len(ids),
            output_count=inserted, api_cost=estimated, cost_currency="USD",
            summary={"api_cost_basis": basis, "unknown_cost_calls": unknown,
                     "snapshots_inserted": inserted,
                     "cached_calls": int(page.cached)},
        )
        return VideoStatisticsResult(
            run_id=run_id, play_counts=counts, snapshots_inserted=inserted,
            external_calls=sum(not call.cached for call in provider_store.calls),
            cached_calls=int(page.cached), estimated_api_cost_usd=estimated,
        )
    except Exception as exc:
        estimated, basis, unknown = _cost_summary(provider_store.calls)
        run_store.finish_run(
            run_id, status="failed", input_count=len(ids),
            output_count=inserted, api_cost=estimated, cost_currency="USD",
            summary={
                "api_cost_basis": basis if unknown == 0 else "unknown",
                "known_estimated_cost_usd": sum(
                    float(call.estimated_cost or 0) for call in provider_store.calls
                    if not call.cached and call.status == "success"
                    and call.estimated_cost is not None
                ),
                "unknown_cost_calls": unknown, "error_type": type(exc).__name__,
            },
        )
        raise RuntimeError("video statistics refresh failed") from None
    finally:
        if owned_transport:
            active_transport.close()
