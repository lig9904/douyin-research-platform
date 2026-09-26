# py: ==3.14.*
#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@94b8eac0107608e48485ae7dbb8f881229e150a1
#wmill>=1.815.0

"""Manual, bounded Windmill entry point for paid TikHub comment collection."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import re
from typing import Any, Callable, Iterator, TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1 import (
    CommentCollector,
    CommentEvidenceStore,
    DailyBudgetGuard,
    L0L1Store,
)
from douyin_research.l2 import CommentFeatureExtractor
from douyin_research.providers.errors import ProviderError, ProviderRateLimitError
from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TikHubTransport


CONFIRMATION = "COLLECT_COMMENTS_PAID"
REBUILD_CONFIRMATION = "REBUILD_COMMENT_FEATURES"
BUDGET_KEY = "windmill_manual_comments"
PROVIDER = "tikhub"
MAX_COUNT = 20
MAX_PAGES = 10
MAX_ITEMS = 200
COMMENT_PAGE_COST_USD = 0.001
LOCK_NAME = "douyin_research:manual_comment_collection"
_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


@dataclass(frozen=True, slots=True)
class ManualRequest:
    video_platform_id: str
    project_id: str = ""
    execute: bool = False
    confirmation: str = ""
    count: int = 20
    max_pages: int = 1
    max_items: int = 20
    rebuild_features_only: bool = False
    cursor: str = "0"


@dataclass(frozen=True, slots=True)
class _CollectionOutcome:
    collection: Any
    feature_status: str
    feature_error_type: str | None = None


def _validate(request: ManualRequest) -> None:
    if not request.video_platform_id.strip():
        raise ValueError("video_platform_id is required")
    if not 1 <= request.count <= MAX_COUNT:
        raise ValueError(f"count must be between 1 and {MAX_COUNT}")
    if not 1 <= request.max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
    if not 1 <= request.max_items <= MAX_ITEMS:
        raise ValueError(f"max_items must be between 1 and {MAX_ITEMS}")
    if request.max_items > request.count * request.max_pages:
        raise ValueError("max_items cannot exceed count * max_pages")
    if request.max_items % request.count:
        raise ValueError("max_items must be a multiple of count for safe pagination")
    if not isinstance(request.cursor, str) or not request.cursor.isdecimal() or len(request.cursor) > 40:
        raise ValueError("cursor must be a decimal pagination token")
    if not isinstance(request.project_id, str):
        raise ValueError("project_id is invalid")
    if request.execute and not request.project_id:
        raise ValueError("project_id is required for execution")
    if request.project_id:
        try:
            UUID(request.project_id)
        except ValueError:
            raise ValueError("project_id is invalid") from None


def _execute(
    request: ManualRequest,
    *,
    preflight: Callable[[str], None],
    load_api_key: Callable[[], str],
    collect: Callable[[str, ManualRequest], Any],
    rebuild_features: Callable[[ManualRequest], Any] | None = None,
) -> dict[str, Any]:
    """Run pure guards first; return only aggregate, non-identifying fields."""

    _validate(request)
    plan = {
        "status": "preview",
        "execute": False,
        "count": request.count,
        "max_pages": request.max_pages,
        "max_items": request.max_items,
        "maximum_external_calls": 0 if request.rebuild_features_only else request.max_pages,
        "maximum_estimated_cost_usd": 0 if request.rebuild_features_only else round(
            request.max_pages * COMMENT_PAGE_COST_USD, 6
        ),
        "sdk_retries": 0,
        "llm_calls": 0,
        "rebuild_features_only": request.rebuild_features_only,
        "cursor": request.cursor,
    }
    if not request.execute:
        return plan
    if request.rebuild_features_only:
        if request.confirmation != REBUILD_CONFIRMATION:
            raise PermissionError("exact feature-rebuild confirmation is required")
        preflight(request.video_platform_id)
        if rebuild_features is None:
            raise RuntimeError("feature rebuild is unavailable")
        snapshot = rebuild_features(request)
        return {
            "status": "features_rebuilt",
            "execute": True,
            "sampled_comment_count": int(snapshot.sampled_comment_count),
            "feature_status": "ready",
            "external_pages": 0,
            "estimated_api_cost_usd": 0,
            "llm_calls": 0,
        }
    if request.confirmation != CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")

    # Database prerequisites are checked before the secret is resolved.
    preflight(request.video_platform_id)
    api_key = (load_api_key() or "").strip()
    if not api_key:
        raise RuntimeError("TikHub secret is not configured")

    outcome = collect(api_key, request)
    summary = outcome.collection if isinstance(outcome, _CollectionOutcome) else outcome
    feature_status = outcome.feature_status if isinstance(outcome, _CollectionOutcome) else "ready"
    feature_error_type = outcome.feature_error_type if isinstance(outcome, _CollectionOutcome) else None
    return {
        "status": "completed" if feature_status == "ready" else "collected_feature_pending",
        "execute": True,
        "feature_status": feature_status,
        "feature_error_type": feature_error_type,
        "next_cursor": getattr(summary, "next_cursor", None),
        "has_more": bool(getattr(summary, "has_more", False)),
        "comments_returned": int(summary.comments_returned),
        "new_comments": int(summary.new_comments),
        "observations_inserted": int(summary.observations_inserted),
        "duplicate_observations": int(summary.duplicate_observations),
        "pages_fetched": int(summary.pages_fetched),
        "cached_pages": int(summary.cached_pages),
        "external_pages": int(summary.external_pages),
        "cross_page_duplicates_removed": int(
            summary.cross_page_duplicates_removed
        ),
        "estimated_api_cost_usd": float(summary.estimated_api_cost_usd),
        "cached": bool(summary.cached),
        "sdk_retries": 0,
        "llm_calls": 0,
    }


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _preflight(dsn: str, video_platform_id: str, project_id: str, *, require_budget: bool = True) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id from source_video
            where platform='douyin' and platform_video_id=%s
            """,
            (video_platform_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("source video must exist before comment collection")
        actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
        if len(actor) > 254 or not _EMAIL.fullmatch(actor):
            raise PermissionError("project member identity is required")
        cur.execute(
                """select 1 from research_project project
                   join research_organization organization on organization.id=project.organization_id
                   join project_video_inclusion inclusion on inclusion.project_id=project.id
                   where project.id=%s and project.status='active' and organization.status='active'
                     and inclusion.video_id=%s and inclusion.status='accepted'
                     and project_actor_can_read(project.id,%s)
                     and exists (
                       select 1 from research_project_member member
                       where member.project_id=project.id and member.actor_id=%s
                         and member.role in ('owner','admin','researcher')
                         and member.status='active' and member.effective_from <= now()
                         and (member.effective_until is null or member.effective_until > now())
                         and not exists (
                           select 1 from research_project_member newer
                           where newer.project_id=member.project_id and newer.actor_id=member.actor_id
                             and newer.effective_from <= now()
                             and newer.effective_from > member.effective_from)
                     )""",
            (UUID(project_id), row[0], actor, actor),
        )
        if cur.fetchone() is None:
            raise PermissionError("accepted project video and active researcher access are required")

        if require_budget:
            cur.execute(
                """
                select max_requests, max_cost
                from daily_budget
                where budget_date=current_date
                  and provider=%s
                  and budget_key=%s
                """,
                (PROVIDER, BUDGET_KEY),
            )
            if cur.fetchone() is None:
                raise RuntimeError("daily accounting row must be configured before execution")


@contextmanager
def _single_paid_job(dsn: str) -> Iterator[None]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        if not cur.fetchone()[0]:
            raise RuntimeError("another manual comment collection is already running")
        try:
            yield
        finally:
            cur.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def _load_api_key() -> str:
    # Delayed import and lookup: preview/invalid/preflight-failed runs never read the secret.
    import wmill

    return wmill.get_variable("f/content_research/tikhub_api_key")


class _RedactingTransport:
    """Keep upstream exception payloads out of DB metadata and Windmill logs."""

    def __init__(self, transport: TikHubTransport) -> None:
        self.transport = transport

    @staticmethod
    def _safe_failure(exc: Exception, mapped: Exception) -> Exception:
        # Preserve only attempt count and status for the billing ledger.  Do
        # not copy SDK text, response bodies, URLs or request headers.
        attempts = getattr(exc, "provider_attempts", None)
        if isinstance(attempts, (tuple, list)) and len(attempts) <= 20:
            safe = []
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    break
                status = attempt.get("http_status")
                safe.append({"http_status": status if isinstance(status, int) and 100 <= status <= 599 else None})
            else:
                mapped.provider_attempts = tuple(safe)
        return mapped

    def call(self, spec, kwargs):
        try:
            return self.transport.call(spec, kwargs)
        except ProviderRateLimitError as exc:
            raise self._safe_failure(exc, ProviderRateLimitError(
                "TikHub request was rate limited",
                retry_after=exc.retry_after,
            )) from None
        except ProviderError as exc:
            raise self._safe_failure(exc, type(exc)("TikHub request failed")) from None
        except Exception as exc:
            raise self._safe_failure(exc, RuntimeError("TikHub request failed")) from None


def _rebuild_features(dsn: str, video_platform_id: str):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select id from source_video where platform='douyin' and platform_video_id=%s",
            (video_platform_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("source video must exist before feature rebuild")
        video_id = row[0]
    return CommentFeatureExtractor(dsn).extract(video_id)


def _collect(dsn: str, api_key: str, request: ManualRequest) -> _CollectionOutcome:
    transport = TikHubTransport(api_key, max_retries=0)
    try:
        guard = DailyBudgetGuard(dsn)
        reserve = guard.make_before_external_call(
            provider=PROVIDER,
            budget_key=BUDGET_KEY,
        )

        def before_external_call(spec) -> None:
            # A project may be revoked after the initial preview/preflight.
            # Recheck the accepted inclusion and actor before every paid page.
            if transport.prefer_sdk or transport.max_retries != 0:
                raise RuntimeError("paid comments require single-attempt REST transport")
            _preflight(dsn, request.video_platform_id, request.project_id)
            reserve(spec)

        provider = TikHubProvider(
            transport=_RedactingTransport(transport),
            store=PostgresProviderStore(dsn),
            before_external_call=before_external_call,
        )
        collector = CommentCollector(
            provider=provider,
            evidence_store=CommentEvidenceStore(dsn),
            run_store=L0L1Store(dsn),
        )
        summary = collector.collect(
            request.video_platform_id,
            count=request.count,
            max_pages=request.max_pages,
            max_items=request.max_items,
            sample_reason="top",
            triggered_by="windmill_manual",
            project_id=UUID(request.project_id) if request.project_id else None,
            cursor=request.cursor,
        )
        if not summary.comments_returned:
            return _CollectionOutcome(summary, "no_comment_evidence")
        try:
            _rebuild_features(dsn, request.video_platform_id)
        except Exception as exc:
            # The paid collection and its ledger are already committed.  Never
            # turn an L2 failure into a paid retry instruction; the no-cost
            # rebuild mode can replay the stored evidence independently.
            return _CollectionOutcome(summary, "pending", type(exc).__name__)
        return _CollectionOutcome(summary, "ready")
    finally:
        transport.close()


def main(
    db: postgresql,
    video_platform_id: str,
    project_id: str = "",
    execute: bool = False,
    confirmation: str = "",
    count: int = 20,
    max_pages: int = 1,
    max_items: int = 20,
    rebuild_features_only: bool = False,
    cursor: str = "0",
):
    request = ManualRequest(
        video_platform_id=video_platform_id,
        project_id=project_id,
        execute=execute,
        confirmation=confirmation,
        count=count,
        max_pages=max_pages,
        max_items=max_items,
        rebuild_features_only=rebuild_features_only,
        cursor=cursor,
    )
    dsn = _dsn(db)

    if not execute:
        return _execute(
            request,
            preflight=lambda _: None,
            load_api_key=lambda: "",
            collect=lambda _key, _request: None,
        )

    with _single_paid_job(dsn):
        return _execute(
            request,
            preflight=lambda video_id: _preflight(
                dsn, video_id, request.project_id,
                require_budget=not request.rebuild_features_only,
            ),
            load_api_key=_load_api_key,
            collect=lambda key, bounded: _collect(dsn, key, bounded),
            rebuild_features=lambda bounded: _rebuild_features(dsn, bounded.video_platform_id),
        )
