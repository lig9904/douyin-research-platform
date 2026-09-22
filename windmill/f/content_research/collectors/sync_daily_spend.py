# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@ce8ae1c4c3358e0064daee45a0dd35540025a6e6",
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///

"""Hourly, free TikHub daily-spend snapshot synchronization for Windmill."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, TypedDict

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.providers.daily_spend import (
    DailySpendError,
    sync_tikhub_daily_spend,
)


API_KEY_PATH = "f/content_research/tikhub_api_key"
LOCK_NAME = "douyin_research:sync_tikhub_daily_spend"
REQUIRED_DAILY_SPEND_COLUMNS = {
    "bill_scope_key",
    "scope_kind",
    "scope_label",
    "billing_finality",
}


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _windmill_variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


def _require_scoped_schema(columns: set[str]) -> None:
    if not REQUIRED_DAILY_SPEND_COLUMNS.issubset(columns):
        raise RuntimeError("supplier daily spend scope migration is not ready")


def _preflight(dsn: str) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select to_regclass('supplier_daily_spend')")
        if cur.fetchone()[0] is None:
            raise RuntimeError("supplier daily spend schema is not ready")
        cur.execute(
            "select attname from pg_attribute "
            "where attrelid = to_regclass('supplier_daily_spend') "
            "and attnum > 0 and not attisdropped"
        )
        columns = {row[0] for row in cur.fetchall()}
        # Refuse before calling the supplier when migration 019 is not yet active.
        _require_scoped_schema(columns)


@contextmanager
def _single_sync(dsn: str) -> Iterator[None]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        if not cur.fetchone()[0]:
            raise RuntimeError("another daily spend synchronization is already running")
        try:
            yield
        finally:
            cur.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def main(db: postgresql, account_scope: str = "default") -> dict[str, object]:
    """Fetch one free supplier aggregate and return safe dashboard metadata."""

    dsn = _dsn(db)
    _preflight(dsn)
    api_key = (_windmill_variable(API_KEY_PATH) or "").strip()
    if not api_key:
        raise RuntimeError("TikHub secret is not configured")
    try:
        with _single_sync(dsn):
            snapshot, wrote = sync_tikhub_daily_spend(
                dsn, api_key, account_scope=account_scope, timeout_seconds=15.0
            )
    except DailySpendError:
        # Keep upstream response details and credentials out of Windmill logs.
        raise RuntimeError("TikHub daily spend synchronization failed") from None
    return {
        "status": "completed",
        "provider": snapshot.provider,
        "account_scope": snapshot.account_scope,
        "bill_scope_key": snapshot.bill_scope_key,
        "scope_kind": snapshot.scope_kind,
        "scope_label": snapshot.scope_label,
        "billing_date": snapshot.billing_date.isoformat(),
        "cost_currency": snapshot.cost_currency,
        "total_cost": str(snapshot.total_cost),
        "total_requests": snapshot.total_requests,
        "paid_requests": snapshot.paid_requests,
        "billing_finality": snapshot.billing_finality,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "snapshot_written": wrote,
        "raw_provider_payload_included": False,
    }
