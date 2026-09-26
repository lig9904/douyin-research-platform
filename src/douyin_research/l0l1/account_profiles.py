"""Project-bound, auditable refresh of public Douyin account profiles.

Only stable accounts attached to accepted project videos are eligible. This
module is a privileged service entry point, not an unauthenticated HTTP API.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable, Iterator
from uuid import UUID

import psycopg

from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.normalizer import normalize_video_observations
from douyin_research.providers.errors import ProviderSchemaError
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider
from douyin_research.providers.transport import ProviderTransport, TikHubTransport
from douyin_research.providers.types import ProviderCallMeta

from .budget import DailyBudgetGuard
from .ingest import L0L1Store


_VIDEO_ID = re.compile(r"[0-9]{15,25}\Z")
_BUDGET_KEY = "account_profile_refresh"


@dataclass(frozen=True, slots=True)
class AccountProfileRefreshResult:
    run_id: UUID
    requested_video_count: int
    distinct_account_count: int
    snapshots_inserted: int
    external_calls: int
    cached_calls: int
    estimated_api_cost_usd: float | None


class _RecordingStore(PostgresProviderStore):
    def __init__(self, dsn: str, *, run_id: UUID, project_id: UUID) -> None:
        super().__init__(dsn)
        self.run_id = run_id
        self.project_id = project_id
        self.calls: list[ProviderCallMeta] = []

    def record_call(self, call: ProviderCallMeta) -> None:
        call = replace(call, metadata={
            **call.metadata, "pipeline_run_id": str(self.run_id),
            "project_id": str(self.project_id),
        })
        super().record_call(call)
        self.calls.append(call)


def _accepted_account_cursor(
    cur: psycopg.Cursor[Any], project_id: UUID, video_platform_id: str,
    *, lock: bool = False,
) -> str:
    cur.execute(
            """select account.platform_account_id, video.id
               from research_project project
               join research_organization organization
                 on organization.id=project.organization_id
               join project_video_inclusion inclusion
                 on inclusion.project_id=project.id and inclusion.status='accepted'
               join source_video video on video.id=inclusion.video_id
               join source_account account on account.id=video.account_id
               where project.id=%s and project.status='active'
                 and organization.status='active'
                 and video.platform='douyin' and account.platform='douyin'
                 and video.platform_video_id=%s
               """ + (
                " for share of project, organization, inclusion, video, account"
                if lock else ""
            ),
            (project_id, video_platform_id),
        )
    row = cur.fetchone()
    if row is None or not isinstance(row[0], str) or not row[0].strip():
        raise PermissionError("accepted project video with stable account is required")
    stable_id, video_id = row
    cur.execute(
        """select response.response_body,response.endpoint_key,response.requested_at
           from discovery_event discovery
           join external_api_response response
             on response.provider=discovery.provider
            and response.request_fingerprint=discovery.metadata->>'request_fingerprint'
           where discovery.video_id=%s and discovery.provider='tikhub'
             and response.platform='douyin' and response.response_code='200'
             and response.http_status=200
           order by response.requested_at desc limit 30""",
        (video_id,),
    )
    for payload, endpoint_key, observed_at in cur.fetchall():
        try:
            observations = normalize_video_observations(
                payload, endpoint_key=endpoint_key, raw_ref=None,
                observed_at=observed_at,
            )
        except (ProviderSchemaError, TypeError, ValueError):
            continue
        if any(
            obs.video.platform_video_id == video_platform_id
            and obs.account is not None and obs.account.sec_user_id == stable_id
            for obs in observations
        ):
            return stable_id
    raise PermissionError("accepted video lacks verified sec_user_id evidence")


def _accepted_account(dsn: str, project_id: UUID, video_platform_id: str) -> str:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        return _accepted_account_cursor(cur, project_id, video_platform_id)


@contextmanager
def _paid_account_guard(
    dsn: str, project_id: UUID, video_platform_id: str, stable_id: str,
) -> Iterator[None]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if _accepted_account_cursor(cur, project_id, video_platform_id, lock=True) != stable_id:
            raise PermissionError("project account identity changed")
        yield


@contextmanager
def _account_cache_lock(dsn: str, stable_id: str) -> Iterator[None]:
    # Serialize the cache decision as well as HTTP across concurrent runs.
    with psycopg.connect(dsn) as conn:
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 93017))", (stable_id,))
        yield


def _ensure_accounting_row(dsn: str) -> None:
    """Create an uncapped row once; never overwrite an operator's later cap."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """insert into daily_budget(
                 budget_date,provider,budget_key,max_requests,max_cost,cost_currency)
               values (%s,'tikhub',%s,null,null,'USD')
               on conflict(budget_date,provider,budget_key) do nothing""",
            (date.today(), _BUDGET_KEY),
        )


def _cost_summary(calls: list[ProviderCallMeta]) -> tuple[float | None, str, int]:
    external = [call for call in calls if not call.cached]
    uncertain = [
        call for call in external
        if call.status != "success" or call.estimated_cost is None
    ]
    if uncertain:
        return None, "unknown", len(uncertain)
    return sum(float(call.estimated_cost or 0) for call in external), (
        "estimated" if external else "actual"
    ), 0


def refresh_project_account_profiles(
    *, dsn: str, project_id: UUID, video_platform_ids: Iterable[str],
    api_key: str, transport: ProviderTransport | None = None,
) -> AccountProfileRefreshResult:
    """Refresh at most ten exact accepted-video accounts, once per stable ID.

    Caller holds privileged database and TikHub credentials. Supplier charges
    remain estimates until the daily usage statement is reconciled.
    """
    ids = tuple(video_platform_ids)
    if not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids) or any(
        not isinstance(item, str) or _VIDEO_ID.fullmatch(item) is None for item in ids
    ):
        raise ValueError("one to ten distinct exact video IDs are required")
    if not isinstance(project_id, UUID):
        raise ValueError("project_id must be a UUID")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("TikHub secret is required")

    accounts: dict[str, str] = {}
    for video_id in ids:
        accounts[video_id] = _accepted_account(dsn, project_id, video_id)
    account_to_video = {stable_id: video_id for video_id, stable_id in accounts.items()}
    _ensure_accounting_row(dsn)

    run_store = L0L1Store(dsn)
    run_id = run_store.create_run(
        "account_profile_refresh", "v1", triggered_by="privileged_operator",
        platform="douyin", project_id=project_id,
    )
    owned_transport = transport is None
    active_transport = transport or TikHubTransport(api_key, max_retries=0)
    provider_store = _RecordingStore(dsn, run_id=run_id, project_id=project_id)
    budget = DailyBudgetGuard(dsn)
    reserve = budget.make_before_external_call(
        provider="tikhub", budget_key=_BUDGET_KEY,
    )
    inserted = 0
    cached = 0
    try:
        for stable_id, video_id in account_to_video.items():
            # The gate is checked again at the outbound boundary and before
            # committing a canonical snapshot; revocation cannot be bypassed
            # with a warm provider cache.
            provider = TikHubDouyinProvider(
                transport=active_transport, store=provider_store,
                before_external_call=reserve,
                uncached_transport_guard=lambda _spec, _ids: _paid_account_guard(
                    dsn, project_id, video_id, stable_id,
                ),
            )
            with _account_cache_lock(dsn, stable_id):
                page = provider.fetch_account_profile(stable_id)
            if _accepted_account(dsn, project_id, video_id) != stable_id:
                raise PermissionError("project account identity changed")
            _, was_inserted = run_store.ingest_verified_account_profile(
                page.items[0], endpoint_key=page.endpoint_key,
                project_id=project_id, video_platform_id=video_id,
            )
            inserted += int(was_inserted)
            cached += int(page.cached)
        estimated, basis, unknown = _cost_summary(provider_store.calls)
        run_store.finish_run(
            run_id, status="success", input_count=len(ids),
            output_count=len(account_to_video), api_cost=estimated,
            cost_currency="USD", summary={
                "api_cost_basis": basis,
                "unknown_cost_calls": unknown,
                "snapshots_inserted": inserted,
                "cached_calls": cached,
            },
        )
        return AccountProfileRefreshResult(
            run_id=run_id, requested_video_count=len(ids),
            distinct_account_count=len(account_to_video),
            snapshots_inserted=inserted,
            external_calls=sum(not call.cached for call in provider_store.calls),
            cached_calls=cached, estimated_api_cost_usd=estimated,
        )
    except Exception as exc:
        estimated, basis, unknown = _cost_summary(provider_store.calls)
        run_store.finish_run(
            run_id, status="failed", input_count=len(ids),
            output_count=inserted, api_cost=estimated,
            cost_currency="USD", summary={
                "api_cost_basis": basis if unknown == 0 else "unknown",
                "known_estimated_cost_usd": sum(
                    float(call.estimated_cost or 0) for call in provider_store.calls
                    if not call.cached and call.status == "success"
                    and call.estimated_cost is not None
                ),
                "unknown_cost_calls": unknown,
                "error_type": type(exc).__name__,
            },
        )
        raise RuntimeError("account profile refresh failed") from None
    finally:
        if owned_transport:
            active_transport.close()
