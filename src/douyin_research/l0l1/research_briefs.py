"""Validated user-authored discovery plans for the research control plane.

The brief controls collection scope only.  It never creates ASR or L3
approvals and never submits content to an AI provider.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, Iterator
from uuid import UUID

import psycopg

from douyin_research.providers.endpoints import EndpointSpec
from douyin_research.providers.errors import ProviderBudgetError
from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider
from douyin_research.providers.transport import TikHubTransport
from douyin_research.providers.types import ProviderCallMeta

from .budget import DailyBudgetGuard
from .ingest import L0L1Store
from .runner import DiscoverySource, L0L1Runner
from .scoring import L1Scorer


RESEARCH_BRIEF_BUDGET_KEY = "research-brief"
RESEARCH_BRIEF_MAX_EXTERNAL_CALLS = 2
_DETAIL_ENDPOINTS = frozenset({
    "douyin.app.multi_video_v2", "douyin.app.one_video", "douyin.app.multi_video",
    "douyin.app.video_statistics", "douyin.app.multi_video_statistics",
})
_SOURCE_TYPES = frozenset({"low_fan", "keyword", "account"})
_DEPTHS = frozenset({"metadata", "comments", "media", "review_ready"})
_WINDOWS = frozenset({24, 72, 168, 720})
_CADENCES = frozenset({6, 12, 24})


@dataclass(frozen=True, slots=True)
class ResearchBriefConfig:
    platform: str
    source_type: str
    target: str | None
    time_window_hours: int
    max_items: int
    depth: str
    cadence_hours: int | None
    subject_id: str | None = None


class _RecordingProviderStore(PostgresProviderStore):
    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)
        self.recorded_calls: list[ProviderCallMeta] = []

    def record_call(self, call: ProviderCallMeta) -> None:
        self.recorded_calls.append(call)
        super().record_call(call)


def _integer(name: str, value: object, allowed: frozenset[int]) -> int:
    if type(value) is not int or value not in allowed:
        raise ValueError(f"{name} is invalid")
    return value


def validate_config(config: ResearchBriefConfig) -> ResearchBriefConfig:
    if config.platform != "douyin":
        raise ValueError("platform must be douyin")
    if config.source_type not in _SOURCE_TYPES:
        raise ValueError("source_type is invalid")
    if config.depth not in _DEPTHS:
        raise ValueError("depth is invalid")
    _integer("time_window_hours", config.time_window_hours, _WINDOWS)
    if config.cadence_hours is not None:
        _integer("cadence_hours", config.cadence_hours, _CADENCES)
    if type(config.max_items) is not int or not 1 <= config.max_items <= 20:
        raise ValueError("max_items is invalid")
    if config.depth in {"media", "review_ready"} and config.max_items > 5:
        raise ValueError("media scope must not exceed 5 items")
    if config.source_type == "low_fan":
        if config.target is not None:
            raise ValueError("low_fan target must be empty")
        if config.max_items > 5 or config.time_window_hours == 720:
            raise ValueError("low_fan scope exceeds its bounded endpoint")
    else:
        if not isinstance(config.target, str):
            raise ValueError("target is required")
        normalized = " ".join(config.target.strip().split())
        if not normalized or len(normalized) > 120 or "\x00" in normalized:
            raise ValueError("target is invalid")
        if normalized != config.target:
            raise ValueError("target must be normalized")
    if config.subject_id is not None:
        try:
            UUID(config.subject_id)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("subject_id is invalid") from None
    return config


def make_config(
    *,
    platform: str = "douyin",
    source_type: str,
    target: str | None,
    time_window_hours: int,
    max_items: int,
    depth: str,
    cadence_hours: int | None,
    subject_id: str | None = None,
) -> ResearchBriefConfig:
    normalized_target = None
    if source_type != "low_fan" and isinstance(target, str):
        normalized_target = " ".join(target.strip().split())
    return validate_config(
        ResearchBriefConfig(
            platform=platform,
            source_type=source_type,
            target=normalized_target,
            time_window_hours=time_window_hours,
            max_items=max_items,
            depth=depth,
            cadence_hours=cadence_hours,
            subject_id=subject_id,
        )
    )


def config_snapshot(config: ResearchBriefConfig) -> dict[str, Any]:
    """Durable server-side snapshot; never returned in paid job summaries."""
    validate_config(config)
    snapshot = asdict(config)
    # Preserve the exact legacy snapshot shape until a project brief opts into
    # a subject gate.  That makes old scheduled records replayable.
    if snapshot["subject_id"] is None:
        del snapshot["subject_id"]
    return snapshot


def discovery_source(
    config: ResearchBriefConfig, *, now: datetime | None = None,
) -> DiscoverySource:
    validate_config(config)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(hours=config.time_window_hours)
    target_digest = hashlib.sha256((config.target or "").encode("utf-8")).hexdigest()[:16]
    source_key = (
        f"brief:{config.source_type}:{target_digest}:{config.time_window_hours}h"
    )
    common = {
        "source_type": f"brief_{config.source_type}",
        "source_key": source_key,
        "max_items": config.max_items,
        "published_after": cutoff,
    }
    if config.source_type == "low_fan":
        return DiscoverySource(
            kind="low_fan",
            kwargs={
                "page": 1,
                "page_size": config.max_items,
                "date_window": config.time_window_hours,
                "tags": [],
                "force_refresh": False,
            },
            **common,
        )
    if config.source_type == "keyword":
        # TikHub only offers 1-day, 7-day and 180-day search windows.  Use the
        # smallest provider window that contains the requested scope and keep
        # ``published_after`` as the exact local boundary (for example 72h).
        publish_time = {
            24: "1",
            72: "7",
            168: "7",
            720: "180",
        }[config.time_window_hours]
        return DiscoverySource(
            kind="search",
            kwargs={
                "query": config.target,
                "sort_type": "2",
                "publish_time": publish_time,
                "force_refresh": False,
            },
            **common,
        )
    return DiscoverySource(
        kind="account_posts",
        kwargs={
            "account_id": config.target,
            "count": config.max_items,
            "force_refresh": False,
        },
        **common,
    )


def depth_plan(depth: str) -> dict[str, bool]:
    if depth not in _DEPTHS:
        raise ValueError("depth is invalid")
    return {
        "collect_comments": depth in {"comments", "media", "review_ready"},
        "collect_media": depth in {"media", "review_ready"},
        "review_required": depth == "review_ready",
        "auto_submit_asr": False,
        "auto_submit_l3": False,
    }


def _run_call_gate(
    daily_reserve: Callable[[EndpointSpec], None],
    *, project_check: Callable[[], None] | None = None,
) -> Callable[[EndpointSpec], None]:
    count = 0

    def reserve(spec: EndpointSpec) -> None:
        nonlocal count
        if count >= RESEARCH_BRIEF_MAX_EXTERNAL_CALLS:
            raise ProviderBudgetError("research brief external-call limit exceeded")
        if project_check is not None:
            project_check()
        daily_reserve(spec)
        count += 1

    return reserve


def _ensure_active_project(dsn: str, project_id: UUID, subject_id: UUID | None = None) -> None:
    # Cheap preflight; the uncached transport guard below provides the
    # serialized check at the actual external-call boundary.
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """select 1 from research_project p
               join research_organization o on o.id = p.organization_id
               left join research_subject s on s.id=%s and s.project_id=p.id
               where p.id = %s and p.status = 'active' and o.status = 'active'
                 and (%s::uuid is null or s.status='active')""",
            (subject_id, project_id, subject_id),
        ).fetchone()
    if row is None:
        raise PermissionError("research project is unavailable")


@contextmanager
def _project_detail_transport_guard(
    dsn: str, project_id: UUID, subject_id: UUID, video_ids: tuple[str, ...],
) -> Iterator[None]:
    """Serialize an uncached paid detail call with project eligibility edits.

    The transaction stays open through the single-attempt HTTP call (30s
    timeout). A review or project pause committed first rejects the call;
    a concurrent edit waits for the already-authorized call to finish.
    """
    if not video_ids or len(video_ids) != len(set(video_ids)):
        raise PermissionError("project detail request has invalid video IDs")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select video.platform_video_id
                   from research_project project
                   join research_organization organization
                     on organization.id=project.organization_id
                   join research_subject subject
                     on subject.project_id=project.id
                   join project_video_inclusion inclusion
                     on inclusion.project_id=project.id
                   join source_video video on video.id=inclusion.video_id
                   join project_video_subject_relevance relevance
                     on relevance.project_id=project.id
                    and relevance.video_id=video.id
                    and relevance.subject_id=subject.id
                   where project.id=%s and subject.id=%s
                     and project.status='active' and organization.status='active'
                     and subject.status='active'
                     and inclusion.status in ('candidate','shortlisted','accepted')
                     and relevance.decision='relevant'
                     and video.platform='douyin'
                     and video.availability_status='available'
                     and video.platform_video_id=any(%s::text[])
                   for share of project, organization, subject, inclusion, video, relevance""",
                (project_id, subject_id, list(video_ids)),
            )
            allowed = {row[0] for row in cur.fetchall()}
            if allowed != set(video_ids):
                raise PermissionError("project detail request is no longer eligible")
            yield


