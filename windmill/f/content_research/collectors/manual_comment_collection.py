#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@f97b81cbb49bfca6e2e476627021d1a21208347e
#wmill>=1.815.0

"""Manual, bounded Windmill entry point for paid TikHub comment collection."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypedDict

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1 import (
    CommentCollector,
    CommentEvidenceStore,
    DailyBudgetGuard,
    L0L1Store,
)
from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TikHubTransport


CONFIRMATION = "COLLECT_COMMENTS_PAID"
BUDGET_KEY = "windmill_manual_comments"
PROVIDER = "tikhub"
MAX_COUNT = 20
MAX_PAGES = 2
MAX_ITEMS = 40
COMMENT_PAGE_COST_USD = 0.001
LOCK_NAME = "douyin_research:manual_comment_collection"


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
    execute: bool = False
    confirmation: str = ""
    count: int = 20
    max_pages: int = 1
    max_items: int = 20


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


def _execute(
    request: ManualRequest,
    *,
    preflight: Callable[[str], None],
    load_api_key: Callable[[], str],
    collect: Callable[[str, ManualRequest], Any],
) -> dict[str, Any]:
    """Run pure guards first; return only aggregate, non-identifying fields."""

    _validate(request)
    plan = {
        "status": "preview",
        "execute": False,
        "count": request.count,
        "max_pages": request.max_pages,
        "max_items": request.max_items,
        "maximum_external_calls": request.max_pages,
        "maximum_estimated_cost_usd": round(
            request.max_pages * COMMENT_PAGE_COST_USD, 6
        ),
        "sdk_retries": 0,
        "llm_calls": 0,
    }
    if not request.execute:
        return plan
    if request.confirmation != CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")

    # Database prerequisites are checked before the secret is resolved.
    preflight(request.video_platform_id)
    api_key = (load_api_key() or "").strip()
    if not api_key:
        raise RuntimeError("TikHub secret is not configured")

    summary = collect(api_key, request)
    return {
        "status": "completed",
        "execute": True,
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


def _preflight(dsn: str, video_platform_id: str) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select exists(
              select 1 from source_video
              where platform='douyin' and platform_video_id=%s
            )
            """,
            (video_platform_id,),
        )
        if not cur.fetchone()[0]:
            raise RuntimeError("source video must exist before comment collection")

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
        budget = cur.fetchone()
        if budget is None or (budget[0] is None and budget[1] is None):
            raise RuntimeError("daily budget must be configured before execution")


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


def _collect(dsn: str, api_key: str, request: ManualRequest):
    transport = TikHubTransport(api_key, max_retries=0)
    try:
        guard = DailyBudgetGuard(dsn)
        provider = TikHubProvider(
            transport=transport,
            store=PostgresProviderStore(dsn),
            before_external_call=guard.make_before_external_call(
                provider=PROVIDER,
                budget_key=BUDGET_KEY,
            ),
        )
        collector = CommentCollector(
            provider=provider,
            evidence_store=CommentEvidenceStore(dsn),
            run_store=L0L1Store(dsn),
        )
        return collector.collect(
            request.video_platform_id,
            count=request.count,
            max_pages=request.max_pages,
            max_items=request.max_items,
            sample_reason="top",
            triggered_by="windmill_manual",
        )
    finally:
        transport.close()


def main(
    db: postgresql,
    video_platform_id: str,
    execute: bool = False,
    confirmation: str = "",
    count: int = 20,
    max_pages: int = 1,
    max_items: int = 20,
):
    request = ManualRequest(
        video_platform_id=video_platform_id,
        execute=execute,
        confirmation=confirmation,
        count=count,
        max_pages=max_pages,
        max_items=max_items,
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
            preflight=lambda video_id: _preflight(dsn, video_id),
            load_api_key=_load_api_key,
            collect=lambda key, bounded: _collect(dsn, key, bounded),
        )