@contextmanager
def _project_active_transport_guard(
    dsn: str, project_id: UUID, subject_id: UUID,
) -> Iterator[None]:
    """Keep a paid discovery call serialized with project or subject pause."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select 1 from research_project project
                   join research_organization organization
                     on organization.id=project.organization_id
                   join research_subject subject
                     on subject.project_id=project.id
                   where project.id=%s and subject.id=%s
                     and project.status='active' and organization.status='active'
                     and subject.status='active'
                   for share of project, organization, subject""",
                (project_id, subject_id),
            )
            if cur.fetchone() is None:
                raise PermissionError("research project is unavailable")
            yield


def _project_uncached_guard(
    dsn: str, project_id: UUID, subject_id: UUID,
) -> Callable[[EndpointSpec, tuple[str, ...] | None], ContextManager[None]]:
    def guard(spec: EndpointSpec, video_ids: tuple[str, ...] | None) -> ContextManager[None]:
        if spec.key in _DETAIL_ENDPOINTS:
            if video_ids is None:
                raise PermissionError("project detail request is missing exact video IDs")
            return _project_detail_transport_guard(dsn, project_id, subject_id, video_ids)
        if video_ids is not None:
            raise PermissionError("project video IDs used on a non-detail endpoint")
        return _project_active_transport_guard(dsn, project_id, subject_id)

    return guard


def run_live(
    *, dsn: str, api_key: str, config: ResearchBriefConfig, triggered_by: str,
    project_id: UUID | None = None,
) -> dict[str, Any]:
    validate_config(config)
    if not dsn or not api_key or not triggered_by:
        raise ValueError("database, API key and service identity are required")
    subject_id = UUID(config.subject_id) if config.subject_id is not None else None
    if (project_id is None) != (subject_id is None):
        raise ValueError("project research requires a subject")
    if project_id is not None:
        _ensure_active_project(dsn, project_id, subject_id)

    budget = DailyBudgetGuard(dsn)
    budget.configure(
        provider="tikhub",
        budget_key=RESEARCH_BRIEF_BUDGET_KEY,
        max_requests=None,
        max_cost=None,
        cost_currency="USD",
    )
    transport = TikHubTransport(api_key, max_retries=0)
    provider_store = _RecordingProviderStore(dsn)
    provider = TikHubDouyinProvider(
        transport=transport,
        store=provider_store,
        auth_scope="research-brief-v1",
        before_external_call=_run_call_gate(
            budget.make_before_external_call(
                provider="tikhub", budget_key=RESEARCH_BRIEF_BUDGET_KEY,
            ),
            project_check=(
                (lambda: _ensure_active_project(dsn, project_id))
                if project_id is not None and subject_id is None
                else (lambda: _ensure_active_project(dsn, project_id, subject_id))
                if project_id is not None else None
            ),
        ),
        uncached_transport_guard=(
            _project_uncached_guard(dsn, project_id, subject_id)
            if project_id is not None and subject_id is not None else None
        ),
        detail_strategy="batch50",
    )
    store = L0L1Store(dsn)
    runner = L0L1Runner(
        provider=provider,
        store=store,
        scorer=L1Scorer(dsn),
        budget=budget,
        budget_key=RESEARCH_BRIEF_BUDGET_KEY,
        provider_reserves_budget=True,
    )
    try:
        summary = runner.run(
            [discovery_source(config)],
            enrich_details=True,
            triggered_by=triggered_by,
            enrich_new_only=True,
            project_id=project_id,
            subject_id=subject_id,
        )
    finally:
        transport.close()

    calls = provider_store.recorded_calls
    cached_calls = sum(call.cached for call in calls)
    uncached_calls = len(calls) - cached_calls
    estimated_cost = sum(float(call.estimated_cost or 0) for call in calls)
    unknown_cost_calls = sum(
        call.metadata.get("billing_status") == "unknown" for call in calls
    )
    cost_summary = {
        "estimated_api_cost_usd": estimated_cost,
        "reconciled_api_cost_usd": None,
        "unknown_cost_calls": unknown_cost_calls,
        "api_cost_basis": "unknown" if unknown_cost_calls else "estimated",
    }
    store.finish_run(
        summary.run_id,
        api_cost=estimated_cost,
        cost_currency="USD",
        summary=cost_summary,
    )
    return {
        "run_id": str(summary.run_id),
        "platform": summary.platform,
        "observations": summary.observations,
        "unique_platform_videos": summary.unique_platform_videos,
        "new_candidate_count": summary.new_candidate_count,
        "relevant_new_project_count": summary.relevant_new_project_count,
        "subject_scoring_status": "project_subject_l1" if project_id is not None else "global_l1",
        "relevant_candidate_count": summary.relevant_candidate_count,
        "pending_candidate_count": summary.pending_candidate_count,
        "irrelevant_candidate_count": summary.irrelevant_candidate_count,
        "scored_videos": len(summary.scores),
        "provider_call_count": len(calls),
        "cached_call_count": cached_calls,
        "uncached_call_count": uncached_calls,
        "max_external_calls": RESEARCH_BRIEF_MAX_EXTERNAL_CALLS,
        "sdk_retries": 0,
        "cost_summary": cost_summary,
        **depth_plan(config.depth),
    }
